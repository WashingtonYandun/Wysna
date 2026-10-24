"""Source-command orchestration over CompilerService and NativeToolchain."""
import json
import os
import sys
from typing import Optional
from lexer.Lexer import Lexer
from runtime.ExecutionContext import ExecutionContext
from runtime.Values import WysnaUnit
from formatter.Formatter import Formatter
from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeCompilationError, NativeToolchain
from error.Error import WysnaError
from tooling.ToolResult import (
    artifact_file,
    artifact_inline,
    diagnostic_from_error,
    diagnostic_record,
    tool_result,
    write_tool_result,
)
from common.Platform import peak_rss_kb
from common.Paths import get_repo_root
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tooling.RunReport import _ApplicationExit, _set_run_report


def _bounded_tool_output(value: str, limit: int = 8192) -> str:
    """Keep subprocess diagnostics useful without embedding unbounded output."""
    if len(value) <= limit:
        return value
    suffix = "\n[tool output truncated]"
    return value[:limit - len(suffix)] + suffix



def _write_json_v1(command, status, exit_code, *, errors=(), source_by_file=None,
                   extra_diagnostics=(), artifacts=(), target=None):
    diagnostics = list(extra_diagnostics)
    span_cache = {}
    for error in errors:
        nested = getattr(error, "diagnostics", (error,))
        diagnostics.extend(diagnostic_from_error(item, source_by_file, span_cache=span_cache)
                           for item in nested)
    write_tool_result(
        tool_result(command, status, exit_code, diagnostics=diagnostics,
                    artifacts=artifacts, target=target),
        sys.stdout,
    )



def _sources_for_diagnostics(file_name, source_code, service=None):
    sources = {
        file_name: source_code,
        os.path.abspath(file_name): source_code,
    }
    if service is not None:
        sources.update(service.source_texts())
    return sources



def run_pipeline(
    source_code: str,
    file_name: str = "<stdin>",
    check_only: bool = False,
    show_tokens: bool = False,
    show_ast: bool = False,
    json_diagnostics: bool = False,
    fmt_only: bool = False,
    fmt_write: bool = False,
    emit_c_path: Optional[str] = None,
    build_bin_path: Optional[str] = None,
    backend: str = "hir",
    use_selfhost: bool = False,
    use_hir: Optional[bool] = None,
    sanitize: bool = False,
    show_stats: bool = False,
    link_libraries=(),
    show_mir: bool = False,
    execution_context: Optional[ExecutionContext] = None,
    json_v1: bool = False,
    tool_command: Optional[str] = None,
    report_state=None,
    project_root=None,
    project_resolver=None,
) -> int:
    if tool_command is None:
        if check_only:
            tool_command = "check"
        elif fmt_only:
            tool_command = "fmt"
        elif emit_c_path is not None:
            tool_command = "emit-c"
        elif build_bin_path is not None:
            tool_command = "build"
        else:
            tool_command = "run"
    try:
        # Resolve legacy flags if passed explicitly
        if use_selfhost:
            backend = "selfhost"
        elif use_hir is False:
            backend = "ast"
        elif use_hir is True:
            backend = "hir"

        import time

        t_start = time.perf_counter()
        lex_ms = parse_ms = check_ms = 0.0
        ast = None
        checked = None
        selfhost_direct = (
            backend == "selfhost"
            and (emit_c_path is not None or build_bin_path is not None)
            and file_name != "<stdin>"
            and not show_tokens
            and not show_ast
            and not fmt_only
            and not check_only
        )

        if show_tokens:
            tokens = Lexer(source_code, file=file_name).tokenize()
            for t in tokens:
                print(f"  {t}")
            if not show_ast and not check_only and emit_c_path is None and build_bin_path is None:
                return 0

        if not selfhost_direct:
            service = CompilerService(project_root=project_root,
                                      project_resolver=project_resolver)
            parsed = service.parse(source_code, file_name, recover=check_only)
            ast = parsed.program if show_ast or fmt_only else None
            lex_ms, parse_ms = parsed.timing_ms['lex'], parsed.timing_ms['parse']
        if show_ast:
            print(json.dumps(ast.to_dict(), indent=2))
            if not check_only and emit_c_path is None and build_bin_path is None:
                return 0

        if fmt_only:
            formatted = Formatter().format(ast)
            if fmt_write and file_name != "<stdin>":
                with open(file_name, "w", encoding="utf-8") as f:
                    f.write(formatted)
                if json_v1:
                    artifact = artifact_file("formatted_source", "text/x-wysna", file_name)
            else:
                if json_v1:
                    artifact = artifact_inline("formatted_source", "text/x-wysna", formatted)
                else:
                    print(formatted, end="")
            if json_v1:
                _write_json_v1(
                    tool_command,
                    "ok",
                    0,
                    artifacts=[artifact],
                    target={"profile": "wysna-source", "triple": None},
                )
            return 0

        if not selfhost_direct:
            # 3. Static Type Checking
            t_check0 = time.perf_counter()
            checked = service.check_parsed(parsed)
            check_ms = (time.perf_counter() - t_check0) * 1000.0

        if show_mir:
            from mir.Nodes import dump
            print(dump(service.lower_mir(checked)), end="")
            return 0

        if check_only:
            if json_v1:
                _write_json_v1(tool_command, "ok", 0)
                return 0
            if show_stats:
                stat_obj = {
                    "status": "ok",
                    "file": file_name,
                    "telemetry": {
                        "monomorphization": dict(checked.telemetry),
                        "timing_ms": {
                            "lex": round(lex_ms, 2),
                            "parse": round(parse_ms, 2),
                            "check": round(check_ms, 2),
                            "total": round((time.perf_counter() - t_start) * 1000.0, 2),
                        },
                        "peak_rss_kb": peak_rss_kb(),
                    }
                }
                print(json.dumps(stat_obj, indent=2))
            elif json_diagnostics:
                print(json.dumps({"status": "ok", "file": file_name, "diagnostics": []}, indent=2))
            else:
                print(f"[OK] Check passed for {file_name}")
            return 0

        # 4. C99 Code Generation / Native Build
        if emit_c_path is not None or build_bin_path is not None:
            repo_root = get_repo_root()
            toolchain = NativeToolchain(repo_root)
            codegen_telemetry = None
            phase_ms = {}
            hir_ms = None
            if backend == "selfhost" and file_name != "<stdin>":
                selfhost_bin = ensure_selfhost_compiler_binary(repo_root)
                emission = toolchain.emit_selfhost(selfhost_bin, file_name)
                c_code, codegen_ms = emission.source, emission.codegen_ms
            else:
                emission = service.emit_c(checked, backend="ast" if backend == "ast" else "hir")
                c_code = emission.source
                codegen_telemetry = dict(emission.telemetry)
                phase_ms = emission.timing_ms if backend != "ast" else {}
                hir_ms = phase_ms.get("hir")
                codegen_ms = emission.timing_ms["codegen"]

            c_compile_ms = link_ms = 0.0
            def report_codegen_stats():
                if not show_stats:
                    return
                stat_obj = {
                    "status": "ok",
                    "file": file_name,
                    "telemetry": {
                        "monomorphization": dict(checked.telemetry) if checked is not None else None,
                        "codegen": codegen_telemetry,
                        "timing_ms": {
                            "lex": round(lex_ms, 2),
                            "parse": round(parse_ms, 2),
                            "check": round(check_ms, 2),
                            "hir": round(hir_ms, 2) if hir_ms is not None else None,
                            "codegen": round(codegen_ms, 2),
                            "mir": round(phase_ms.get('mir', 0), 2),
                            "c_emit": round(phase_ms.get('c_emit', 0), 2),
                            "c_compile": round(c_compile_ms, 2),
                            "link": round(link_ms, 2),
                            "total": round((time.perf_counter() - t_start) * 1000.0, 2),
                        },
                        "peak_rss_kb": peak_rss_kb(),
                    }
                }
                print(json.dumps(stat_obj, indent=2), file=sys.stderr)

            if emit_c_path is not None:
                with open(emit_c_path, "w", encoding="utf-8") as f:
                    f.write(c_code)
                if build_bin_path is None:
                    if json_v1:
                        artifact = artifact_file("c_source", "text/x-c", emit_c_path)
                        _write_json_v1(
                            tool_command,
                            "ok",
                            0,
                            artifacts=[artifact],
                            target={"profile": "c99-source", "triple": None},
                        )
                    report_codegen_stats()
                    return 0

            if build_bin_path is not None:
                built = toolchain.build(c_code, build_bin_path, sanitize=sanitize,
                                        link_libraries=link_libraries)
                c_compile_ms, link_ms = built.c_compile_ms, built.link_ms
                if json_v1:
                    artifact = artifact_file(
                        "native_executable", "application/octet-stream", built.output_path,
                    )
                    _write_json_v1(
                        tool_command,
                        "ok",
                        0,
                        artifacts=[artifact],
                        target={"profile": "native-cli", "triple": None},
                    )
                report_codegen_stats()
                return 0

        # 5. Runtime Interpretation
        if show_stats:
            print(json.dumps({
                "status": "ok",
                "file": file_name,
                "telemetry": {
                    "monomorphization": dict(checked.telemetry),
                    "timing_ms": {
                        "lex": round(lex_ms, 2),
                        "parse": round(parse_ms, 2),
                        "check": round(check_ms, 2),
                        "total": round((time.perf_counter() - t_start) * 1000.0, 2),
                    },
                    "peak_rss_kb": peak_rss_kb(),
                }
            }, indent=2), file=sys.stderr)
        res = service.interpret(checked, foreign_libraries=link_libraries,
                                execution_context=execution_context)

        if not isinstance(res, WysnaUnit) and file_name == "<stdin>":
            print(res)

        if report_state is not None:
            _set_run_report(
                report_state,
                "ok",
                0,
                0,
                execution={"kind": "application_exit", "exit_code": 0, "signal": None},
            )

        return 0

    except _ApplicationExit as termination:
        code = termination.code
        if report_state is None:
            raise SystemExit(code)
        if not 0 <= code <= 255:
            diagnostic = diagnostic_record(
                "W5002", "execution",
                "Application exit code is outside the portable range 0..255.",
                filename=file_name,
            )
            _set_run_report(
                report_state,
                "execution_error",
                5,
                5,
                execution={
                    "kind": "application_exit",
                    "exit_code": code,
                    "signal": None,
                    "data": {"portable_range": [0, 255]},
                },
                diagnostics=[diagnostic],
            )
            return 5
        _set_run_report(
            report_state,
            "ok",
            0,
            code,
            execution={"kind": "application_exit", "exit_code": code, "signal": None},
        )
        return code

    except NativeCompilationError as err:
        if report_state is not None:
            diagnostic = diagnostic_record(
                "W5001", "toolchain",
                _bounded_tool_output(f"{err.phase} failed: {err.stderr}"),
                filename=file_name,
            )
            _set_run_report(report_state, "toolchain_error", 3, 3,
                            diagnostics=[diagnostic])
            print(f"[{err.label}]\n{err.stderr}", file=sys.stderr)
            return 3
        if json_v1:
            diagnostic = diagnostic_record(
                "W5001", "toolchain",
                _bounded_tool_output(f"{err.phase} failed: {err.stderr}"),
                filename=file_name,
            )
            _write_json_v1(tool_command, "toolchain_error", 3,
                           extra_diagnostics=[diagnostic])
            return 3
        print(f"[{err.label}]\n{err.stderr}", file=sys.stderr)
        return 1
    except WysnaError as err:
        if report_state is not None:
            span_cache = {}
            source_by_file = _sources_for_diagnostics(
                file_name, source_code, locals().get("service"))
            diagnostics = [
                diagnostic_from_error(item, source_by_file, span_cache=span_cache)
                for item in getattr(err, "diagnostics", (err,))
            ]
            _set_run_report(report_state, "diagnostic_error", 1, 1,
                            diagnostics=diagnostics)
            for diagnostic in getattr(err, "diagnostics", (err,)):
                print(str(diagnostic), file=sys.stderr)
            return 1
        if json_v1:
            _write_json_v1(
                tool_command, "diagnostic_error", 1, errors=[err],
                source_by_file=_sources_for_diagnostics(
                    file_name, source_code, locals().get("service")),
            )
            return 1
        if json_diagnostics:
            print(json.dumps(err.to_dict(), indent=2))
        else:
            for diagnostic in getattr(err, 'diagnostics', (err,)):
                print(str(diagnostic), file=sys.stderr)
        return 1
    except Exception as exc:
        if report_state is not None:
            if isinstance(exc, OSError):
                diagnostic = diagnostic_record(
                    "W5004", "filesystem", f"Unable to access a requested file: {exc}",
                    filename=file_name,
                )
                _set_run_report(report_state, "toolchain_error", 3, 3,
                                diagnostics=[diagnostic])
                print(f"[Filesystem Error] {exc}", file=sys.stderr)
                return 3
            diagnostic = diagnostic_record(
                "W9999", "internal", str(exc), filename=file_name,
            )
            _set_run_report(report_state, "internal_error", 4, 4,
                            diagnostics=[diagnostic])
            print(f"[Internal Error] {exc}", file=sys.stderr)
            return 4
        if json_v1:
            if isinstance(exc, OSError):
                diagnostic = diagnostic_record(
                    "W5004", "filesystem", f"Unable to write requested output: {exc}",
                    filename=file_name,
                )
                _write_json_v1(tool_command, "toolchain_error", 3,
                               extra_diagnostics=[diagnostic])
                return 3
            diagnostic = diagnostic_record(
                "W9999", "internal", str(exc), filename=file_name,
            )
            _write_json_v1(tool_command, "internal_error", 4,
                           extra_diagnostics=[diagnostic])
            return 4
        if json_diagnostics:
            err_dict = {
                "code": "W9999",
                "severity": "error",
                "stage": "internal",
                "message": str(exc),
                "file": file_name,
                "line": 1,
                "column": 1,
                "span": {
                    "file": file_name,
                    "line": 1,
                    "column": 1,
                },
            }
            print(json.dumps(err_dict, indent=2))
        else:
            print(f"[Internal Error] {exc}", file=sys.stderr)
        return 1

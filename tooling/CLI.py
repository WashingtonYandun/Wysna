"""Command-line parsing and presentation for the Wysna executable."""
import json
import os
import sys
from typing import Optional
from parser.Parser import Parser
from checker.TypeChecker import TypeChecker
from runtime.Interpreter import Interpreter
from runtime.ExecutionContext import ExecutionContext
from runtime.Values import WysnaUnit
from error.Error import WysnaError
from tooling.ToolResult import (
    diagnostic_record,
    tool_result,
    write_tool_result,
    write_tool_result_atomic,
)
from common.Version import WYSNA_VERSION
from common.Paths import get_repo_root
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tooling.SourceCommand import run_pipeline, _bounded_tool_output, _write_json_v1
from tooling.RunReport import _capture_application_exit, _set_run_report, _write_run_report


def _same_file_path(first, second):
    first = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(first))))
    second = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(second))))
    if first == second:
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False



def repl(show_tokens: bool = False, show_ast: bool = False) -> None:
    print("==================================================")
    print(f"Wysna REPL v{WYSNA_VERSION} (Type 'exit' to quit)")
    print("==================================================")

    parser = Parser(file="<repl>")
    checker = TypeChecker(file="<repl>")
    interp = Interpreter(file="<repl>")

    while True:
        try:
            line = input("wysna> ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        line = line.strip()
        if not line:
            continue
        if line == "exit":
            break

        try:
            ast = parser.parse(line)
            if show_ast:
                print(json.dumps(ast.to_dict(), indent=2))

            checker.check(ast)
            res = interp.interpret(ast)
            if not isinstance(res, WysnaUnit):
                print(res)
        except WysnaError as err:
            print(str(err))
        except Exception as exc:
            print(f"Error: {exc}")



def print_help(prog: Optional[str] = None) -> None:
    if prog is None:
        prog = "wysna"
    print(f"Wysna Language Compiler & Toolchain v{WYSNA_VERSION}")
    print("Usage:")
    print(f"  {prog} lsp                                       # LSP over stdio")
    print(f"  {prog} service serve --worker <bin> --database <db> # Local bounded HTTP/JSON pilot")
    print(f"  {prog} repl                                    # Start interactive REPL")
    print(f"  {prog} [run] [--stats] [--report-json <path>] [--invocation-id <id>] <file.wysna> [-- args...] # Execute Wysna source file")
    print(f"  {prog} check [--json|--output-format=json-v1] [--stats] <file.wysna>   # Static typecheck without execution")
    print(f"  {prog} fmt [--write] [--output-format=json-v1] <file.wysna> # Canonical deterministic formatter")
    print(f"  {prog} ast [--json] <file.wysna>               # Emit canonical JSON AST (v0.1)")
    print(f"  {prog} mir <file.wysna>                        # Emit verified MIR as JSON")
    print(f"  {prog} emit-mir [--implementation=seed|compiler] <file.wysna> [-o <out.json>] # Emit portable MIR")
    print(f"  {prog} tokens <file.wysna>                     # Emit lexical token stream")
    print(f"  {prog} emit-c [--backend=hir|ast|selfhost] [--output-format=json-v1] <file.wysna> [-o <out.c>] # Transpile to C99")
    print(f"  {prog} build [--backend=hir|ast|selfhost] [--output-format=json-v1] <file.wysna> [-o <bin>] # Compile native binary")
    print(f"  {prog} test [--differential [N]] [--fuzz [N]]            # Run conformance, differential, or fuzz suite")
    print(f"  {prog} bench [--update-baseline]                         # Run Seed/Compiler performance benchmark harness")
    print(f"  {prog} version                                           # Show compiler version")



def main(argv=None) -> None:
    prog = "wysna"
    args = list(sys.argv[1:] if argv is None else argv)
    cli_args = args[:args.index("--")] if "--" in args else args
    json_v1 = "--output-format=json-v1" in cli_args
    report_json_path = None
    requested_invocation_id = None
    command_for_output = args[0] if args and args[0] in (
        "run", "check", "fmt", "ast", "mir", "tokens", "emit-c", "build",
        "test", "bench", "version", "repl",
    ) else "run"

    def usage_error(message: str) -> None:
        if json_v1 and report_json_path is None:
            diagnostic = diagnostic_record("W5002", "usage", message)
            write_tool_result(
                tool_result(command_for_output, "usage_error", 2,
                            diagnostics=[diagnostic]),
                sys.stdout,
            )
            raise SystemExit(2)
        print(f"{prog}: {message}", file=sys.stderr)
        print(f"Try '{prog} --help' for usage.", file=sys.stderr)
        raise SystemExit(2)

    if args and args[0] == "project":
        from tooling.ProjectCLI import run_project_command
        raise SystemExit(run_project_command(args[1:]))
    if args and args[0] == "emit-mir":
        from tooling.MirCLI import run_mir_command
        raise SystemExit(run_mir_command(args[1:]))
    if args and args[0] == "service":
        from tooling.ServiceCLI import run_service_command
        raise SystemExit(run_service_command(args[1:]))
    if args and args[0] == "api":
        from tooling.ApiIndex import run_api_command
        raise SystemExit(run_api_command(args[1:]))
    if args and args[0] == "capabilities":
        from tooling.ApiIndex import run_capabilities_command
        raise SystemExit(run_capabilities_command(args[1:]))
    if args == ["lsp"]:
        from tooling.LanguageServer import serve
        serve()
        return
    show_tokens = False
    show_ast = False
    check_only = False
    json_diagnostics = False
    show_stats = False
    fmt_only = False
    fmt_write = False
    backend = "hir"
    use_selfhost = False
    use_hir = None
    emit_c_path: Optional[str] = None
    build_bin_path: Optional[str] = None
    show_mir = False
    subcmd: Optional[str] = None
    file_to_run: Optional[str] = None
    sanitize = False
    link_libraries = []

    if args and args[0] in ("run", "check", "fmt", "ast", "mir", "tokens", "emit-c", "build", "test", "bench", "version", "repl"):
        subcmd = args[0]
        command_for_output = subcmd
        args = args[1:]
        if subcmd == "bench":
            if any(arg not in ("--update-baseline", "--help", "-h") for arg in args):
                usage_error("bench accepts only --update-baseline")
            if args in (["--help"], ["-h"]):
                print_help()
                return
            if not os.path.isfile(os.path.join(get_repo_root(), "bench", "run_bench.py")):
                print(f"{prog} bench requires a source checkout", file=sys.stderr)
                sys.exit(1)
            update_baseline = "--update-baseline" in args
            repo_root = get_repo_root()
            selfhost_bin = ensure_selfhost_compiler_binary(repo_root)
            from bench.run_bench import run_benchmark_suite
            ok = run_benchmark_suite(repo_root, selfhost_bin, update_baseline=update_baseline)
            sys.exit(0 if ok else 1)
        elif subcmd == "test":
            if args in (["--help"], ["-h"]):
                print_help()
                return
            if not os.path.isdir(os.path.join(get_repo_root(), "tests", "unit")):
                print(f"{prog} test requires a source checkout", file=sys.stderr)
                sys.exit(1)
            if args and args[0] == "--differential":
                gen_count = 25
                base_seed = 1000
                idx = 1
                count_seen = False
                seed_seen = False
                while idx < len(args):
                    if args[idx] == "--seed" and not seed_seen and idx + 1 < len(args) and args[idx + 1].isdigit():
                        base_seed = int(args[idx + 1])
                        seed_seen = True
                        idx += 2
                    elif args[idx].isdigit() and not count_seen:
                        gen_count = int(args[idx])
                        count_seen = True
                        idx += 1
                    else:
                        usage_error(f"invalid test option '{args[idx]}'")
                repo_root = get_repo_root()
                selfhost_bin = ensure_selfhost_compiler_binary(repo_root)
                from tests.differential.differential_test import run_differential_suite
                ok = run_differential_suite(repo_root, selfhost_bin, generated_count=gen_count, base_seed=base_seed)
                sys.exit(0 if ok else 1)
            elif args and args[0] == "--fuzz":
                fuzz_count = 200
                base_seed = 9000
                idx = 1
                count_seen = False
                seed_seen = False
                while idx < len(args):
                    if args[idx] == "--seed" and not seed_seen and idx + 1 < len(args) and args[idx + 1].isdigit():
                        base_seed = int(args[idx + 1])
                        seed_seen = True
                        idx += 2
                    elif args[idx].isdigit() and not count_seen:
                        fuzz_count = int(args[idx])
                        count_seen = True
                        idx += 1
                    else:
                        usage_error(f"invalid test option '{args[idx]}'")
                repo_root = get_repo_root()
                from tests.differential.differential_test import run_fuzz_suite
                ok = run_fuzz_suite(fuzz_count=fuzz_count, base_seed=base_seed, repo_root=repo_root)
                sys.exit(0 if ok else 1)
            else:
                if any(arg not in ("--sanitize", "-s") for arg in args):
                    usage_error(f"invalid test option '{next(arg for arg in args if arg not in ('--sanitize', '-s'))}'")
                if args and ("--sanitize" in args or "-s" in args):
                    os.environ["WYSNA_SANITIZE"] = "1"
                from run_conformance import run_suite
                run_suite()
                return
        elif subcmd == "version":
            print(f"Wysna Language Compiler v{WYSNA_VERSION} (Wysna Seed + Wysna Compiler Self-Hosted C99)")
            return
        elif subcmd == "check":
            check_only = True
        elif subcmd == "fmt":
            fmt_only = True
        elif subcmd == "ast":
            show_ast = True
        elif subcmd == "mir":
            show_mir = True
        elif subcmd == "tokens":
            show_tokens = True

    program_args = []
    if "--" in args:
        separator = args.index("--")
        if subcmd not in (None, "run"):
            usage_error("program arguments after -- are supported only by run")
        if separator == 0:
            usage_error("a source file must appear before --")
        program_args = args[separator + 1:]
        args = args[:separator]

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--tokens", "-t"):
            show_tokens = True
        elif arg == "--ast":
            if subcmd == "ast":
                show_ast = True
            else:
                backend = "ast"
                use_hir = False
        elif arg in ("--check", "-c"):
            check_only = True
        elif arg == "--json":
            json_diagnostics = True
        elif arg == "--output-format=json-v1":
            json_v1 = True
        elif arg == "--report-json":
            if report_json_path is not None:
                usage_error("--report-json may be specified only once")
            i += 1
            if i >= len(args) or not args[i] or args[i].startswith("-"):
                usage_error("--report-json requires a file path")
            report_json_path = args[i]
        elif arg == "--invocation-id":
            if requested_invocation_id is not None:
                usage_error("--invocation-id may be specified only once")
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("--invocation-id requires a value")
            requested_invocation_id = args[i]
        elif arg in ("--stats", "--metrics"):
            show_stats = True
        elif arg == "--fmt":
            fmt_only = True
        elif arg in ("--write", "-w"):
            fmt_write = True
        elif arg in ("--selfhost", "--backend=selfhost"):
            backend = "selfhost"
            use_selfhost = True
        elif arg in ("--hir", "--backend=hir"):
            backend = "hir"
            use_hir = True
        elif arg in ("--backend=ast",):
            backend = "ast"
            use_hir = False
        elif arg.startswith("--backend="):
            b_val = arg.split("=", 1)[1]
            if b_val in ("hir", "ast", "selfhost"):
                backend = b_val
            else:
                usage_error(f"Unknown backend '{b_val}'. Valid backends: hir, ast, selfhost")
        elif arg in ("--version", "-v"):
            print(f"Wysna Language Compiler v{WYSNA_VERSION} (Wysna Seed + Wysna Compiler Self-Hosted C99)")
            sys.exit(0)
        elif arg == "--emit-c":
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("--emit-c requires an output path")
            emit_c_path = args[i]
        elif arg == "--build":
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("--build requires an output path")
            build_bin_path = args[i]
        elif arg == "-o":
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("-o requires an output path")
            if subcmd == "emit-c":
                emit_c_path = args[i]
            elif subcmd == "build":
                build_bin_path = args[i]
            else:
                build_bin_path = args[i]
        elif arg == "--link-lib":
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("--link-lib requires a library name or path")
            link_libraries.append(args[i])
        elif arg in ("--sanitize", "-s"):
            sanitize = True
        elif arg in ("--help", "-h"):
            if json_v1:
                usage_error("--help cannot be combined with --output-format=json-v1")
            print_help()
            sys.exit(0)
        elif arg.startswith("-"):
            usage_error(f"unrecognized option '{arg}'")
        else:
            if file_to_run is not None:
                usage_error("only one source file may be specified")
            file_to_run = arg
        i += 1

    prog_cmd = "wysna"

    if requested_invocation_id is not None and report_json_path is None:
        usage_error("--invocation-id requires --report-json")

    if report_json_path is not None:
        if subcmd not in (None, "run"):
            usage_error("--report-json is supported only by run")
        if json_v1 or json_diagnostics:
            usage_error("run --report-json cannot be combined with JSON output modes")
        if (show_tokens or show_ast or check_only or fmt_only or fmt_write
                or emit_c_path is not None or build_bin_path is not None or show_mir):
            usage_error("run --report-json supports program execution only")
        if requested_invocation_id == "":
            usage_error("--invocation-id must not be empty")

    if json_v1:
        supported_commands = ("check", "fmt", "emit-c", "build")
        if subcmd not in supported_commands:
            usage_error("--output-format=json-v1 is supported only by check, fmt, emit-c, and build")
        if json_diagnostics or show_stats or show_ast or show_tokens or show_mir:
            usage_error("--output-format=json-v1 cannot be combined with legacy JSON, stats, or diagnostic output modes")
        if subcmd == "check":
            if (not check_only or fmt_only or fmt_write or emit_c_path is not None
                    or build_bin_path is not None):
                usage_error("check --output-format=json-v1 supports type checking only")
            if sanitize or link_libraries:
                usage_error("check --output-format=json-v1 does not accept build options")
        elif subcmd == "fmt":
            if not fmt_only or check_only or emit_c_path is not None or build_bin_path is not None:
                usage_error("fmt --output-format=json-v1 supports formatting only")
            if sanitize or link_libraries or backend != "hir":
                usage_error("fmt --output-format=json-v1 does not accept backend or build options")
        elif subcmd == "emit-c":
            if check_only or fmt_only or fmt_write or build_bin_path is not None:
                usage_error("emit-c --output-format=json-v1 supports C generation only")
            if sanitize or link_libraries:
                usage_error("emit-c --output-format=json-v1 does not accept build options")
        elif subcmd == "build":
            if check_only or fmt_only or fmt_write or emit_c_path is not None:
                usage_error("build --output-format=json-v1 supports native compilation only")

    if file_to_run:
        if file_to_run.endswith(".w"):
            if json_v1:
                usage_error("'.w' files are no longer supported; rename the source to use the .wysna extension")
            print(f"error: '.w' files are no longer supported. Rename '{file_to_run}' to '{file_to_run[:-2]}.wysna'", file=sys.stderr)
            sys.exit(1)

        # Resolve file with extension fallback if omitted
        if not os.path.isfile(file_to_run) and os.path.isfile(file_to_run + ".wysna"):
            file_to_run = file_to_run + ".wysna"

        def _strip_src_ext(p: str) -> str:
            if p.endswith(".wysna"):
                return p[:-6]
            return p

        if subcmd == "emit-c" and emit_c_path is None:
            emit_c_path = _strip_src_ext(file_to_run) + ".c"
        elif subcmd == "build" and build_bin_path is None:
            build_bin_path = _strip_src_ext(file_to_run) if file_to_run.endswith(".wysna") else file_to_run + ".bin"

        report_state = None
        invocation_id = requested_invocation_id
        if report_json_path is not None:
            if _same_file_path(report_json_path, file_to_run):
                usage_error("the report path must not overwrite the source file")
            pending = tool_result(
                "run",
                "pending",
                None,
                process_exit_code=None,
                invocation_id=invocation_id,
            )
            invocation_id = pending["invocation_id"]
            try:
                write_tool_result_atomic(pending, report_json_path)
            except OSError as error:
                print(
                    f"{prog}: unable to initialize JSON report '{report_json_path}': {error}",
                    file=sys.stderr,
                )
                raise SystemExit(3)
            report_state = {
                "status": "pending",
                "tool_exit_code": None,
                "process_exit_code": None,
                "execution": None,
                "diagnostics": [],
            }

        if json_v1 and subcmd in ("emit-c", "build"):
            output_path = emit_c_path if subcmd == "emit-c" else build_bin_path
            canonical_output = os.path.normcase(os.path.realpath(output_path))
            canonical_source = os.path.normcase(os.path.realpath(file_to_run))
            try:
                same_file = os.path.samefile(output_path, file_to_run)
            except OSError:
                same_file = False
            if canonical_output == canonical_source or same_file:
                usage_error("the output path must not overwrite the source file")

        try:
            with open(file_to_run, "r", encoding="utf-8",
                      newline="" if json_v1 or report_state is not None else None) as f:
                source = f.read()
        except (OSError, UnicodeError) as e:
            if report_state is not None:
                diagnostic = diagnostic_record(
                    "W5003", "filesystem",
                    f"Unable to read source file '{file_to_run}': {e}",
                    filename=file_to_run,
                )
                _set_run_report(report_state, "toolchain_error", 3, 3,
                                diagnostics=[diagnostic])
                print(f"Error reading file '{file_to_run}': {e}", file=sys.stderr)
                try:
                    _write_run_report(report_json_path, invocation_id, report_state)
                except OSError as report_error:
                    print(f"{prog}: unable to finalize JSON report: {report_error}", file=sys.stderr)
                raise SystemExit(3)
            if json_v1:
                diagnostic = diagnostic_record(
                    "W5003", "filesystem", f"Unable to read source file '{file_to_run}': {e}",
                    filename=file_to_run,
                )
                write_tool_result(
                    tool_result(command_for_output, "toolchain_error", 3,
                                diagnostics=[diagnostic]),
                    sys.stdout,
                )
                sys.exit(3)
            print(f"Error reading file '{file_to_run}': {e}", file=sys.stderr)
            sys.exit(1)

        code = run_pipeline(
            source_code=source,
            file_name=file_to_run,
            check_only=check_only,
            show_tokens=show_tokens,
            show_ast=show_ast,
            show_mir=show_mir,
            json_diagnostics=json_diagnostics,
            fmt_only=fmt_only,
            fmt_write=fmt_write,
            emit_c_path=emit_c_path,
            build_bin_path=build_bin_path,
            backend=backend,
            use_selfhost=use_selfhost,
            use_hir=use_hir,
            sanitize=sanitize,
            show_stats=show_stats,
            link_libraries=link_libraries,
            execution_context=ExecutionContext.for_program(
                prog_cmd,
                program_args,
                **({"terminate": _capture_application_exit} if report_state is not None else {}),
            ),
            json_v1=json_v1,
            tool_command=command_for_output,
            report_state=report_state,
        )
        if report_state is not None:
            if report_state["status"] == "pending":
                _set_run_report(
                    report_state,
                    "ok",
                    0,
                    0,
                    execution={"kind": "application_exit", "exit_code": 0, "signal": None},
                )
            try:
                _write_run_report(report_json_path, invocation_id, report_state)
            except OSError as report_error:
                print(f"{prog}: unable to finalize JSON report: {report_error}", file=sys.stderr)
                raise SystemExit(3)
            sys.exit(report_state["process_exit_code"])
        sys.exit(code)

    # No file argument. Dropping into the REPL silently made `w check` in a Makefile exit
    # 0 without checking anything, so the REPL now needs to be asked for explicitly and
    # every other subcommand is a usage error.
    if report_json_path is not None:
        usage_error("--report-json requires a source file")
    if program_args:
        usage_error("program arguments require a source file")
    if subcmd is None or subcmd == "repl":
        if json_v1:
            usage_error("--output-format=json-v1 requires a supported command and a source file")
        repl(show_tokens=show_tokens, show_ast=show_ast)
        return
    if json_v1:
        usage_error(f"{subcmd} requires a source file")
    print(f"{prog_cmd} {subcmd}: no input file given", file=sys.stderr)
    print(f"usage: {prog_cmd} {subcmd} <file.wysna> [options]", file=sys.stderr)
    print(f"       {prog_cmd} repl        # start the interactive REPL", file=sys.stderr)
    sys.exit(2)

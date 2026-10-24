"""User-facing project workflows kept separate from compiler maintenance commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from common.Version import WYSNA_VERSION
from compiler.BrowserLibraryToolchain import (
    EMSCRIPTEN_VERSION,
    EMSCRIPTEN_RUNTIME_METHODS,
    BrowserLibraryCompilationError,
    BrowserLibraryToolchain,
)
from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeCompilationError, NativeToolchain
from compiler.WasiToolchain import WasiCompilationError, WasiToolchain
from error.Error import ToolchainError, WysnaError
from project.Manifest import ManifestError
from project.Resolver import ProjectResolver
from project.Targets import target_spec
from runtime.ExecutionContext import ExecutionContext
from runtime.WasiRunner import WasiRunner, WasiRuntimeError
from tooling.TargetValidation import validate_checked_target
from tooling.ToolResult import (
    artifact_file,
    diagnostic_from_error,
    diagnostic_record,
    tool_result,
    write_tool_result,
    write_tool_result_atomic,
)


_MAX_CAPTURE_BYTES = 1_048_576


def _protected_project_paths(resolver):
    protected = {resolver.manifest.path.resolve(), resolver.lock_path().resolve()}
    for package in resolver.packages.values():
        protected.add(package.manifest.path.resolve())
        protected.update(path.resolve() for path in package.manifest.root.rglob("*.wysna"))
    return protected


class _StrictArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ManifestError(message)


def _find_manifest(explicit):
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_dir():
            path = path / "wysna.toml"
        if not path.is_file():
            raise ManifestError(f"manifest not found: '{path}'")
        return path.resolve()
    current = Path.cwd().resolve()
    for directory in (current, *current.parents):
        candidate = directory / "wysna.toml"
        if candidate.is_file():
            return candidate
    raise ManifestError("no wysna.toml found from the current directory to its parents")


def _parse_arguments(argv):
    if not argv or argv[0] in ("--help", "-h"):
        print("Usage: wysna project <lock|check|build|run|test> [options]")
        print("Use --manifest PATH to select a project root explicitly.")
        return None
    command = argv[0]
    if command not in ("lock", "check", "build", "run", "test"):
        raise ManifestError(f"unknown project command '{command}'")
    parser = _StrictArgumentParser(prog=f"wysna project {command}", add_help=True)
    parser.add_argument("--manifest")
    parser.add_argument("--frozen", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output-format", choices=("json-v1",))
    parser.add_argument("--target")
    parser.add_argument("--entry")
    parser.add_argument("--filter")
    parser.add_argument("--report-json")
    parser.add_argument("-o", "--output")
    separator = argv.index("--") if "--" in argv else len(argv)
    option_args = argv[1:separator]
    program_args = argv[separator + 1:] if separator < len(argv) else []
    if program_args and command != "run":
        raise ManifestError("arguments after -- are supported only by 'wysna project run'")
    options = parser.parse_args(option_args)
    if options.frozen and command == "lock":
        raise ManifestError("project lock cannot combine --frozen with lockfile updates")
    if options.entry and command != "run":
        raise ManifestError("--entry is supported only by 'wysna project run'")
    if options.filter and command != "test":
        raise ManifestError("--filter is supported only by 'wysna project test'")
    if options.report_json and command not in ("run", "test"):
        raise ManifestError("--report-json is supported only by project run and test")
    if options.output and command != "build":
        raise ManifestError("-o is supported only by 'wysna project build'")
    if options.output_format and command not in ("check", "build"):
        raise ManifestError("--output-format=json-v1 is supported only by project check and build")
    if options.target and command not in ("check", "build", "run"):
        raise ManifestError("--target is supported only by project check, build, and run")
    return command, options, program_args


def _emit(command, status, code, *, diagnostics=(), artifacts=(), target=None, json_mode=False):
    if json_mode:
        write_tool_result(tool_result(command, status, code, diagnostics=list(diagnostics),
                                       artifacts=list(artifacts), target=target), sys.stdout)
    return code


def _target_metadata(target):
    spec = target_spec(target.profile)
    return {"profile": spec.profile, "triple": spec.triple}


def _check_frozen(resolver, frozen):
    if frozen:
        resolver.verify_lock()


def _run_check(resolver, target_name, json_mode):
    manifest = resolver.manifest
    target = manifest.targets.get(target_name)
    if target is None:
        raise ManifestError(f"unknown target '{target_name}'")
    service = CompilerService(project_root=manifest.root, project_resolver=resolver)
    try:
        checked = service.check_file(manifest.root / target.entry)
        validate_checked_target(checked, target)
    except WysnaError as error:
        diagnostic = diagnostic_from_error(error, service.source_texts())
        if json_mode:
            return _emit("project.check", "diagnostic_error", 1, diagnostics=[diagnostic],
                         target=_target_metadata(target), json_mode=True)
        print(str(error), file=sys.stderr)
        return 1
    if json_mode:
        return _emit("project.check", "ok", 0,
                     target=_target_metadata(target), json_mode=True)
    print(f"[OK] Project check passed: {manifest.name} ({len(checked.modules)} modules)")
    return 0


def _run_build(resolver, target_name, output, json_mode):
    manifest = resolver.manifest
    target = manifest.targets.get(target_name)
    if target is None:
        raise ManifestError(f"unknown target '{target_name}'")
    entry = manifest.root / target.entry
    destination = Path(output).expanduser() if output else manifest.root / "build" / target.name
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    if destination.resolve() in _protected_project_paths(resolver):
        raise ManifestError("build output must not overwrite a source, manifest, or lockfile")
    service = CompilerService(project_root=manifest.root, project_resolver=resolver)
    try:
        checked = service.check_file(entry)
        spec = validate_checked_target(checked, target)
        emission = service.emit_c(checked, target_profile=target.profile)
        c_source = emission.source
        destination.parent.mkdir(parents=True, exist_ok=True)
        if target.profile == "wasi-command":
            built = WasiToolchain(Path(__file__).resolve().parents[1]).build(c_source, destination)
        elif target.profile == "browser-library":
            built = BrowserLibraryToolchain(Path(__file__).resolve().parents[1]).build(
                c_source, destination, emission.abi_exports,
                memory_limit_mb=target.memory_limit_mb,
            )
        else:
            built = NativeToolchain(Path(__file__).resolve().parents[1]).build(c_source, destination)
    except ToolchainError as error:
        diagnostic = diagnostic_from_error(error, service.source_texts())
        if json_mode:
            return _emit("project.build", "toolchain_error", 3, diagnostics=[diagnostic],
                         target=_target_metadata(target), json_mode=True)
        print(str(error), file=sys.stderr)
        return 3
    except WysnaError as error:
        diagnostic = diagnostic_from_error(error, service.source_texts())
        if json_mode:
            return _emit("project.build", "diagnostic_error", 1, diagnostics=[diagnostic],
                         target=_target_metadata(target), json_mode=True)
        print(str(error), file=sys.stderr)
        return 1
    except WasiCompilationError as error:
        diagnostic = diagnostic_record("W5001", "wasi-toolchain", str(error))
        if json_mode:
            return _emit("project.build", "toolchain_error", 3, diagnostics=[diagnostic],
                         target=_target_metadata(target), json_mode=True)
        print(str(error), file=sys.stderr)
        return 3
    except BrowserLibraryCompilationError as error:
        diagnostic = diagnostic_record("W5005", "browser-toolchain", str(error))
        if json_mode:
            return _emit("project.build", "toolchain_error", 3, diagnostics=[diagnostic],
                         target=_target_metadata(target), json_mode=True)
        print(str(error), file=sys.stderr)
        return 3
    except NativeCompilationError as error:
        diagnostic = diagnostic_record("W5004", "native-toolchain", str(error))
        if json_mode:
            return _emit("project.build", "toolchain_error", 3, diagnostics=[diagnostic],
                         target=_target_metadata(target), json_mode=True)
        print(error.stderr or str(error), file=sys.stderr)
        return 3
    if target.profile == "browser-library":
        artifacts = [
            artifact_file("browser_library", "text/javascript", built.artifacts[0]),
            artifact_file("browser_runtime_module", "text/javascript", built.artifacts[1]),
            artifact_file("wasm_module", "application/wasm", built.artifacts[2]),
        ]
    else:
        artifacts = [artifact_file(spec.artifact_kind.value,
                                   "application/wasm" if target.profile == "wasi-command"
                                   else "application/octet-stream", built.output_path)]
    source_hashes = []
    for source_path, source_text in service.source_texts().items():
        path = Path(source_path).resolve()
        try:
            relative = path.relative_to(manifest.root).as_posix()
        except ValueError:
            continue
        source_hashes.append({"path": relative,
                              "sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest()})
    source_hashes.sort(key=lambda item: item["path"])
    lock_path = resolver.lock_path()
    lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.is_file() else None
    from tooling.ToolResult import artifact_inline
    if target.profile == "wasi-command":
        from compiler.WasiToolchain import WASI_STACK_SIZE_BYTES
        compiler_metadata = {
            "wasi_sdk_path": built.sdk_path,
            "wasi_sdk_compiler": built.compiler_path,
            "wasi_sdk_compiler_version": built.compiler_version,
            "wasi_runtime": "wasmtime==50.0.1",
            "wasi_stack_size_bytes": WASI_STACK_SIZE_BYTES,
            "wasi_imports": list(built.imports),
            "wasm_exports": list(built.exports),
        }
        build_flags = ["--target=wasm32-wasip1", "-std=c99", "-O2",
                       f"-Wl,-z,stack-size={WASI_STACK_SIZE_BYTES}", "-Wl,--stack-first"]
    elif target.profile == "browser-library":
        compiler_metadata = {
            "emscripten_compiler": built.compiler_path,
            "emscripten_compiler_version": built.compiler_version,
            "emscripten_sdk_version": EMSCRIPTEN_VERSION,
            "wasm_imports": list(built.wasm_imports),
            "wasm_exports": list(built.wasm_exports),
        }
        build_flags = ["--target=wasm32-unknown-emscripten", "-std=c99", "-O2",
                       "-sMODULARIZE=1", "-sEXPORT_ES6=1", "-sWASM_BIGINT=1",
                       "-sALLOW_MEMORY_GROWTH=1",
                       "-sEXPORTED_RUNTIME_METHODS=" + ",".join(EMSCRIPTEN_RUNTIME_METHODS),
                       "-sFILESYSTEM=0"]
    else:
        from common.Platform import compiler_command
        compiler_metadata = {"c_compiler_command": compiler_command()}
        build_flags = ["-std=c99", "-O2"]
    provenance = {
        "schema_version": "wysna.build-provenance/1",
        "project": {"name": manifest.name, "version": manifest.version},
        "manifest_sha256": manifest.raw_sha256,
        "compiler_version": WYSNA_VERSION,
        "stdlib_version": WYSNA_VERSION,
        "runtime_abi": 2,
        "target": target.profile,
        "flags": build_flags,
        "lock_sha256": lock_hash,
        "sources": source_hashes,
    }
    if target.profile == "wasi-command":
        provenance.update({
            "target_spec": {"triple": spec.triple, "artifact_kind": spec.artifact_kind.value},
            "compiler": compiler_metadata,
            "capabilities": list(spec.capabilities),
            "wasi": {
                "fuel": target.fuel,
                "memory_limit_bytes": target.memory_limit_mb * 1024 * 1024,
                "stack_size_bytes": WASI_STACK_SIZE_BYTES,
                "preopens": [{"path": item.path, "access": item.access}
                             for item in target.preopens],
            },
        })
    elif target.profile == "browser-library":
        provenance.update({
            "target_spec": {"triple": spec.triple, "artifact_kind": spec.artifact_kind.value},
            "compiler": compiler_metadata,
            "capabilities": list(spec.capabilities),
            "browser": {
                "abi_version": 1,
                "memory_limit_bytes": built.memory_limit_bytes,
                "exports": list(emission.abi_exports),
            },
        })
    else:
        provenance["c_compiler_command"] = compiler_command()
    provenance_artifact = artifact_inline("build_provenance", "application/json",
                                           json.dumps(provenance, ensure_ascii=False,
                                                      sort_keys=True, separators=(",", ":")))
    if json_mode:
        return _emit("project.build", "ok", 0, artifacts=artifacts + [provenance_artifact],
                     target=_target_metadata(target), json_mode=True)
    print(f"[OK] Built {manifest.name} -> {built.output_path}")
    return 0


def _entry_path(manifest, target_name, override):
    if override is None:
        target = manifest.targets.get(target_name)
        if target is None:
            raise ManifestError(f"unknown target '{target_name}'")
        relative = target.entry
    else:
        value = Path(override)
        if value.is_absolute() or ".." in value.parts or "\\" in override:
            raise ManifestError("--entry must be a package-relative path without '..'")
        relative = value.as_posix()
    path = (manifest.root / relative).resolve()
    try:
        path.relative_to(manifest.root)
    except ValueError as error:
        raise ManifestError("project entry escapes the package root") from error
    if not path.is_file() or path.suffix != ".wysna":
        raise ManifestError(f"project entry is not an existing .wysna file: '{relative}'")
    return path


def _write_output_bytes(stream, content):
    binary_stream = getattr(stream, "buffer", None)
    if binary_stream is None:
        stream.write(content.decode("utf-8", errors="replace"))
    else:
        binary_stream.write(content)
    stream.flush()


def _run_wasi_program(resolver, target, entry, program_args, report_path=None):
    manifest = resolver.manifest
    if report_path:
        report = Path(report_path).expanduser().resolve()
        if report in _protected_project_paths(resolver):
            print("wysna: run report path must not overwrite a source, manifest, or lockfile",
                  file=sys.stderr)
            return 2
        pending = tool_result("project.run", "pending", None, process_exit_code=None,
                              target=_target_metadata(target))
        invocation_id = pending["invocation_id"]
        try:
            write_tool_result_atomic(pending, report_path)
        except OSError as error:
            print(f"wysna: cannot initialize run report '{report_path}': {error}", file=sys.stderr)
            return 3
    else:
        invocation_id = None

    def finish(status, tool_exit_code, process_exit_code, *, execution=None, diagnostics=()):
        if not report_path:
            return process_exit_code
        result = tool_result("project.run", status, tool_exit_code,
                             process_exit_code=process_exit_code, execution=execution,
                             diagnostics=list(diagnostics), invocation_id=invocation_id,
                             target=_target_metadata(target))
        try:
            write_tool_result_atomic(result, report_path)
        except OSError as error:
            print(f"wysna: unable to finalize run report: {error}", file=sys.stderr)
            return 3
        return process_exit_code

    service = CompilerService(project_root=manifest.root, project_resolver=resolver)
    try:
        checked = service.check_file(entry)
        validate_checked_target(checked, target)
        c_source = service.emit_c(checked).source
        with tempfile.TemporaryDirectory(prefix="wysna-wasi-run-") as temporary:
            wasm_path = Path(temporary) / "program.wasm"
            WasiToolchain(Path(__file__).resolve().parents[1]).build(c_source, wasm_path)
            result = WasiRunner().run(
                wasm_path, project_root=manifest.root, arguments=program_args,
                preopens=target.preopens, fuel=target.fuel,
                memory_limit_mb=target.memory_limit_mb, capture_output=bool(report_path),
            )
    except WysnaError as error:
        diagnostic = diagnostic_from_error(error, service.source_texts())
        if not report_path:
            print(str(error), file=sys.stderr)
        return finish("diagnostic_error", 1, 1, diagnostics=[diagnostic])
    except WasiCompilationError as error:
        diagnostic = diagnostic_record("W5001", "wasi-toolchain", str(error))
        if not report_path:
            print(str(error), file=sys.stderr)
        return finish("toolchain_error", 3, 3, diagnostics=[diagnostic])
    except WasiRuntimeError as error:
        diagnostic = diagnostic_record("W4003", "wasi-runtime", str(error), filename=str(entry))
        if not report_path:
            print(str(error), file=sys.stderr)
        return finish("execution_error", 5, 5, diagnostics=[diagnostic])

    if report_path:
        _write_output_bytes(sys.stdout, result.stdout)
        _write_output_bytes(sys.stderr, result.stderr)
        execution = {
            "kind": "wasm",
            "runtime": "wasmtime==50.0.1",
            "exit_code": result.exit_code,
            "signal": None,
            "fuel_remaining": result.fuel_remaining,
            "elapsed_ms": round(result.elapsed_ms, 3),
            "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
        }
        return finish("ok", 0, result.exit_code, execution=execution)
    return result.exit_code


def _run_program(resolver, target_name, entry_override, program_args, report_path=None):
    manifest = resolver.manifest
    target = manifest.targets.get(target_name)
    if target is None:
        raise ManifestError(f"unknown target '{target_name}'")
    if target.profile == "browser-library":
        raise ManifestError("browser-library targets cannot be run as command-line programs")
    entry = _entry_path(manifest, target_name, entry_override)
    if target.profile == "wasi-command":
        return _run_wasi_program(resolver, target, entry, program_args, report_path)
    try:
        source = entry.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        print(f"wysna: cannot read project entry '{entry}': {error}", file=sys.stderr)
        return 3
    if report_path:
        report = Path(report_path).expanduser().resolve()
        if report in _protected_project_paths(resolver):
            print("wysna: run report path must not overwrite a source, manifest, or lockfile", file=sys.stderr)
            return 2
    report_state = None
    invocation_id = None
    if report_path:
        pending = tool_result("project.run", "pending", None, process_exit_code=None)
        invocation_id = pending["invocation_id"]
        try:
            write_tool_result_atomic(pending, report_path)
        except OSError as error:
            print(f"wysna: cannot initialize run report '{report_path}': {error}", file=sys.stderr)
            return 3
        report_state = {"status": "pending", "tool_exit_code": None, "process_exit_code": None,
                        "execution": None, "diagnostics": []}
    from tooling.RunReport import _capture_application_exit, _set_run_report
    from tooling.SourceCommand import run_pipeline
    code = run_pipeline(
        source_code=source,
        file_name=str(entry),
        execution_context=ExecutionContext.for_program(
            "wysna", program_args, cwd=manifest.root,
            **({"terminate": _capture_application_exit} if report_state is not None else {}),
        ),
        project_root=manifest.root,
        project_resolver=resolver,
        tool_command="project.run",
        report_state=report_state,
    )
    if report_state is None:
        return code
    if report_state["status"] == "pending":
        _set_run_report(report_state, "ok", 0, 0,
                        execution={"kind": "application_exit", "exit_code": 0, "signal": None})
    result = tool_result("project.run", report_state["status"], report_state["tool_exit_code"],
                         process_exit_code=report_state["process_exit_code"],
                         execution=report_state["execution"], diagnostics=report_state["diagnostics"],
                         invocation_id=invocation_id)
    try:
        write_tool_result_atomic(result, report_path)
    except OSError as error:
        print(f"wysna: unable to finalize run report: {error}", file=sys.stderr)
        return 3
    return report_state["process_exit_code"]


def _run_tests(resolver, filter_text, report_path, frozen=False):
    manifest = resolver.manifest
    tests = [case for case in manifest.tests if filter_text is None or filter_text in case.name]
    if report_path:
        report = Path(report_path).expanduser().resolve()
        if report in _protected_project_paths(resolver):
            raise ManifestError("test report path must not overwrite a source, manifest, or lockfile")
    if not tests:
        if filter_text is not None:
            raise ManifestError(f"test filter '{filter_text}' matched no cases")
        print("[EMPTY] No [[tests]] cases are declared; no project code was checked.")
        if report_path:
            lock_path = resolver.lock_path()
            write_tool_result_atomic({
                "schema_version": "wysna.project-test-report/1",
                "project": manifest.name,
                "manifest": str(manifest.path),
                "manifest_sha256": manifest.raw_sha256,
                "toolchain": WYSNA_VERSION,
                "lock_sha256": (
                    hashlib.sha256(lock_path.read_bytes()).hexdigest()
                    if lock_path.is_file() else None
                ),
                "status": "empty",
                "duration_ms": 0.0,
                "selected": 0,
                "passed": 0,
                "failed": 0,
                "cases": [],
            }, report_path)
        return 2

    script = Path(__file__).resolve().parents[1] / "main.py"
    records = []
    lock_path = resolver.lock_path()
    lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.is_file() else None
    overall_start = time.perf_counter()
    for case in tests:
        entry_bytes = (manifest.root / case.entry).read_bytes()
        entry_hash = hashlib.sha256(entry_bytes).hexdigest()
        started = time.perf_counter()
        deadline = started + case.timeout_ms / 1000
        command = [sys.executable, str(script), "project", "run", "--manifest",
                   str(manifest.path), "--entry", case.entry]
        if frozen:
            command.append("--frozen")
        process = subprocess.Popen(
            command, cwd=manifest.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"),
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        )
        from runtime.HostAbi import attach_process_tree, release_process_tree
        attach_process_tree(process)
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        output_limit_event = threading.Event()

        def drain(stream, name):
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                remaining = _MAX_CAPTURE_BYTES - len(captured[name])
                if remaining > 0:
                    captured[name].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    output_limit_event.set()
                    try:
                        from runtime.HostAbi import terminate_process_tree
                        terminate_process_tree(process)
                    except (ImportError, OSError):
                        pass

        readers = [threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
                   threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True)]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            process.wait(timeout=max(0.0, deadline - time.perf_counter()))
        except subprocess.TimeoutExpired:
            timed_out = True
            from runtime.HostAbi import terminate_process_tree
            terminate_process_tree(process, force_group=True)
            process.wait()
        from runtime.HostAbi import terminate_process_tree
        terminate_process_tree(process, force_group=True)
        release_process_tree(process)
        for reader in readers:
            reader.join()
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        stdout_bytes = bytes(captured["stdout"])
        stderr_bytes = bytes(captured["stderr"])
        output_limited = output_limit_event.is_set()
        if timed_out:
            status = "timed_out"
            exit_code = None
        elif output_limited:
            status = "output_limited"
            exit_code = process.returncode
        else:
            status = "passed" if process.returncode == 0 else "failed"
            exit_code = process.returncode
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        if stdout_text:
            print(f"--- {case.name} stdout ---")
            print(stdout_text, end="" if stdout_text.endswith("\n") else "\n")
        if stderr_text:
            print(f"--- {case.name} stderr ---", file=sys.stderr)
            print(stderr_text, end="" if stderr_text.endswith("\n") else "\n", file=sys.stderr)
        print(f"[{status.upper()}] {case.name} ({duration_ms:.1f} ms)")
        records.append({
            "name": case.name,
            "entry": case.entry,
            "entry_sha256": entry_hash,
            "status": status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "timeout_ms": case.timeout_ms,
            "output_limit_bytes_per_stream": _MAX_CAPTURE_BYTES,
            "output_limited": output_limited,
            "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        })
    failed = sum(case["status"] != "passed" for case in records)
    report = {
        "schema_version": "wysna.project-test-report/1",
        "project": manifest.name,
        "manifest": str(manifest.path),
        "manifest_sha256": manifest.raw_sha256,
        "toolchain": WYSNA_VERSION,
        "lock_sha256": lock_hash,
        "status": "passed" if failed == 0 else "failed",
        "duration_ms": round((time.perf_counter() - overall_start) * 1000, 3),
        "selected": len(records),
        "passed": len(records) - failed,
        "failed": failed,
        "cases": records,
    }
    if report_path:
        write_tool_result_atomic(report, report_path)
    return 1 if failed else 0


def run_project_command(argv):
    option_argv = argv[:argv.index("--")] if "--" in argv else argv
    json_mode = ("--output-format=json-v1" in option_argv
                 or any(option_argv[index:index + 2] == ["--output-format", "json-v1"]
                        for index in range(len(option_argv) - 1)))
    result_command = "project"
    try:
        parsed = _parse_arguments(argv)
        if parsed is None:
            return 0
        command, options, program_args = parsed
        result_command = "project." + command
        manifest_path = _find_manifest(options.manifest)
        resolver = ProjectResolver.load(manifest_path)
        if command == "lock":
            resolver.write_lock()
            print(f"[OK] Wrote {resolver.lock_path()}")
            return 0
        _check_frozen(resolver, options.frozen)
        if command == "check":
            return _run_check(resolver, options.target or "app", json_mode)
        if command == "build":
            return _run_build(resolver, options.target or "app", options.output, json_mode)
        if command == "run":
            return _run_program(resolver, options.target or "app", options.entry, program_args,
                                report_path=options.report_json)
        return _run_tests(resolver, options.filter, options.report_json, options.frozen)
    except (ManifestError, OSError, ValueError) as error:
        message = str(error)
        if json_mode:
            diagnostic = diagnostic_record("W5002", result_command, message)
            return _emit(result_command, "usage_error", 2, diagnostics=[diagnostic], json_mode=True)
        print(f"wysna project: {message}", file=sys.stderr)
        return 2

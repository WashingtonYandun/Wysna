"""User-facing project workflows kept separate from compiler maintenance commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from common.Paths import get_repo_root
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
from project.Architecture import check_architecture
from project.Manifest import ManifestError, ManifestIOError, Target
from project.Resolver import ProjectResolver
from project.Scaffold import ScaffoldError, create_project
from project.Targets import target_spec
from project.Testing import build_test_plan, capture_test, test_failure_kind, TestPlanMismatch, write_junit
from runtime.ExecutionContext import ExecutionContext
from runtime.WasiRunner import WasiRunner, WasiRuntimeError
from tooling.TargetValidation import validate_checked_target
from tooling.DiagnosticRender import render_diagnostic
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
        print("Usage: wysna project <new|lock|check|build|run|test> [options]")
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
    parser.add_argument("--report-junit")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--case")
    parser.add_argument("--expect-plan-hash")
    parser.add_argument("--test-entry", action="store_true", help=argparse.SUPPRESS)
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
    if options.output_format and command not in ("check", "build", "test", "lock"):
        raise ManifestError("--output-format=json-v1 is supported only by project lock, check, build and test")
    if command != "test" and (options.report_junit or options.list or options.discover or options.case or options.expect_plan_hash):
        raise ManifestError("test selection and JUnit options are supported only by project test")
    if options.list and options.report_junit:
        raise ManifestError("--report-junit requires test execution, not --list")
    if options.test_entry and (command != 'run' or not options.entry or options.target):
        raise ManifestError('the test worker requires a native entry without a target override')
    if options.target and command not in ("check", "build", "run"):
        raise ManifestError("--target is supported only by project check, build, and run")
    return command, options, program_args


def _emit(command, status, code, *, diagnostics=(), artifacts=(), target=None, result=None, json_mode=False):
    if json_mode:
        envelope = tool_result(command, status, code, diagnostics=list(diagnostics),
                               artifacts=list(artifacts), target=target)
        if result is not None:
            envelope["result"] = result
        write_tool_result(envelope, sys.stdout)
    return code


def _target_metadata(target):
    spec = target_spec(target.profile)
    return {"profile": spec.profile, "triple": spec.triple}


def _check_frozen(resolver, frozen):
    if frozen:
        resolver.verify_lock()


def _resolve_target_name(manifest, requested):
    if requested:
        return requested
    if "app" in manifest.targets or len(manifest.targets) != 1:
        return "app"
    return next(iter(manifest.targets))


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
        print(render_diagnostic(error, service.source_texts()), file=sys.stderr)
        return 1
    report, issues = check_architecture(manifest, checked)
    if json_mode:
        sources = service.source_texts()
        if report:
            sources[str(manifest.path)] = manifest.raw.decode("utf-8")
        return _emit("project.check", "diagnostic_error" if issues else "ok", int(bool(issues)),
                     diagnostics=[diagnostic_from_error(issue, sources) for issue in issues],
                     target=_target_metadata(target), result={"architecture": report} if report else None,
                     json_mode=True)
    if issues:
        for issue in issues:
            print(render_diagnostic(issue, service.source_texts()), file=sys.stderr)
        return 1
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
            built = WasiToolchain(Path(get_repo_root())).build(c_source, destination)
        elif target.profile == "browser-library":
            built = BrowserLibraryToolchain(Path(get_repo_root())).build(
                c_source, destination, emission.abi_exports,
                memory_limit_mb=target.memory_limit_mb,
            )
        else:
            built = NativeToolchain(Path(get_repo_root())).build(c_source, destination)
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
        print(render_diagnostic(error, service.source_texts()), file=sys.stderr)
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
            WasiToolchain(Path(get_repo_root())).build(c_source, wasm_path)
            result = WasiRunner().run(
                wasm_path, project_root=manifest.root, arguments=program_args,
                preopens=target.preopens, fuel=target.fuel,
                memory_limit_mb=target.memory_limit_mb, capture_output=bool(report_path),
            )
    except WysnaError as error:
        diagnostic = diagnostic_from_error(error, service.source_texts())
        if not report_path:
            print(render_diagnostic(error, service.source_texts()), file=sys.stderr)
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


def _run_program(resolver, target_name, entry_override, program_args, report_path=None, native_test=False):
    manifest = resolver.manifest
    target = Target('test', entry_override, 'native-cli') if native_test else manifest.targets.get(target_name)
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


def _finish_test_report(report, code, report_path, junit_path, json_mode):
    artifacts = []
    diagnostics = []
    try:
        if report_path:
            write_tool_result_atomic(report, report_path)
            artifacts.append(artifact_file('test-report', 'application/json', Path(report_path).resolve()))
        if junit_path:
            write_junit(report, junit_path)
            artifacts.append(artifact_file('test-report', 'application/xml', Path(junit_path).resolve()))
    except OSError as error:
        code = 3
        diagnostics.append(diagnostic_record('W5004', 'filesystem', str(error)))
        if not json_mode:
            print(f'wysna: cannot publish test report: {error}', file=sys.stderr)
    if json_mode:
        status = {0: 'ok', 2: 'usage_error', 3: 'toolchain_error', 130: 'cancelled'}.get(code, 'runtime_error')
        payload = tool_result('project.test', status, code, artifacts=artifacts, diagnostics=diagnostics)
        payload['result'] = report
        write_tool_result(payload, sys.stdout)
    return code


def _run_tests(resolver, filter_text, report_path, frozen=False, *, json_mode=False,
               list_only=False, discover=False, case_id=None, expected_hash=None, junit_path=None):
    manifest = resolver.manifest
    tests, plan = build_test_plan(resolver, filter_text, discover, case_id)
    if expected_hash is not None and expected_hash != plan['plan_hash']:
        raise TestPlanMismatch('test plan is stale; list tests again before executing')
    destinations = [Path(value).expanduser().resolve() for value in (report_path, junit_path) if value]
    if len(set(destinations)) != len(destinations):
        raise ManifestError('JSON and JUnit reports require different paths')
    for destination in destinations:
        if destination in _protected_project_paths(resolver):
            raise ManifestError('test report path must not overwrite a source, manifest, or lockfile')
    if list_only:
        code = _finish_test_report(plan, 0 if tests else 2, report_path, None, json_mode)
        if not json_mode:
            for case in plan['cases']:
                print(case['id'] + ' ' + case['entry'])
        return code
    if not tests:
        if not json_mode:
            print('[EMPTY] No tests selected; no project code was checked.')
        report = {'schema_version': 'wysna.project-test-report/1', 'project': manifest.name,
                  'manifest': str(manifest.path), 'manifest_sha256': manifest.raw_sha256,
                  'toolchain': WYSNA_VERSION, 'lock_sha256': plan['lock_sha256'],
                  'workspace_revision': plan['workspace_revision'],
                  'plan_hash': plan['plan_hash'], 'status': 'empty', 'duration_ms': 0.0,
                  'selected': 0, 'executed': 0, 'passed': 0, 'failed': 0, 'cases': []}
        return _finish_test_report(report, 2, report_path, junit_path, json_mode)

    report, code = execute_project_tests(manifest, tests, plan, frozen, json_mode=json_mode)
    return _finish_test_report(report, code, report_path, junit_path, json_mode)


def execute_project_tests(manifest, tests, plan, frozen, *, json_mode):
    """Execute a nonempty, resolved test plan using the normal project workers."""
    script = Path(get_repo_root()) / "main.py"
    installed_entry = script.parent / "wysna_entry.py"
    if installed_entry.is_file() and (script.parent / "installation.json").is_file():
        runner = [sys.executable, "-I", "-B", "-X", "utf8", str(installed_entry)]
    else:
        runner = [sys.executable, "-B", str(script)]
    records = []
    overall_start = time.perf_counter()
    for case in tests:
        entry_bytes = (manifest.root / case.entry).read_bytes()
        entry_hash = hashlib.sha256(entry_bytes).hexdigest()
        started = time.perf_counter()
        command = runner + ["project", "run", "--manifest", str(manifest.path), "--entry", case.entry, '--test-entry']
        if frozen:
            command.append("--frozen")
        status, exit_code, stdout_bytes, stderr_bytes, child_report = capture_test(
            command, manifest.root, case.timeout_ms)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        output_limited = status == 'output_limited'
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        if stdout_text and not json_mode:
            print(f"--- {case.name} stdout ---")
            print(stdout_text, end="" if stdout_text.endswith("\n") else "\n")
        if stderr_text and not json_mode:
            print(f"--- {case.name} stderr ---", file=sys.stderr)
            print(stderr_text, end="" if stderr_text.endswith("\n") else "\n", file=sys.stderr)
        if not json_mode:
            print(f"[{status.upper()}] {case.name} ({duration_ms:.1f} ms)")
        records.append({
            'id': 'test:' + case.name,
            'stdout': stdout_text,
            'stderr': stderr_text,
            'failure_kind': test_failure_kind(status, child_report),
            'diagnostics': child_report.get('diagnostics', []),
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
        if status == 'cancelled':
            break
    cancelled = any(case['status'] == 'cancelled' for case in records)
    failed_cases = [case for case in records if case['status'] != 'passed']
    failed = len(failed_cases)
    report = {
        "schema_version": "wysna.project-test-report/1",
        "project": manifest.name,
        "manifest": str(manifest.path),
        "manifest_sha256": manifest.raw_sha256,
        "toolchain": WYSNA_VERSION,
        "lock_sha256": plan['lock_sha256'],
        "workspace_revision": plan['workspace_revision'],
        "plan_hash": plan['plan_hash'],
        "status": "cancelled" if cancelled else "passed" if failed == 0 else "failed",
        "duration_ms": round((time.perf_counter() - overall_start) * 1000, 3),
        "selected": len(tests),
        "executed": len(records),
        "passed": len(records) - failed,
        "failed": failed,
        "cases": records,
    }
    return report, 130 if cancelled else 1 if failed else 0


def run_project_command(argv):
    option_argv = argv[:argv.index("--")] if "--" in argv else argv
    json_mode = ("--output-format=json-v1" in option_argv
                 or any(option_argv[index:index + 2] == ["--output-format", "json-v1"]
                        for index in range(len(option_argv) - 1)))
    result_command = "project"
    try:
        if argv and argv[0] == "new":
            result_command = "project.new"
            parser = _StrictArgumentParser(prog="wysna project new")
            parser.add_argument("name")
            parser.add_argument("--output-format", choices=("json-v1",))
            options = parser.parse_args(argv[1:])
            root, files = create_project(options.name, Path.cwd())
            if json_mode:
                artifacts = [artifact_file("project-source", "text/plain", path) for path in files]
                return _emit(result_command, "ok", 0, artifacts=artifacts, json_mode=True)
            print(f"[OK] Created project: {root}")
            print(f"Next: cd {options.name} && wysna project test")
            return 0
        parsed = _parse_arguments(argv)
        if parsed is None:
            return 0
        command, options, program_args = parsed
        result_command = "project." + command
        manifest_path = _find_manifest(options.manifest)
        resolver = ProjectResolver.load(manifest_path)
        if command == "lock":
            resolver.write_lock()
            if json_mode:
                return _emit('project.lock', 'ok', 0, json_mode=True,
                             artifacts=[artifact_file('project-lock', 'application/json', resolver.lock_path())])
            print(f"[OK] Wrote {resolver.lock_path()}")
            return 0
        _check_frozen(resolver, options.frozen)
        resolved_target = (_resolve_target_name(resolver.manifest, options.target)
                           if command in ('check', 'build', 'run') and not options.test_entry else None)
        if command == "check":
            return _run_check(resolver, resolved_target, json_mode)
        if command == "build":
            return _run_build(resolver, resolved_target, options.output, json_mode)
        if command == "run":
            return _run_program(resolver, resolved_target, options.entry, program_args,
                                report_path=options.report_json, native_test=options.test_entry)
        return _run_tests(resolver, options.filter, options.report_json, options.frozen,
                          json_mode=json_mode, list_only=options.list, discover=options.discover,
                          case_id=options.case, expected_hash=options.expect_plan_hash,
                          junit_path=options.report_junit)
    except ScaffoldError as error:
        if json_mode:
            diagnostic = diagnostic_record("W5004", "filesystem", str(error))
            return _emit(result_command, "toolchain_error", 3, diagnostics=[diagnostic], json_mode=True)
        diagnostic = WysnaError(str(error), file="<project>", code="W5004", stage="filesystem")
        print(render_diagnostic(diagnostic, {}), file=sys.stderr)
        return 3
    except (ManifestError, OSError, ValueError) as error:
        message = str(error)
        if isinstance(error, (OSError, ManifestIOError)):
            if json_mode:
                diagnostic = diagnostic_record("W5004", "filesystem", message)
                return _emit(result_command, "toolchain_error", 3, diagnostics=[diagnostic], json_mode=True)
            diagnostic = WysnaError(message, file="<project>", code="W5004", stage="filesystem")
            print(render_diagnostic(diagnostic, {}), file=sys.stderr)
            return 3
        if json_mode:
            diagnostic = diagnostic_record('W5005' if isinstance(error, TestPlanMismatch) else 'W5002', result_command, message)
            return _emit(result_command, "usage_error", 2, diagnostics=[diagnostic], json_mode=True)
        print(f"wysna project: {message}", file=sys.stderr)
        return 2

"""Explicit Seed LLVM command orchestration; never selects another backend."""
import json
import sys
import tempfile
from pathlib import Path
from dataclasses import asdict
from compiler.LlvmToolchain import LlvmToolchain
from common.Platform import peak_rss_kb
from error.Error import WysnaError
from tooling.ToolResult import artifact_file, tool_result, write_tool_result


def run_llvm_command(service, checked, command, ir_path, binary_path, profile,
                     json_v1=False, show_stats=False, *, link_libraries=(), sanitize=False,
                     execution_context=None, run_timeout_ms=None, report_state=None):
    if ((command == "check" and (ir_path is not None or binary_path is not None))
            or (command == "emit-llvm" and (ir_path is None or binary_path is not None))
            or (command == "build" and (binary_path is None or ir_path is not None))):
        raise WysnaError("Conflicting LLVM command outputs", code="W5002", stage="options")
    if command in ('check', 'emit-llvm') and (sanitize or link_libraries):
        raise WysnaError('Link inputs and sanitizers are build/run options', code='W5002', stage='options')
    if command == 'run':
        if ir_path is not None or binary_path is not None or json_v1:
            raise WysnaError('LLVM run does not accept output artifacts or JSON stdout', code='W5002', stage='options')
        from compiler.NativeExecution import run_native
        from runtime.ExecutionContext import ExecutionContext
        from tooling.RunReport import _set_run_report
        emission = service.emit_llvm(checked, profile=profile)
        if report_state is not None:
            report_state['target'] = {'profile': emission.target.source_profile, 'triple': emission.target.triple}
        with tempfile.TemporaryDirectory(prefix='wysna-llvm-run-') as temporary:
            build = LlvmToolchain(profile=profile).build(emission, Path(temporary) / 'program',
                                                       sanitize=sanitize, link_libraries=link_libraries)
            execution = run_native(build.output_path, execution_context or ExecutionContext.for_program('wysna'), run_timeout_ms)
        if execution.exit_code is not None:
            _set_run_report(report_state, 'ok', 0, execution.exit_code,
                            execution={'kind': 'application_exit', 'exit_code': execution.exit_code, 'signal': None})
            return execution.exit_code
        kind = 'timeout' if execution.timed_out else 'interrupted' if execution.interrupted else 'signal'
        code = 124 if execution.timed_out else 130 if execution.interrupted else 128 + execution.signal
        _set_run_report(report_state, 'execution_error', 5, 5,
                        execution={'kind': kind, 'exit_code': None, 'signal': execution.signal,
                                   'data': {'timeout_ms': run_timeout_ms}})
        return 5 if report_state is not None else code
    emission = service.emit_llvm(checked, profile=profile)
    timings = dict(emission.timing_ms)
    artifacts = []
    build = None
    target = {"profile": emission.target.source_profile, "triple": emission.target.triple}
    if command != "check":
        toolchain = LlvmToolchain(profile=profile)
        if command == "emit-llvm":
            output = toolchain.write_ir(emission, ir_path)
            artifacts.append(artifact_file("llvm_ir", "text/x-llvm", output))
        else:
            build = toolchain.build(emission, binary_path, link_libraries=link_libraries, sanitize=sanitize)
            for phase, duration in build.timing_ms.items():
                timings[phase] = timings.get(phase, 0) + duration
            if build.debug_symbols:
                artifacts.append(artifact_file("debug_symbols", "application/octet-stream", build.debug_symbols))
            artifacts.append(artifact_file("native_binary", "application/octet-stream", build.output_path))
    if json_v1:
        write_tool_result(tool_result(command, "ok", 0, artifacts=artifacts, target=target), sys.stdout)
    elif not show_stats:
        print("[OK] " + command + " passed (" + emission.target.source_profile + ", " + profile + ")")
    if show_stats:
        statistics = {"status": "ok", "backend": "llvm-experimental",
                      "target": asdict(emission.target),
                      "telemetry": {"timing_ms": timings, "peak_rss_kb": peak_rss_kb()}}
        if build is not None:
            statistics["cache_hits"] = build.cache_hits
            statistics["instrumentation"] = build.instrumentation
            statistics["runtime_toolchain"] = {"command": build.c_compiler, "version": build.c_compiler_version}
        print(json.dumps(statistics, indent=2), file=sys.stdout if command == "check" else sys.stderr)
    return 0

"""Explicit Seed LLVM command orchestration; never selects another backend."""
import json
import sys
from dataclasses import asdict
from compiler.LlvmToolchain import LlvmToolchain
from common.Platform import peak_rss_kb
from error.Error import WysnaError
from tooling.ToolResult import artifact_file, tool_result, write_tool_result


def run_llvm_command(service, checked, command, ir_path, binary_path, profile,
                     json_v1=False, show_stats=False):
    if ((command == "check" and (ir_path is not None or binary_path is not None))
            or (command == "emit-llvm" and (ir_path is None or binary_path is not None))
            or (command == "build" and (binary_path is None or ir_path is not None))):
        raise WysnaError("Conflicting LLVM command outputs", code="W5002", stage="options")
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
            build = toolchain.build(emission, binary_path)
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
            statistics["runtime_toolchain"] = {"command": build.c_compiler, "version": build.c_compiler_version}
        print(json.dumps(statistics, indent=2), file=sys.stdout if command == "check" else sys.stderr)
    return 0

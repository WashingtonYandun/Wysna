"""Application exit and machine-readable run-report protocol."""
from tooling.ToolResult import tool_result, write_tool_result_atomic


class _ApplicationExit(Exception):
    """Carry an interpreted application's requested exit code to the run reporter."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code



def _capture_application_exit(code):
    raise _ApplicationExit(code)



def _set_run_report(report_state, status, tool_exit_code, process_exit_code,
                    *, execution=None, diagnostics=()):
    if report_state is None:
        return
    report_state.update({
        "status": status,
        "tool_exit_code": tool_exit_code,
        "process_exit_code": process_exit_code,
        "execution": execution,
        "diagnostics": list(diagnostics),
    })



def _write_run_report(path, invocation_id, report_state):
    result = tool_result(
        "run",
        report_state["status"],
        report_state["tool_exit_code"],
        process_exit_code=report_state["process_exit_code"],
        execution=report_state["execution"],
        diagnostics=report_state["diagnostics"],
        invocation_id=invocation_id,
    )
    write_tool_result_atomic(result, path)

"""Execution context and command-line argument contracts."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import main
from runtime.ExecutionContext import ExecutionContext


ROOT = Path(__file__).resolve().parents[2]


def test_interpreter_receives_runner_arguments_and_host_context():
    with tempfile.TemporaryDirectory(prefix="wysna_context_") as temporary:
        directory = Path(temporary)
        (directory / "input.txt").write_text("from execution cwd", encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()
        context = ExecutionContext.for_program(
            "wysna",
            ("first", "--literal"),
            cwd=directory,
            env={"WYSNA_COW_METRICS": "0"},
            stdin=io.StringIO("input"),
            stdout=stdout,
            stderr=stderr,
        )

        source = (
            "print(args()[0])\n"
            "print(args()[1])\n"
            "print(args()[2])\n"
            "print(read_file(\"input.txt\"))\n"
            "write_file(\"output.txt\", \"written from context\")\n"
            "eprint(\"context stderr\")\n"
        )
        assert main.run_pipeline(source, file_name="program.wysna",
                                 execution_context=context) == 0

        assert stdout.getvalue() == "wysna\nfirst\n--literal\nOk(from execution cwd)\n"
        assert stderr.getvalue() == "context stderr\n"
        assert (directory / "output.txt").read_text(encoding="utf-8") == "written from context"
        assert context.resolve_path("output.txt") == os.path.join(str(directory), "output.txt")


def test_cli_delimiter_preserves_runtime_arguments():
    with tempfile.TemporaryDirectory(prefix="wysna_cli_args_") as temporary:
        source = Path(temporary) / "args.wysna"
        source.write_text(
            "print(args()[0])\nprint(args()[1])\nprint(args()[2])\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(ROOT / "wysna"), "run", str(source), "--", "first", "--literal"],
            cwd=temporary,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == "wysna\nfirst\n--literal\n"


def test_native_executable_uses_operating_system_arguments():
    with tempfile.TemporaryDirectory(prefix="wysna_native_args_") as temporary:
        source = Path(temporary) / "args.wysna"
        binary = Path(temporary) / "args-program"
        source.write_text("print(args()[0])\nprint(args()[1])\n", encoding="utf-8")

        build = subprocess.run(
            [sys.executable, str(ROOT / "wysna"), "build", str(source), "-o", str(binary)],
            cwd=temporary,
            capture_output=True,
            text=True,
        )
        assert build.returncode == 0, build.stderr

        result = subprocess.run([str(binary), "native-value"], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{binary}\nnative-value\n"


def test_cli_rejects_unknown_options_missing_values_and_extra_sources():
    with tempfile.TemporaryDirectory(prefix="wysna_cli_invalid_") as temporary:
        first = Path(temporary) / "first.wysna"
        second = Path(temporary) / "second.wysna"
        first.write_text("print(1)\n", encoding="utf-8")
        second.write_text("print(2)\n", encoding="utf-8")
        cases = (
            ["run", str(first), "--unknown"],
            ["emit-c", str(first), "-o"],
            ["run", str(first), str(second)],
        )

        for arguments in cases:
            result = subprocess.run(
                [sys.executable, str(ROOT / "wysna"), *arguments],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 2, (arguments, result.stdout, result.stderr)
            assert "Try 'wysna --help' for usage." in result.stderr


if __name__ == "__main__":
    test_interpreter_receives_runner_arguments_and_host_context()
    test_cli_delimiter_preserves_runtime_arguments()
    test_native_executable_uses_operating_system_arguments()
    test_cli_rejects_unknown_options_missing_values_and_extra_sources()
    print("Execution context and CLI argument contracts passed")

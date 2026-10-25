"""Versioned MIR emission through independent Seed and Compiler producers."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from error.Error import WysnaError
from mir.Exchange import import_module


class _UsageError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _protect_sources(output, sources):
    if output is None:
        return
    target = Path(output).resolve()
    for source in sources:
        path = Path(source).resolve()
        if target == path or (target.exists() and path.exists() and os.path.samefile(target, path)):
            raise _UsageError("MIR output must not overwrite a source file")


def emit_mir(path, implementation="seed"):
    """Return verified interchange and the source paths protected from output writes.

    Compiler checks and lowers its source independently. Python only bootstraps the
    executable and verifies the serialized result at this transitional boundary.
    """
    source_path = Path(path).resolve()
    if not source_path.is_file():
        raise WysnaError("Source file could not be read", file=str(source_path),
                         code="W5003", stage="input")
    if implementation == "seed":
        from compiler.CompilerService import CompilerService
        service = CompilerService()
        try:
            checked = service.check_file(source_path)
        except (OSError, UnicodeError) as error:
            raise WysnaError(str(error), file=str(source_path), code="W5003", stage="input") from error
        payload = service.export_mir(checked)
        return payload, tuple(checked.sources)
    if implementation != "compiler":
        raise _UsageError("implementation must be seed or compiler")
    from compiler.Bootstrap import ensure_selfhost_compiler_binary
    from common.Paths import get_repo_root
    executable = ensure_selfhost_compiler_binary(get_repo_root())
    completed = subprocess.run(
        [executable, "--emit-mir", str(source_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if completed.returncode:
        raise _CompilerRejected(completed.stderr or completed.stdout)
    payload = completed.stdout
    import_module(payload)
    return payload, (str(source_path),)


class _CompilerRejected(Exception):
    pass


def _write_atomic(path, payload):
    target = Path(path).resolve()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=target.parent, prefix=".wysna-mir-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_mir_command(argv):
    parser = _Parser(prog="wysna emit-mir", allow_abbrev=False,
                     description="Emit verified wysna.mir/1 interchange. Compiler supports result-cfg-v1.")
    parser.add_argument("source")
    parser.add_argument("--implementation", choices=("seed", "compiler"), default="seed")
    parser.add_argument("-o", "--output")
    try:
        args = parser.parse_args(argv)
        _protect_sources(args.output, (args.source,))
        payload, sources = emit_mir(args.source, args.implementation)
        _protect_sources(args.output, sources)
        if args.output:
            _write_atomic(args.output, payload)
        else:
            sys.stdout.write(payload)
        return 0
    except _UsageError as error:
        print(f"[W5002] wysna emit-mir: {error}", file=sys.stderr)
        return 2
    except _CompilerRejected as error:
        print(str(error).rstrip(), file=sys.stderr)
        return 1
    except WysnaError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        print(f"[W5004] wysna emit-mir: {error}", file=sys.stderr)
        return 1

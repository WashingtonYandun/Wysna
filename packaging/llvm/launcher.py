"""Entry point for the installed Seed LLVM SDK; imports only its bundled Seed."""
import json
import hashlib
import uuid
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'seed'))
os.environ['WYSNA_LLVM_SDK'] = str(ROOT)


# The packager fills this from the canonical ToolResult factory, before Seed imports.
BOOTSTRAP_ERROR_TEMPLATE = None
BOOTSTRAP_FILES = ('compiler/__init__.py', 'compiler/LlvmSdk.py', 'compiler/ArtifactCache.py',
                   'compiler/NativeToolchain.py', 'common/__init__.py', 'common/Platform.py',
                   'error/__init__.py', 'error/Error.py')


def preflight():
    record = json.loads((ROOT / 'llvm-sdk.json').read_text(encoding='utf-8'))
    if not isinstance(record, dict) or not isinstance(record.get('files'), dict):
        raise ValueError('LLVM SDK manifest has no valid file index')
    for relative in BOOTSTRAP_FILES:
        key = 'seed/' + relative
        source = ROOT / key
        expected = record['files'].get(key)
        if (not expected or not source.resolve().is_relative_to(ROOT)
                or hashlib.sha256(source.read_bytes()).hexdigest() != expected):
            raise ValueError('LLVM SDK bootstrap file is corrupt or missing: ' + key)


def failure(command, message, options):
    if '--output-format=json-v1' in options and BOOTSTRAP_ERROR_TEMPLATE is not None:
        result = dict(BOOTSTRAP_ERROR_TEMPLATE)
        result['command'] = command
        result['invocation_id'] = str(uuid.uuid4())
        result['diagnostics'] = [{**result['diagnostics'][0], 'message': str(message)}]
        print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    else:
        print('[W5001] LLVM SDK Error: ' + str(message), file=sys.stderr)
    return 3


def main():
    args = sys.argv[1:]
    options = args[:args.index('--')] if '--' in args else args
    command = args[0] if args and not args[0].startswith('-') else 'run'
    try:
        preflight()
        from compiler.LlvmSdk import LlvmSdk
        from compiler.NativeToolchain import NativeCompilationError
    except (OSError, ValueError, KeyError, TypeError, ImportError, SyntaxError) as error:
        return failure(command, error, options)
    try:
        sdk = LlvmSdk(ROOT)
    except NativeCompilationError as error:
        return failure(command, error.stderr, options)
    if args in (['version'], ['--version'], ['-v']):
        print('Wysna Seed LLVM SDK ' + sdk.record['version'] + ' (LLVM ' + sdk.record['llvm_version'] +
              ', ' + sdk.record['triple'] + ')')
        return 0
    if not args or args in (['--help'], ['-h']):
        print('Wysna Seed LLVM SDK: check, emit-llvm, build, run, version\n'
              'Usage: wysna COMMAND SOURCE [--profile=dev|release|size] [-o OUTPUT]\n'
              'Run:   wysna run SOURCE [--timeout-ms N] [-- ARGUMENTS]\n'
              'Python 3.10+ is required; LLVM and the C compiler/linker are bundled.')
        return 0
    if command not in ('check', 'emit-llvm', 'build', 'run'):
        print('[W5002] Unsupported LLVM SDK command: ' + command, file=sys.stderr)
        return 2
    if any(arg.startswith('--backend=') and arg != '--backend=llvm-experimental' for arg in options):
        print('[W5002] The LLVM SDK uses llvm-experimental; select the separate Seed or native SDK for C99', file=sys.stderr)
        return 2
    args.insert(1, '--backend=llvm-experimental')
    from tooling.CLI import main as cli_main
    cli_main(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

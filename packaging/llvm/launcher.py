"""Entry point for the installed Seed LLVM SDK; imports only its bundled Seed."""
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'seed'))
os.environ['WYSNA_LLVM_SDK'] = str(ROOT)


def main():
    from compiler.LlvmSdk import LlvmSdk
    from compiler.NativeToolchain import NativeCompilationError
    from tooling.ToolResult import diagnostic_record, tool_result, write_tool_result
    args = sys.argv[1:]
    options = args[:args.index('--')] if '--' in args else args
    command = args[0] if args and not args[0].startswith('-') else 'run'
    try:
        sdk = LlvmSdk(ROOT)
    except NativeCompilationError as error:
        if '--output-format=json-v1' in options:
            write_tool_result(tool_result(command, 'toolchain_error', 3,
                              diagnostics=[diagnostic_record('W5001', 'llvm-sdk', error.stderr)]), sys.stdout)
        else:
            print('[W5001] LLVM SDK Error: ' + error.stderr, file=sys.stderr)
        return 3
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

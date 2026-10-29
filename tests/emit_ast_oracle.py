"""Emit the independent AST C99 oracle for the differential test harness."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compiler.CompilerService import CompilerService


def main(arguments):
    if len(arguments) < 2:
        print("usage: emit_ast_oracle.py <source.wysna> <output.c> [--build <binary>] [--sanitize] [--link-lib <library>]...",
              file=sys.stderr)
        return 2
    source_path, output_path = map(Path, arguments[:2])
    binary_path = None
    sanitize = False
    libraries = []
    index = 2
    while index < len(arguments):
        option = arguments[index]
        if option == "--build" and binary_path is None and index + 1 < len(arguments):
            binary_path = Path(arguments[index + 1])
            index += 2
        elif option == "--sanitize" and not sanitize:
            sanitize = True
            index += 1
        elif option == "--link-lib" and index + 1 < len(arguments):
            libraries.append(arguments[index + 1])
            index += 2
        else:
            print(f"invalid test-oracle option: {option}", file=sys.stderr)
            return 2
    service = CompilerService(project_root=source_path.parent)
    checked = service.check_file(source_path)
    emission = service._emit_ast_oracle_for_testing(checked)
    output_path.write_text(emission.source, encoding="utf-8")
    if binary_path is not None:
        from compiler.NativeToolchain import NativeToolchain
        NativeToolchain(ROOT).build(emission.source, binary_path,
                                    sanitize=sanitize, link_libraries=libraries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

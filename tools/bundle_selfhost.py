#!/usr/bin/env python3
"""
tools/bundle_selfhost.py - Deterministic Bundler for Wysna Self-Hosted Compiler.

Concatenates the modular compiler source files from selfhost/src/ into
the unified compilation unit selfhost/compiler.wysna for 3-stage bootstrap fixed-point verification.
"""

import os
import sys

MODULE_ORDER = [
    "01_ast.wysna",
    "02_lexer.wysna",
    "04_context.wysna",
    "03_token_stream.wysna",
    "03_modules.wysna",
    "04_emitter.wysna",
    "05_metadata.wysna",
    "06_parser_expr.wysna",
    "07_parser_decl.wysna",
    "08_checker.wysna",
    "08_checked.wysna",
    "08_semantics.wysna",
    "08_mir.wysna",
    "08_mir_channels.wysna",
    "08_mir_lowering.wysna",
    "08_mir_verifier.wysna",
    "09_main.wysna",
]


def bundle(src_dir: str = "selfhost/src") -> str:
    parts = []
    for mod_name in MODULE_ORDER:
        path = os.path.join(src_dir, mod_name)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing compiler module: {path}")
        with open(path, "r", encoding="utf-8") as f:
            parts.append(f.read())
    return "".join(parts)


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(repo_root, "selfhost", "src")
    target_path = os.path.join(repo_root, "selfhost", "compiler.wysna")

    bundled_code = bundle(src_dir)

    if "--check" in sys.argv:
        if not os.path.exists(target_path):
            print(f"[FAIL] Target file does not exist: {target_path}", file=sys.stderr)
            return 1
        with open(target_path, "r", encoding="utf-8") as f:
            current_code = f.read()
        if bundled_code != current_code:
            print("[FAIL] selfhost/compiler.wysna is out of sync with selfhost/src/!", file=sys.stderr)
            return 1
        print("[PASS] selfhost/compiler.wysna is exactly in sync with selfhost/src/.")
        return 0

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(bundled_code)

    print(f"[OK] Bundled {len(MODULE_ORDER)} modules from selfhost/src/ into {target_path} ({len(bundled_code.splitlines())} lines).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

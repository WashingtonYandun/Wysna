#!/usr/bin/env python3
"""Split a monolithic MIR-emitted C99 source file into balanced translation units."""

from pathlib import Path
import re


_GLOBAL_RE = re.compile(r"^\s*static\s+(WVal\s+[A-Za-z_][A-Za-z0-9_]*)\s*;\s*$")
_FWD_VTABLE_RE = re.compile(r"^\s*static\s+(const\s+WClassVTable\s+[A-Za-z_][A-Za-z0-9_]*)\s*;\s*$")


def _strip_static(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("static "):
        leading = text[: len(text) - len(stripped)]
        return leading + stripped[len("static ") :]
    return text


def _scan_top_level_items(source: str) -> tuple[list[str], list[str]]:
    """Return (preamble_lines, top_level_statements) without breaking inside {...} or literals."""
    preamble: list[str] = []
    items: list[str] = []
    n = len(source)
    i = 0
    depth = 0
    current: list[str] = []
    in_preamble = True

    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""

        # Line comment
        if ch == "/" and nxt == "/":
            end = source.find("\n", i)
            if end == -1:
                end = n
            else:
                end += 1
            chunk = source[i:end]
            if in_preamble and depth == 0 and not "".join(current).strip():
                preamble.append(chunk)
            else:
                current.append(chunk)
            i = end
            continue

        # Block comment
        if ch == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            end = n if end == -1 else end + 2
            chunk = source[i:end]
            if in_preamble and depth == 0 and not "".join(current).strip():
                preamble.append(chunk)
            else:
                current.append(chunk)
            i = end
            continue

        # String or character literal
        if ch in ('"', "'"):
            quote = ch
            start = i
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            current.append(source[start:i])
            continue

        # Preprocessor directive at top level line start
        if depth == 0 and ch == "#" and not "".join(current).strip():
            end = source.find("\n", i)
            while end != -1 and end >= 2 and source[end - 1] == "\\":
                next_end = source.find("\n", end + 1)
                end = n if next_end == -1 else next_end
            if end == -1:
                end = n
            else:
                end += 1
            directive = source[i:end]
            if in_preamble:
                preamble.append(directive)
            else:
                items.append(directive)
            current.clear()
            i = end
            continue

        current.append(ch)
        if ch == "{":
            in_preamble = False
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
            if depth == 0:
                # Look ahead past spaces/newlines to see if this closes a brace-init declaration (`};`) or a function body (`}`)
                j = i + 1
                while j < n and source[j] in " \t\r\n":
                    j += 1
                if j < n and source[j] == ";":
                    current.append(source[i + 1 : j + 1])
                    i = j
                stmt = "".join(current).strip()
                if stmt:
                    items.append(stmt)
                current.clear()
        elif depth == 0 and ch == ";":
            in_preamble = False
            stmt = "".join(current).strip()
            if stmt:
                items.append(stmt)
            current.clear()
        elif depth == 0 and ch == "\n" and not "".join(current).strip():
            current.clear()
        else:
            if depth == 0 and not ch.isspace():
                in_preamble = False

        i += 1

    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return preamble, items


def _find_top_level_char(text: str, target: str) -> int:
    """Find first occurrence of target outside quotes and parentheses/brackets."""
    n = len(text)
    i = 0
    paren = 0
    bracket = 0
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch == "/" and nxt == "/":
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
            continue
        if ch in ('"', "'"):
            q = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if ch == target and paren == 0 and bracket == 0:
            return i
        if ch == "(":
            paren += 1
        elif ch == ")" and paren > 0:
            paren -= 1
        elif ch == "[":
            bracket += 1
        elif ch == "]" and bracket > 0:
            bracket -= 1
        i += 1
    return -1


def split_c_translation_unit(source_path: Path, output_dir: Path, chunks: int = 8) -> tuple[Path, list[Path]]:
    """Split a generated MIR C99 file into compiler_common.h + N balanced C translation units."""
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    text = source_path.read_text(encoding="utf-8")
    preamble, items = _scan_top_level_items(text)

    header_decls: list[str] = []
    seen_header_decls: set[str] = set()
    data_defs: list[str] = []
    fn_defs: list[tuple[int, str]] = []

    def add_header_decl(decl: str) -> None:
        normalized = " ".join(decl.split())
        if normalized not in seen_header_decls:
            seen_header_decls.add(normalized)
            header_decls.append(decl.rstrip() + "\n")

    for item in items:
        s = item.strip()
        if not s:
            continue
        if s.startswith("#"):
            header_decls.append(s + "\n")
            continue

        brace_pos = _find_top_level_char(s, "{")
        eq_pos = _find_top_level_char(s, "=")

        # Function definition: top-level `{` before any `=`, and signature contains `(`
        if brace_pos != -1 and (eq_pos == -1 or brace_pos < eq_pos):
            sig = _strip_static(s[:brace_pos].strip())
            if "(" in sig:
                if not sig.startswith("int main("):
                    add_header_decl(sig + ";")
                body = s[brace_pos:]
                fn_text = f"{sig} {body}\n"
                fn_defs.append((fn_text.count("\n") + 1, fn_text))
                continue

        # Data definition with initializer (`static const ... = ...;`)
        if eq_pos != -1:
            decl_only = _strip_static(s[:eq_pos].strip())
            add_header_decl(f"extern {decl_only};")
            data_defs.append(_strip_static(s) + "\n")
            continue

        # Forward declaration or extern declaration ending with `;`
        m_global = _GLOBAL_RE.match(s)
        if m_global:
            inner = m_global.group(1)
            add_header_decl(f"extern {inner};")
            data_defs.append(f"{inner};\n")
            continue

        m_vtable = _FWD_VTABLE_RE.match(s)
        if m_vtable:
            add_header_decl(f"extern {m_vtable.group(1)};")
            continue

        stripped = _strip_static(s)
        if s.lstrip().startswith("static ") and "(" not in s:
            add_header_decl(f"extern {stripped}")
        else:
            add_header_decl(stripped)

    header_path = output_dir / "compiler_common.h"
    header_parts = [
        "#ifndef WYSNA_SPLIT_COMMON_H\n#define WYSNA_SPLIT_COMMON_H\n",
        "".join(preamble),
        "\n",
        "".join(header_decls),
        "\n#endif /* WYSNA_SPLIT_COMMON_H */\n",
    ]
    header_path.write_text("".join(header_parts), encoding="utf-8")

    c_files: list[Path] = []
    if data_defs:
        defs_path = output_dir / "compiler_defs.c"
        defs_path.write_text('#include "compiler_common.h"\n\n' + "".join(data_defs), encoding="utf-8")
        c_files.append(defs_path)

    target_chunks = max(1, min(chunks, len(fn_defs))) if fn_defs else 1
    buckets: list[list[str]] = [[] for _ in range(target_chunks)]
    bucket_lines = [0] * target_chunks
    # Greedy longest-processing-time-first balancing keeps parallel `-O2` compile times even.
    for line_count, fn_text in sorted(fn_defs, key=lambda pair: -pair[0]):
        smallest = min(range(target_chunks), key=lambda idx: bucket_lines[idx])
        buckets[smallest].append(fn_text)
        bucket_lines[smallest] += line_count

    for idx, bucket in enumerate(buckets):
        if not bucket and c_files:
            continue
        chunk_path = output_dir / f"compiler_chunk_{idx:02d}.c"
        chunk_path.write_text('#include "compiler_common.h"\n\n' + "".join(bucket), encoding="utf-8")
        c_files.append(chunk_path)

    return header_path, c_files

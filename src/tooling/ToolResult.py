"""Versioned machine-readable results for Wysna CLI tools."""

import hashlib
import json
import os
import tempfile
import uuid

from common.Version import WYSNA_VERSION
from error.Error import WysnaError


SCHEMA_VERSION = "wysna.tool-result/1"
_DEFAULT_PROCESS_EXIT_CODE = object()


def _line_starts(source):
    starts = [0]
    index = 0
    while index < len(source):
        if source[index] == "\r":
            index += 2 if source[index:index + 2] == "\r\n" else 1
            starts.append(index)
        elif source[index] == "\n":
            index += 1
            starts.append(index)
        else:
            index += 1
    return starts


def _character_offset(source, line, column):
    """Map a one-based Unicode-scalar position to a Python string offset."""
    if not isinstance(line, int) or not isinstance(column, int) or line < 1 or column < 1:
        return None

    starts = _line_starts(source)
    if line > len(starts):
        return None

    start = starts[line - 1]
    end = start
    while end < len(source) and source[end] not in "\r\n":
        end += 1

    return min(start + column - 1, end)


def _byte_offset(source, line, column):
    """Map a one-based Unicode-scalar position to its UTF-8 byte offset."""
    character_offset = _character_offset(source, line, column)
    if character_offset is None:
        return None
    try:
        return len(source[:character_offset].encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _character_span(source, line, column):
    """Return the scalar at a lexer-reported position, or an exact EOF point."""
    char_offset = _character_offset(source, line, column)
    if char_offset is None:
        return None
    try:
        start_byte = len(source[:char_offset].encode("utf-8"))
    except UnicodeEncodeError:
        return None
    if char_offset >= len(source) or source[char_offset] in "\r\n":
        return start_byte, start_byte
    end_byte = start_byte + len(source[char_offset].encode("utf-8"))
    return start_byte, end_byte


def _token_span(source, filename, line, column, source_sha256, span_cache):
    """Resolve a diagnostic location to the exact token from its source snapshot."""
    key = (filename, source_sha256)
    if key not in span_cache:
        try:
            from lexer.Lexer import Lexer

            tokens = Lexer(source, file=filename).tokenize()
            index = {}
            for token in tokens:
                if not hasattr(token, "start_offset") or not hasattr(token, "end_offset"):
                    continue
                key_position = (token.line, token.col)
                if token.type.name == "EOF":
                    index.setdefault(key_position, (token.start_offset, token.end_offset))
                else:
                    index[key_position] = (token.start_offset, token.end_offset)
            span_cache[key] = index
        except (WysnaError, UnicodeError, ValueError, RuntimeError):
            # Lexer diagnostics cannot be indexed as a complete token stream.
            span_cache[key] = None

    index = span_cache[key]
    if index is None:
        return _character_span(source, line, column)
    char_span = index.get((line, column))
    if char_span is None:
        return None
    start, end = char_span
    try:
        return len(source[:start].encode("utf-8")), len(source[:end].encode("utf-8"))
    except UnicodeEncodeError:
        return None


def source_for_file(source_by_file, filename):
    if not filename:
        return None
    source = source_by_file.get(filename)
    if source is not None:
        return source
    try:
        for candidate in (os.path.abspath(filename), os.path.realpath(filename)):
            source = source_by_file.get(candidate)
            if source is not None:
                return source
        # A caller may have keyed the snapshot by a symlinked spelling of the same
        # file (for example /tmp vs /private/tmp on macOS); compare real paths.
        wanted = os.path.realpath(filename)
        for key, source in source_by_file.items():
            if isinstance(key, str) and os.path.realpath(key) == wanted:
                return source
    except (OSError, TypeError, ValueError):
        pass
    return None


def _primary_span(filename, line, column, source, *, has_location=True,
                  explicit_start_byte=None, explicit_end_byte=None, span_cache=None):
    if not filename or source is None:
        return None
    try:
        source_bytes = source.encode("utf-8")
    except (AttributeError, UnicodeEncodeError):
        return None
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    span = None
    if (isinstance(explicit_start_byte, int) and isinstance(explicit_end_byte, int)
            and 0 <= explicit_start_byte <= explicit_end_byte <= len(source_bytes)):
        try:
            source_bytes[:explicit_start_byte].decode("utf-8")
            source_bytes[:explicit_end_byte].decode("utf-8")
            span = (explicit_start_byte, explicit_end_byte)
        except UnicodeDecodeError:
            pass
    if span is None and has_location:
        span = _token_span(source, filename, line, column, source_sha256,
                           span_cache if span_cache is not None else {})
    if span is None:
        return None
    return {
        "source_id": filename,
        "source_sha256": source_sha256,
        "start_byte": span[0],
        "end_byte": span[1],
    }


def _resolve_fixes(fixes, filename, source):
    """Turn line/column/old-text edits into byte-range edits over one snapshot.

    An edit is dropped, never guessed, when the expected old text is not found on
    the reported line; an incorrect machine edit is worse than no edit.
    """
    if not fixes or source is None or not filename:
        return []
    try:
        source_bytes = source.encode("utf-8")
    except (AttributeError, UnicodeEncodeError):
        return []
    sha = hashlib.sha256(source_bytes).hexdigest()
    resolved = []
    for fix in fixes:
        edits = []
        for edit in fix.get("edits", ()):
            start = _character_offset(source, edit.get("line"), edit.get("col"))
            old = edit.get("old", "")
            if start is None:
                edits = None
                break
            line_end = start
            while line_end < len(source) and source[line_end] not in "\r\n":
                line_end += 1
            index = source.find(old, start, line_end) if old else start
            if index < 0:
                edits = None
                break
            start_byte = len(source[:index].encode("utf-8"))
            edits.append({
                "source_id": filename,
                "source_sha256": sha,
                "start_byte": start_byte,
                "end_byte": start_byte + len(old.encode("utf-8")),
                "replacement": edit.get("new", ""),
            })
        if edits:
            resolved.append({
                "description": fix.get("description", ""),
                "applicability": fix.get("applicability", "maybe"),
                "edits": edits,
            })
    return resolved


def _resolve_related(related, default_filename, source_by_file, span_cache):
    """Resolve secondary diagnostic locations against the snapshot map."""
    if not related:
        return []
    records = []
    for item in related:
        rel_file = item.get("file") or default_filename
        rel_source = source_for_file(source_by_file, rel_file)
        line = item.get("line")
        col = item.get("col")
        records.append({
            "message": item.get("message", ""),
            "line": line,
            "col": col,
            "span": _primary_span(
                rel_file,
                line,
                col,
                rel_source,
                has_location=line is not None and col is not None,
                span_cache=span_cache,
            ),
        })
    return records


def _match_open_token(tokens, close_idx, open_name, close_name):
    depth = 1
    pos = close_idx - 1
    while pos >= 0:
        tname = tokens[pos].type.name
        if tname == close_name:
            depth += 1
        elif tname == open_name:
            depth -= 1
            if depth == 0:
                return pos
        pos -= 1
    return -1


def _match_close_token(tokens, open_idx, open_name, close_name):
    depth = 1
    pos = open_idx + 1
    while pos < len(tokens):
        tname = tokens[pos].type.name
        if tname == open_name:
            depth += 1
        elif tname == close_name:
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    return -1


def _synthesize_unwrap_fixes(error, filename, source):
    """Synthesize a conservative 'maybe' '?' unwrap/propagation fix for W2001 hints."""
    line = getattr(error, "line", None)
    col = getattr(error, "col", None)
    if not isinstance(line, int) or not isinstance(col, int):
        return []
    try:
        from lexer.Lexer import Lexer

        tokens = [
            tok for tok in Lexer(source, file=filename).tokenize()
            if tok.type.name not in ("NEWLINE", "EOF")
        ]
    except (WysnaError, UnicodeError, ValueError, RuntimeError):
        return []

    idx = next((i for i, tok in enumerate(tokens) if tok.line == line and tok.col == col), -1)
    if idx < 0:
        return []

    tok_name = tokens[idx].type.name
    if tok_name in ("IDENTIFIER", "THIS", "SUPER"):
        start_idx = idx
    elif tok_name in ("LPAREN", "LBRACKET", "DOT"):
        cur = idx
        while cur > 0:
            if cur >= 2 and tokens[cur - 2].type.name == "DOT" and tokens[cur - 1].type.name in ("IDENTIFIER", "INT"):
                cur -= 2
            elif tokens[cur - 1].type.name in ("IDENTIFIER", "THIS", "SUPER"):
                cur -= 1
                break
            elif tokens[cur - 1].type.name in ("RPAREN", "RBRACKET"):
                open_idx = _match_open_token(
                    tokens, cur - 1,
                    "LPAREN" if tokens[cur - 1].type.name == "RPAREN" else "LBRACKET",
                    tokens[cur - 1].type.name,
                )
                if open_idx < 0:
                    return []
                cur = open_idx
            else:
                break
        if cur == idx and tok_name == "DOT":
            return []
        if tokens[cur].type.name not in ("IDENTIFIER", "THIS", "SUPER", "LPAREN"):
            return []
        start_idx = cur
    else:
        return []

    end_idx = start_idx
    if tokens[start_idx].type.name == "LPAREN":
        close_idx = _match_close_token(tokens, start_idx, "LPAREN", "RPAREN")
        if close_idx < 0:
            return []
        end_idx = close_idx
        j = close_idx + 1
    else:
        j = start_idx + 1

    while j < len(tokens):
        jname = tokens[j].type.name
        if jname == "DOT" and j + 1 < len(tokens) and tokens[j + 1].type.name in ("IDENTIFIER", "INT"):
            end_idx = j + 1
            j += 2
        elif jname in ("LPAREN", "LBRACKET"):
            close_name = "RPAREN" if jname == "LPAREN" else "RBRACKET"
            close_idx = _match_close_token(tokens, j, jname, close_name)
            if close_idx < 0:
                return []
            end_idx = close_idx
            j = close_idx + 1
        else:
            break

    if end_idx < idx or (j < len(tokens) and tokens[j].type.name == "QUESTION"):
        return []

    start_off = getattr(tokens[start_idx], "start_offset", None)
    end_off = getattr(tokens[end_idx], "end_offset", None)
    if not isinstance(start_off, int) or not isinstance(end_off, int) or not (0 <= start_off < end_off <= len(source)):
        return []

    orig = source[start_off:end_off]
    try:
        sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        start_byte = len(source[:start_off].encode("utf-8"))
        end_byte = len(source[:end_off].encode("utf-8"))
    except UnicodeEncodeError:
        return []

    return [{
        "description": f"append '?' to '{orig}' to propagate failure by returning early",
        "applicability": "maybe",
        "edits": [{
            "source_id": filename,
            "source_sha256": sha,
            "start_byte": start_byte,
            "end_byte": end_byte,
            "replacement": orig + "?",
        }],
    }]


_BINARY_LEFT_DISALLOWED = frozenset({
    "return", "if", "elif", "else", "while", "for", "match",
    "and", "or", "not", "in", "let", "mut",
})
_UNARY_BANG_LEFT_KEYWORDS = frozenset({
    "if", "elif", "while", "return", "and", "or", "not", "in", "else",
})


def _is_ascii_ident_byte(byte_val):
    return (
        byte_val == 95
        or 48 <= byte_val <= 57
        or 65 <= byte_val <= 90
        or 97 <= byte_val <= 122
    )


def _pad_binary_keyword(src_bytes, start, end, keyword):
    prev_b = src_bytes[start - 1:start] if start > 0 else b""
    next_b = src_bytes[end:end + 1] if end < len(src_bytes) else b""
    prefix = "" if (start == 0 or prev_b in (b" ", b"\t", b"\r", b"\n", b"(", b"[", b",")) else " "
    suffix = "" if (end >= len(src_bytes) or next_b in (b" ", b"\t", b"\r", b"\n", b")", b"]", b",", b";")) else " "
    return prefix + keyword + suffix


def _synthesize_syntax_fixes(code, message, filename, source, primary_span):
    """Build deterministic machine fixes for common foreign-syntax slips."""
    if not filename or source is None or primary_span is None:
        return []
    sb = primary_span.get("start_byte")
    eb = primary_span.get("end_byte")
    sha = primary_span.get("source_sha256")
    if not isinstance(sb, int) or not isinstance(eb, int) or not sha:
        return []
    try:
        src_bytes = source.encode("utf-8")
    except UnicodeEncodeError:
        return []
    n = len(src_bytes)

    def make_fix(description, start, end, replacement):
        return [{
            "description": description,
            "applicability": "machine",
            "edits": [{
                "source_id": filename,
                "source_sha256": sha,
                "start_byte": start,
                "end_byte": end,
                "replacement": replacement,
            }],
        }]

    if code == "W0001" and 0 <= sb and sb + 2 <= n and src_bytes[sb:sb + 2] == b"&&":
        return make_fix("replace '&&' with 'and'", sb, sb + 2, _pad_binary_keyword(src_bytes, sb, sb + 2, "and"))

    if code == "W0006" and (
        "Expected '{'" in message
        or "Expected ')'" in message
        or "expected LBRACE" in message
        or "expected RPAREN" in message
    ):
        pipe_start = -1
        if 0 <= sb and sb + 2 <= n and src_bytes[sb:sb + 2] == b"||":
            pipe_start = sb
        elif sb >= 1 and sb + 1 <= n and src_bytes[sb - 1:sb + 1] == b"||":
            pipe_start = sb - 1
        else:
            j = sb
            while j > 0 and src_bytes[j - 1:j] in (b" ", b"\t"):
                j -= 1
            if j >= 2 and src_bytes[j - 2:j] == b"||":
                pipe_start = j - 2
        if pipe_start >= 0:
            left_end = pipe_start
            while left_end > 0 and src_bytes[left_end - 1:left_end] in (b" ", b"\t"):
                left_end -= 1
            left_ok = False
            if left_end > 0:
                lb = src_bytes[left_end - 1]
                if lb in (41, 93, 34, 39) or (48 <= lb <= 57):  # ), ], ", ', 0-9
                    left_ok = True
                elif _is_ascii_ident_byte(lb):
                    wstart = left_end - 1
                    while wstart > 0 and _is_ascii_ident_byte(src_bytes[wstart - 1]):
                        wstart -= 1
                    word = src_bytes[wstart:left_end].decode("ascii", "ignore")
                    left_ok = word not in _BINARY_LEFT_DISALLOWED
            right_start = pipe_start + 2
            while right_start < n and src_bytes[right_start:right_start + 1] in (b" ", b"\t"):
                right_start += 1
            right_ok = (
                right_start < n
                and src_bytes[right_start:right_start + 1] not in (b"\r", b"\n", b"{", b",", b")", b"]")
                and src_bytes[right_start:right_start + 2] not in (b"=>", b"->")
            )
            if left_ok and right_ok:
                return make_fix(
                    "replace '||' with 'or'",
                    pipe_start,
                    pipe_start + 2,
                    _pad_binary_keyword(src_bytes, pipe_start, pipe_start + 2, "or"),
                )

    bang_pos = -1
    if code in ("W0001", "W0006") and 0 <= sb and sb + 1 <= n and src_bytes[sb:sb + 1] == b"!":
        if sb + 1 >= n or src_bytes[sb + 1:sb + 2] != b"=":
            bang_pos = sb
    elif code == "W2004" and 0 <= sb <= eb <= n and src_bytes[sb:eb] == b"return" and "found 'unit'" in message:
        k = eb
        while k < n and src_bytes[k:k + 1] in (b" ", b"\t"):
            k += 1
        if k < n and src_bytes[k:k + 1] == b"!" and (k + 1 >= n or src_bytes[k + 1:k + 2] != b"="):
            bang_pos = k
    if bang_pos >= 0:
        left_end = bang_pos
        while left_end > 0 and src_bytes[left_end - 1:left_end] in (b" ", b"\t", b"\r", b"\n"):
            left_end -= 1
        prefix_ok = False
        if left_end == 0:
            prefix_ok = True
        elif src_bytes[left_end - 1:left_end] in (
            b"(", b"[", b"{", b",", b"=", b":", b";", b"<", b"+", b"-", b"*", b"/", b"%", b"!",
        ):
            prefix_ok = True
        elif left_end >= 2 and src_bytes[left_end - 2:left_end] == b"=>":
            prefix_ok = True
        elif _is_ascii_ident_byte(src_bytes[left_end - 1]):
            wstart = left_end - 1
            while wstart > 0 and _is_ascii_ident_byte(src_bytes[wstart - 1]):
                wstart -= 1
            word = src_bytes[wstart:left_end].decode("ascii", "ignore")
            prefix_ok = word in _UNARY_BANG_LEFT_KEYWORDS
        if prefix_ok:
            prev_b = src_bytes[bang_pos - 1:bang_pos] if bang_pos > 0 else b""
            prefix = "" if (bang_pos == 0 or prev_b in (b" ", b"\t", b"\r", b"\n", b"(", b"[", b"{", b",")) else " "
            next_ws = bang_pos + 1 < n and src_bytes[bang_pos + 1:bang_pos + 2] in (b" ", b"\t")
            return make_fix(
                "replace '!' with 'not'",
                bang_pos,
                bang_pos + 1,
                prefix + ("not" if next_ws else "not "),
            )

    if code == "W0006" and message.startswith("Use '"):
        for old, new in ((b"public", "pub"), (b"private", "priv")):
            if message == f"Use '{new}' in place of '{old.decode()}'" and src_bytes[sb:sb + len(old)] == old:
                return make_fix(message, sb, sb + len(old), new)

    return []


def diagnostic_from_error(error, source_by_file=None, *, span_cache=None):
    """Adapt a legacy compiler diagnostic without inventing an unknown range."""
    from checker.Suggestions import _clean_symbol_name, _demangle_type_text, enrich_from_message

    source_by_file = source_by_file or {}
    filename = getattr(error, "file", None)
    source = source_for_file(source_by_file, filename)
    if getattr(error, "stage", None) == "type" and not getattr(error, "data", None):
        enrich_from_message(error)
    data = dict(getattr(error, "data", None) or {})
    for key in ("expected", "found", "subject_type"):
        if isinstance(data.get(key), str):
            data[key] = _demangle_type_text(data[key])
    for key in ("callee", "function", "name", "symbol", "binding"):
        if isinstance(data.get(key), str):
            data[key] = _clean_symbol_name(data[key])
    if getattr(error, "help", None):
        data["help"] = error.help

    primary_span = _primary_span(
        filename,
        getattr(error, "line", None),
        getattr(error, "col", None),
        source,
        has_location=getattr(error, "has_location", True),
        explicit_start_byte=getattr(error, "start_byte", None),
        explicit_end_byte=getattr(error, "end_byte", None),
        span_cache=span_cache,
    )
    fixes = _resolve_fixes(getattr(error, "fixes", None), filename, source)
    if not fixes and data.get("unwrap_hint") == "?" and source is not None and filename:
        fixes = _synthesize_unwrap_fixes(error, filename, source)
    if not fixes and primary_span is not None and source is not None and filename:
        fixes = _synthesize_syntax_fixes(
            getattr(error, "code", "W9999"),
            getattr(error, "message", str(error)),
            filename,
            source,
            primary_span,
        )

    return {
        "code": getattr(error, "code", "W9999"),
        "severity": getattr(error, "severity", "error"),
        "stage": getattr(error, "stage", "internal"),
        "message": getattr(error, "message", str(error)),
        "primary_span": primary_span,
        "related": _resolve_related(getattr(error, "related", None), filename, source_by_file, span_cache),
        "data": data,
        "fixes": fixes,
    }


def diagnostic_record(code, stage, message, *, severity="error", filename=None,
                      line=None, column=None, source=None, span_cache=None):
    """Create a protocol diagnostic for CLI and host errors."""
    return {
        "code": code,
        "severity": severity,
        "stage": stage,
        "message": message,
        "primary_span": _primary_span(filename, line, column, source,
                                      has_location=line is not None and column is not None,
                                      span_cache=span_cache),
        "related": [],
        "data": {},
        "fixes": [],
    }


def artifact_inline(kind, media_type, content):
    """Describe a UTF-8 text artifact embedded in the result envelope."""
    encoded = content.encode("utf-8")
    return {
        "kind": kind,
        "media_type": media_type,
        "content": content,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def artifact_file(kind, media_type, path):
    """Describe a completed file artifact using the bytes actually written."""
    path = os.fspath(path)
    digest = hashlib.sha256()
    with open(path, "rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "kind": kind,
        "media_type": media_type,
        "path": path,
        "sha256": digest.hexdigest(),
    }


def tool_result(command, status, exit_code, *, diagnostics=None, artifacts=None,
                invocation_id=None, target=None,
                process_exit_code=_DEFAULT_PROCESS_EXIT_CODE, execution=None):
    """Build the stable v1 envelope for stdout results and run sidecars."""
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "status": status,
        "invocation_id": invocation_id or str(uuid.uuid4()),
        "tool_exit_code": exit_code,
        "process_exit_code": (
            exit_code if process_exit_code is _DEFAULT_PROCESS_EXIT_CODE
            else process_exit_code
        ),
        "execution": execution,
        "toolchain": {"compiler": WYSNA_VERSION},
        "target": target or {"profile": "native-cli", "triple": None},
        "diagnostics": diagnostics or [],
        "artifacts": artifacts or [],
        "telemetry": None,
    }


def write_tool_result(result, stream):
    """Write exactly one JSON document to the selected stream."""
    stream.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_tool_result_atomic(result, path):
    """Atomically replace a JSON report using a temporary file beside its target."""
    target = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(target)
    descriptor = None
    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(target)}.",
            suffix=".tmp",
            dir=directory,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            write_tool_result(result, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

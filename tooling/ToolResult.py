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


def _source_for_file(source_by_file, filename):
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


def diagnostic_from_error(error, source_by_file=None, *, span_cache=None):
    """Adapt a legacy compiler diagnostic without inventing an unknown range."""
    source_by_file = source_by_file or {}
    filename = getattr(error, "file", None)
    source = _source_for_file(source_by_file, filename)

    return {
        "code": getattr(error, "code", "W9999"),
        "severity": getattr(error, "severity", "error"),
        "stage": getattr(error, "stage", "internal"),
        "message": getattr(error, "message", str(error)),
        "primary_span": _primary_span(
            filename,
            getattr(error, "line", None),
            getattr(error, "col", None),
            source,
            has_location=getattr(error, "has_location", True),
            explicit_start_byte=getattr(error, "start_byte", None),
            explicit_end_byte=getattr(error, "end_byte", None),
            span_cache=span_cache,
        ),
        "related": [],
        "data": {},
        "fixes": [],
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

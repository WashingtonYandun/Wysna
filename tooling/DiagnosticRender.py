"""Human-readable diagnostics over the same source snapshots as json-v1."""

import re
import unicodedata

from tooling.ToolResult import diagnostic_from_error, source_for_file


def _display(text):
    """Expand tabs and replace terminal control characters in source excerpts."""
    result = []
    width = 0
    for char in text:
        if char == "\t":
            spaces = 4 - width % 4
            result.append(" " * spaces)
            width += spaces
        elif unicodedata.category(char).startswith("C"):
            result.append("\ufffd")
            width += 1
        else:
            result.append(char)
            if not unicodedata.combining(char):
                width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return "".join(result), width


def render_diagnostic(error, source_by_file, *, span_cache=None):
    """Render a diagnostic without inventing locations or rereading files."""
    if getattr(error, "has_location", True):
        header = str(error)
    else:
        header = f"[{error.code}] {error.file} - {error.message}"
    record = diagnostic_from_error(error, source_by_file, span_cache=span_cache)
    span = record["primary_span"]
    if span is None:
        help_text = record["data"].get("help")
        return f"{header}\n  = help: {help_text}" if help_text else header
    source = source_for_file(source_by_file, span["source_id"])
    if source is None:
        return header
    encoded = source.encode("utf-8")
    try:
        prefix = encoded[:span["start_byte"]].decode("utf-8")
        marked = encoded[span["start_byte"]:span["end_byte"]].decode("utf-8")
    except UnicodeError:
        return header
    before = re.split(r"\r\n|\r|\n", prefix)
    line_number = len(before)
    lines = re.split(r"\r\n|\r|\n", source)
    if line_number > len(lines):
        return header
    line = lines[line_number - 1]
    column = len(before[-1])
    # Bound excerpts and never emit source control sequences into the terminal.
    window_start = max(0, column - 80)
    window_end = min(len(line), max(column + 80, window_start + 160))
    excerpt = line[window_start:window_end]
    shown, shown_width = _display(excerpt)
    _, indent = _display(line[window_start:column])
    marked_line = re.split(r"\r\n|\r|\n", marked, maxsplit=1)[0]
    _, mark_width = _display(marked_line)
    mark_width = max(1, min(mark_width, 80, shown_width - indent or 1))
    left = "..." if window_start else ""
    right = "..." if window_end < len(line) else ""
    gutter = str(line_number)
    rendered = (f"{header}\n"
                f" {' ' * len(gutter)} |\n"
                f" {gutter} | {left}{shown}{right}\n"
                f" {' ' * len(gutter)} | {' ' * (len(left) + indent)}{'^' * mark_width}")
    help_text = record["data"].get("help")
    if help_text:
        rendered += f"\n {' ' * len(gutter)} = help: {help_text}"
    return rendered

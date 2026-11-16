"""Preserve line and block comments by anchoring source trivia to corresponding tokens."""
from difflib import SequenceMatcher
from lexer.Lexer import Lexer


def _scan_comments(source):
    """Collect (start_line, start_col, end_line, text) tuples outside string literals."""
    comments = []
    n = len(source)
    i = 0
    line = 1
    col = 1
    quoted = False
    escaped = False
    while i < n:
        ch = source[i]
        if quoted:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                quoted = False
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue
        if ch == '"':
            quoted = True
            i += 1
            col += 1
            continue
        if ch == '#':
            start = i
            s_line, s_col = line, col
            while i < n and source[i] not in ('\n', '\r'):
                i += 1
                col += 1
            comments.append((s_line, s_col, s_line, source[start:i].rstrip()))
            continue
        if ch == '/' and i + 1 < n and source[i + 1] == '*':
            start = i
            s_line, s_col = line, col
            depth = 1
            i += 2
            col += 2
            while i < n and depth > 0:
                if source[i] == '/' and i + 1 < n and source[i + 1] == '*':
                    depth += 1
                    i += 2
                    col += 2
                elif source[i] == '*' and i + 1 < n and source[i + 1] == '/':
                    depth -= 1
                    i += 2
                    col += 2
                elif source[i] == '\n':
                    line += 1
                    col = 1
                    i += 1
                else:
                    i += 1
                    col += 1
            comments.append((s_line, s_col, line, source[start:i].rstrip()))
            continue
        if ch == '\n':
            line += 1
            col = 1
        else:
            col += 1
        i += 1
    return comments


def preserve_comments(source, formatted):
    comments = _scan_comments(source)
    if not comments:
        return formatted
    original = Lexer(source).tokenize()[:-1]
    output = Lexer(formatted).tokenize()[:-1]

    def key(token):
        return (token.type.name, str(token.value))

    matcher = SequenceMatcher(a=[key(t) for t in original], b=[key(t) for t in output], autojunk=False)
    mapping = {}
    for a, b, size in matcher.get_matching_blocks():
        for offset in range(size):
            mapping[a + offset] = b + offset
    lines = formatted.splitlines()
    before, inline = {}, {}
    for line, column, end_line, text in comments:
        preceding = [i for i, token in enumerate(original) if (token.line, token.col) < (line, column)]
        previous = preceding[-1] if preceding else None
        following = [i for i in mapping if (original[i].line, original[i].col) > (line, column)
                     and original[i].type.name != 'NEWLINE']
        has_same_line_after = bool(following and original[following[0]].line == end_line)
        if ('\n' not in text and previous is not None and original[previous].line == line
                and previous in mapping and not has_same_line_after):
            target = output[mapping[previous]].line - 1
            inline.setdefault(target, []).append(text)
        else:
            all_following = [i for i in mapping if (original[i].line, original[i].col) > (line, column)]
            target = output[mapping[min(all_following)]].line - 1 if all_following else len(lines)
            before.setdefault(target, []).append(text)
    rendered = []
    for index in range(len(lines) + 1):
        indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())] if index < len(lines) else ''
        for text in before.get(index, []):
            if '\n' in text:
                part_lines = text.splitlines()
                rendered.append(indent + part_lines[0])
                for extra in part_lines[1:]:
                    rendered.append(extra)
            else:
                rendered.append(indent + text)
        if index < len(lines):
            rendered.append(lines[index] + (' ' + ' '.join(inline[index]) if index in inline else ''))
    return '\n'.join(rendered) + '\n'

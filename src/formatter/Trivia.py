"""Preserve line comments by anchoring source trivia to corresponding tokens."""
from difflib import SequenceMatcher
from lexer.Lexer import Lexer


def preserve_comments(source, formatted):
    comments = []
    for line_number, line in enumerate(source.splitlines(), 1):
        quoted = escaped = False
        for column, char in enumerate(line):
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == '#':
                comments.append((line_number, column + 1, line[column:].rstrip()))
                break
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
    for line, column, text in comments:
        preceding = [i for i, token in enumerate(original) if (token.line, token.col) < (line, column)]
        previous = preceding[-1] if preceding else None
        if previous is not None and original[previous].line == line and previous in mapping:
            target = output[mapping[previous]].line - 1
            inline.setdefault(target, []).append(text)
        else:
            following = [i for i in mapping if (original[i].line, original[i].col) > (line, column)]
            target = output[mapping[min(following)]].line - 1 if following else len(lines)
            before.setdefault(target, []).append(text)
    rendered = []
    for index in range(len(lines) + 1):
        indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())] if index < len(lines) else ''
        rendered.extend(indent + text for text in before.get(index, []))
        if index < len(lines):
            rendered.append(lines[index] + (' ' + ' '.join(inline[index]) if index in inline else ''))
    return '\n'.join(rendered) + '\n'

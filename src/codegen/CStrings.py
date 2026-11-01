"""C99 string escaping, including NUL boundaries and trigraph avoidance."""


def quote(value):
    escaped = {'"': '\\"', '\\': '\\\\', '\n': '\\n', '\r': '\\r', '\t': '\\t',
               '\0': '\\000', '?': '\\077'}
    return '"' + ''.join(escaped.get(character, character) for character in value) + '"'

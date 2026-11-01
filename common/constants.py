from tokens.TokenType import TokenType

KEYWORDS = {
    "pub": TokenType.PUB,
    "extern": TokenType.EXTERN,
    # Declarations (6)
    "fn": TokenType.FN,
    "let": TokenType.LET,
    "mut": TokenType.MUT,
    "struct": TokenType.STRUCT,
    "enum": TokenType.ENUM,
    "import": TokenType.IMPORT,
    "from": TokenType.FROM,

    # Object-Oriented (9)
    "class": TokenType.CLASS,
    "interface": TokenType.INTERFACE,
    "extends": TokenType.EXTENDS,
    "implements": TokenType.IMPLEMENTS,
    "new": TokenType.NEW,
    "this": TokenType.THIS,
    "super": TokenType.SUPER,
    "public": TokenType.PUBLIC,
    "private": TokenType.PRIVATE,

    # Control Flow (10)
    "if": TokenType.IF,
    "elif": TokenType.ELIF,
    "else": TokenType.ELSE,
    "for": TokenType.FOR,
    "while": TokenType.WHILE,
    "in": TokenType.IN,
    "match": TokenType.MATCH,
    "return": TokenType.RETURN,
    "break": TokenType.BREAK,
    "continue": TokenType.CONTINUE,

    # Primitive Literals (3)
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "none": TokenType.NONE,

    # Logical Operators (3)
    "and": TokenType.AND,
    "or": TokenType.OR,
    "not": TokenType.NOT,
}

OPERATORS = [
    "+", "-", "*", "/", "%",
    "==", "!=", "<", "<=", ">", ">=",
    "=", "->", "=>",
    "..", "..=",
    "?", "??", "!",
    "and", "or", "not"
]

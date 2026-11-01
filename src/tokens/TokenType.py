from enum import Enum, auto

class TokenType(Enum):
    # Reserved Keywords
    PUB = auto()
    EXTERN = auto()
    FN = auto()
    LET = auto()
    MUT = auto()
    STRUCT = auto()
    ENUM = auto()
    IMPORT = auto()
    FROM = auto()
    USE = auto()

    CLASS = auto()
    INTERFACE = auto()
    EXTENDS = auto()
    IMPLEMENTS = auto()
    NEW = auto()
    THIS = auto()
    SUPER = auto()
    PUBLIC = auto()
    PRIVATE = auto()

    IF = auto()
    ELIF = auto()
    ELSE = auto()
    FOR = auto()
    WHILE = auto()
    IN = auto()
    MATCH = auto()
    RETURN = auto()
    BREAK = auto()
    CONTINUE = auto()

    TRUE = auto()
    FALSE = auto()
    NONE = auto()

    AND = auto()
    OR = auto()
    NOT = auto()

    # Literals
    INT = auto()
    FLOAT = auto()
    STR = auto()
    IDENTIFIER = auto()

    # Arithmetic Operators
    PLUS = auto()              # +
    MINUS = auto()             # -
    STAR = auto()              # *
    SLASH = auto()             # /
    PERCENT = auto()           # %

    # Relational & Equality Operators
    ASSIGN = auto()            # =
    EQ = auto()                # ==
    NOT_EQ = auto()            # !=
    LT = auto()                # <
    LTE = auto()               # <=
    GT = auto()                # >
    GTE = auto()               # >=

    PIPE = auto()

    # Arrows
    ARROW = auto()             # ->
    FAT_ARROW = auto()         # =>

    # Ranges
    DOT_DOT = auto()           # ..
    DOT_DOT_EQ = auto()        # ..=

    # Optionals & Error Propagation
    QUESTION = auto()          # ?
    QUESTION_QUESTION = auto() # ??
    EXCLAMATION = auto()       # !

    # Punctuation & Delimiters
    DOT = auto()               # .
    COLON = auto()             # :
    COMMA = auto()             # ,
    SEMICOLON = auto()         # ;
    NEWLINE = auto()           # Logical statement boundary inserted from a line break
    LPAREN = auto()            # (
    RPAREN = auto()            # )
    LBRACE = auto()            # {
    RBRACE = auto()            # }
    LBRACKET = auto()          # [
    RBRACKET = auto()          # ]

    # Stream End
    EOF = auto()

from tokens.TokenType import TokenType
from typing import Any, Optional

class Token:
    """
    Represents a lexical token in Wysna.
    
    Attributes:
        value (Any): The literal value or symbol of the token.
        type (TokenType): The token classification.
        line (int): 1-based line number of the initial character.
        col (int): 1-based column number of the initial character.
    """
    def __init__(
        self,
        value: Any = None,
        type: Optional[TokenType] = None,
        line: int = 1,
        col: int = 1,
        type_: Optional[TokenType] = None
    ) -> None:
        self.value = value
        self.type = type if type is not None else type_
        self.line = line
        self.col = col

    def __str__(self) -> str:
        type_name = self.type.name if self.type is not None else "UNKNOWN"
        return f"Token({self.value!r}, {type_name}, line={self.line}, col={self.col})"

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Token):
            return False
        return (
            self.value == other.value
            and self.type == other.type
            and self.line == other.line
            and self.col == other.col
        )

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

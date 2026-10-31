from typing import Dict, Optional

# Canonical W Diagnostic Code Taxonomy (v0.1.2+)
# W0xxx: Lexical & Syntactic Diagnostics
# W1xxx: Name Resolution, Scope & Module Diagnostics
# W2xxx: Type System, Exhaustiveness & Error Channel Diagnostics
# W3xxx: Mutability, Ownership & OOP Encapsulation Diagnostics
# W4xxx: Runtime Execution Diagnostics
# W5xxx: Host Toolchain Diagnostics
# W6xxx: Lints (warnings that never block compilation)
DIAGNOSTIC_CODES: Dict[str, str] = {
    "W9998": "Compiler accepted a program it cannot lower (compiler bug)",
    "W9999": "Internal compiler invariant violation",
    # W0xxx — Lexical & Syntax
    "W0001": "Unexpected token or invalid character",
    "W0002": "Malformed numeric literal",
    "W0003": "Unterminated string literal",
    "W0004": "Invalid or unterminated string escape sequence",
    "W0005": "Invalid Unicode scalar escape sequence",
    "W0006": "Invalid syntax or missing delimiter",
    # W1xxx — Name Resolution & Declarations
    "W1001": "Unknown identifier, symbol, or type",
    "W1002": "Duplicate declaration",
    "W1003": "Module resolution or circular import error",
    "W1004": "Invalid control-flow or lexical context (break/continue/return/this/super)",
    # W2xxx — Type System & Exhaustiveness
    "W2001": "Type mismatch",
    "W2002": "Invalid assignment target",
    "W2003": "Non-exhaustive match expression",
    "W2004": "Invalid return type or missing return statement",
    "W2005": "Invalid call arity or non-callable target",
    "W2006": "Invalid optional ('?') or fallible ('T ! E' / 'else err') propagation",
    "W2007": "Missing or invalid struct initialization field",
    "W2010": "Invalid dictionary key type",
    "W2011": "Recursive generic instantiation cycle or infinite layout",
    "W2012": "Generic constraint violation",
    "W2013": "Feature unsupported by the selected backend",
    # W3xxx — Mutability & OOP Contracts
    "W3001": "Mutation of immutable value binding",
    "W3002": "Invalid member access/mutation or private visibility violation",
    "W3003": "Invalid class inheritance or unimplemented interface contract",
    "W3004": "Constructor safety violation (uninitialized fields or invalid super invocation)",
    # W4xxx — Runtime Execution
    "W4001": "Division or modulo by zero",
    "W4002": "Index or key out of bounds",
    "W4003": "Runtime execution error",
    # W5xxx — Host Toolchain
    "W5001": "C compiler command unavailable or invalid",
    "W5002": "Invalid command usage or option",
    "W5003": "Source file could not be read",
    "W5004": "Requested output artifact could not be written or read back",
    "W5006": "Capability policy rejected execution",
    # W6xxx — Lints (warnings; never block compilation)
    "W6001": "Pure expression statement result is discarded",
    "W6002": "Strong reference cycle between classes can leak under ARC",
}

def normalize_diagnostic_code(code: str, default: str = "W0001") -> str:
    if code in DIAGNOSTIC_CODES:
        return code
    return default


class WysnaError(Exception):
    """
    Base diagnostic exception for Wysna toolchain.

    Attributes:
        message (str): Explanatory error message.
        line (int): 1-based source line.
        col (int): 1-based source column.
        file (str): Source filename or identifier.
        code (str): Stable diagnostic code (e.g. 'W2001').
        severity (str): Diagnostic severity ('error' or 'warning').
        stage (str): Compiler stage ('lexer', 'parser', 'type', 'runtime').
        help (str | None): Optional guidance; it is not an automatic fix.
    """
    def __init__(
        self,
        message: str,
        line: Optional[int] = None,
        col: Optional[int] = None,
        file: str = "<stdin>",
        code: str = "W0001",
        severity: str = "error",
        stage: str = "compiler",
        help: Optional[str] = None,
    ) -> None:
        self.message = message
        self.has_location = line is not None and col is not None
        self.line = line if line is not None else 1
        self.col = col if col is not None else 1
        self.start_byte = None
        self.end_byte = None
        self.file = file
        self.code = normalize_diagnostic_code(code)
        self.severity = severity
        self.stage = stage
        self.help = help
        # Machine-readable extras for agents. ``data`` carries structured facts
        # (for example ``expected``/``found``); ``fixes`` carries candidate edits:
        # {"description": str, "applicability": "machine"|"maybe"|"manual",
        #  "edits": [{"line": int, "col": int, "old": str, "new": str}]}.
        self.data: Dict[str, object] = {}
        self.fixes: list = []
        self.related: list = []
        super().__init__(self.__str__())

    def with_data(self, **facts) -> "WysnaError":
        """Attach structured facts and return self for ``raise`` chaining."""
        self.data.update(facts)
        return self

    def with_related(self, message: str, *, line: Optional[int] = None,
                     col: Optional[int] = None, file: Optional[str] = None) -> "WysnaError":
        """Attach a secondary location (for example a previous or immutable declaration)."""
        self.related.append({
            "message": message,
            "file": self.file if file is None else file,
            "line": line,
            "col": col,
        })
        return self

    def with_fix(self, description: str, old: str, new: str, *,
                 line: Optional[int] = None, col: Optional[int] = None,
                 applicability: str = "maybe") -> "WysnaError":
        """Attach one single-edit candidate fix anchored at the diagnostic by default."""
        self.fixes.append({
            "description": description,
            "applicability": applicability,
            "edits": [{"line": self.line if line is None else line,
                       "col": self.col if col is None else col,
                       "old": old, "new": new}],
        })
        return self

    def __str__(self) -> str:
        return f"[{self.code}] {self.file}:{self.line}:{self.col} - {self.message}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "stage": self.stage,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.col,
            "span": {
                "file": self.file,
                "line": self.line,
                "column": self.col,
            },
        }


class ToolchainError(WysnaError):
    def __init__(self, message: str) -> None:
        super().__init__(message, file="<toolchain>", code="W5001", stage="toolchain")


class LexerError(WysnaError):
    def __init__(self, message: str, line: Optional[int] = None, col: Optional[int] = None, file: str = "<stdin>", code: str = "W0001") -> None:
        super().__init__(
            message=message,
            line=line,
            col=col,
            file=file,
            code=normalize_diagnostic_code(code, default="W0001"),
            stage="lexer",
        )


class ParserError(WysnaError):
    def __init__(self, message: str, line: Optional[int] = None, col: Optional[int] = None, file: str = "<stdin>", code: str = "W0001") -> None:
        super().__init__(
            message=message,
            line=line,
            col=col,
            file=file,
            code=normalize_diagnostic_code(code, default="W0001"),
            stage="parser",
        )


class TypeError(WysnaError):
    def __init__(self, message: str, line: Optional[int] = None, col: Optional[int] = None, file: str = "<stdin>", code: str = "W2001", help: Optional[str] = None) -> None:
        super().__init__(
            message=message,
            line=line,
            col=col,
            file=file,
            code=normalize_diagnostic_code(code, default="W2001"),
            stage="type",
            help=help,
        )


class TypeErrors(TypeError):
    """A bounded batch of independent semantic diagnostics from one check.

    The first diagnostic is the primary one, so callers that only understand a
    single error keep their behavior; agents read ``diagnostics`` to repair every
    independent error in a single step.
    """
    MAX_DIAGNOSTICS = 100

    def __init__(self, diagnostics):
        first = diagnostics[0]
        super().__init__(first.message,
                         first.line if first.has_location else None,
                         first.col if first.has_location else None,
                         first.file, first.code, help=first.help)
        self.data = dict(first.data)
        self.fixes = list(first.fixes)
        self.related = list(first.related)
        self.start_byte = first.start_byte
        self.end_byte = first.end_byte
        self.diagnostics = tuple(diagnostics)

    def to_dict(self):
        result = super().to_dict()
        result['diagnostics'] = [error.to_dict() for error in self.diagnostics]
        return result


class EnvironmentError(WysnaError):
    def __init__(self, message: str, line: Optional[int] = None, col: Optional[int] = None, file: str = "<stdin>", code: str = "W4003") -> None:
        super().__init__(
            message=message,
            line=line,
            col=col,
            file=file,
            code=normalize_diagnostic_code(code, default="W4003"),
            stage="runtime",
        )


class WysnaRuntimeError(WysnaError):
    def __init__(self, message: str, line: Optional[int] = None, col: Optional[int] = None, file: str = "<stdin>", code: str = "W4003") -> None:
        norm_code = normalize_diagnostic_code(code, default="W4003")
        if norm_code == "W4003":
            msg_low = message.lower()
            if "division by zero" in msg_low or "modulo by zero" in msg_low:
                norm_code = "W4001"
            elif "out of bounds" in msg_low or "empty list" in msg_low or "key not found" in msg_low:
                norm_code = "W4002"
        super().__init__(
            message=message,
            line=line,
            col=col,
            file=file,
            code=norm_code,
            stage="runtime",
        )


# Backward compatibility aliases

class ParserErrors(ParserError):
    """A bounded syntax diagnostic batch; no recovered tree reaches execution."""
    def __init__(self, diagnostics):
        first = diagnostics[0]
        super().__init__(first.message,
                         first.line if first.has_location else None,
                         first.col if first.has_location else None,
                         first.file, first.code)
        self.data = dict(first.data)
        self.fixes = list(first.fixes)
        self.related = list(first.related)
        self.start_byte = first.start_byte
        self.end_byte = first.end_byte
        self.diagnostics = tuple(diagnostics)

    def to_dict(self):
        result = super().to_dict()
        result['diagnostics'] = [error.to_dict() for error in self.diagnostics]
        return result

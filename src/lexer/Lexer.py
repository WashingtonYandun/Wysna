from tokens.Token import Token
from tokens.TokenType import TokenType
from common.constants import KEYWORDS
from error.Error import LexerError
from typing import List, Optional

# Tokens after which a physical line break terminates the preceding statement.
_LINE_TERMINATOR_TOKENS = frozenset({
    TokenType.INT, TokenType.FLOAT, TokenType.STR,
    TokenType.TRUE, TokenType.FALSE, TokenType.NONE,
    TokenType.IDENTIFIER, TokenType.THIS, TokenType.SUPER,
    TokenType.RPAREN, TokenType.RBRACKET, TokenType.RBRACE,
    TokenType.QUESTION, TokenType.BREAK, TokenType.CONTINUE,
})

class Lexer:
    """
    Lexical analyzer for Wysna conforming to SPEC.md v0.1.
    
    Transforms UTF-8 source text into a stream of typed tokens with 1-based line/col positions.
    Never calls exit(); raises LexerError with structured location diagnostics.
    """
    def __init__(self, source: str, file: str = "<stdin>") -> None:
        self.source = source
        self.file = file
        self.length = len(source)
        self.pos = 0
        self.line = 1
        self.col = 1

    def peek(self, offset: int = 0) -> Optional[str]:
        idx = self.pos + offset
        if idx < self.length:
            return self.source[idx]
        return None

    def advance(self) -> Optional[str]:
        if self.pos >= self.length:
            return None
        ch = self.source[self.pos]
        self.pos += 1
        if ch == '\n':
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        paren_depth = 0
        bracket_depth = 0

        while self.pos < self.length:
            ch = self.peek()

            # Horizontal whitespace is insignificant; line endings may delimit statements.
            if ch in (' ', '\t'):
                self.advance()
                continue

            if ch in ('\n', '\r'):
                line, col = self.line, self.col
                newline_start = self.pos
                if ch == '\r':
                    self.pos += 1
                    if self.peek() == '\n':
                        self.pos += 1
                    self.line += 1
                    self.col = 1
                else:
                    self.advance()
                if (paren_depth == 0 and bracket_depth == 0 and tokens
                        and tokens[-1].type in _LINE_TERMINATOR_TOKENS):
                    tokens.append(Token(value="\n", type=TokenType.NEWLINE, line=line, col=col))
                    self._stamp(tokens[-1], newline_start)
                continue

            # Line comments: skip until a line ending or EOF.
            if ch == '#':
                while self.peek() is not None and self.peek() not in ('\n', '\r'):
                    self.advance()
                continue

            # Nested block comments: /* ... */
            if ch == '/' and self.peek(1) == '*':
                comment_line, comment_col = self.line, self.col
                depth = 1
                saw_newline = False
                nl_line, nl_col, nl_pos = self.line, self.col, self.pos
                self.advance()
                self.advance()
                while depth > 0:
                    cur = self.peek()
                    if cur is None:
                        raise LexerError(
                            message="Unterminated block comment",
                            line=comment_line,
                            col=comment_col,
                            file=self.file,
                            code="W0002"
                        )
                    if cur == '/' and self.peek(1) == '*':
                        depth += 1
                        self.advance()
                        self.advance()
                    elif cur == '*' and self.peek(1) == '/':
                        depth -= 1
                        self.advance()
                        self.advance()
                    elif cur in ('\n', '\r'):
                        if not saw_newline:
                            nl_line, nl_col, nl_pos = self.line, self.col, self.pos
                            saw_newline = True
                        if cur == '\r':
                            self.pos += 1
                            if self.peek() == '\n':
                                self.pos += 1
                            self.line += 1
                            self.col = 1
                        else:
                            self.advance()
                    else:
                        self.advance()
                if (saw_newline and paren_depth == 0 and bracket_depth == 0
                        and tokens and tokens[-1].type in _LINE_TERMINATOR_TOKENS):
                    tokens.append(Token(value="\n", type=TokenType.NEWLINE, line=nl_line, col=nl_col))
                    self._stamp(tokens[-1], nl_pos)
                continue

            token_start = self.pos
            start_line = self.line
            start_col = self.col

            # Reject leading dot float, e.g. '.5', unless this is tuple positional indexing (.0, .1, ...)
            if ch == '.' and self.peek(1) is not None and self.peek(1).isascii() and self.peek(1).isdigit():
                prev_allows_tuple_index = bool(
                    tokens and (
                        tokens[-1].type in (TokenType.IDENTIFIER, TokenType.RPAREN, TokenType.RBRACKET, TokenType.THIS)
                        or (tokens[-1].type == TokenType.INT and len(tokens) >= 2 and tokens[-2].type == TokenType.DOT)
                    )
                )
                if not prev_allows_tuple_index:
                    raise LexerError(
                        message="Float literals must have leading digits before decimal point",
                        line=start_line,
                        col=start_col,
                        file=self.file,
                        code="W0002"
                    )

            # Numbers (Integers & Floats)
            if ch.isascii() and ch.isdigit():
                tokens.append(self._lex_number(start_line, start_col, after_dot=bool(tokens and tokens[-1].type == TokenType.DOT)))
                self._stamp(tokens[-1], token_start)
                continue

            # Formatted strings: f"..." or f"""..."""
            if ch == 'f' and self.peek(1) == '"':
                self._lex_fstring(tokens, start_line, start_col, token_start)
                continue

            # Strings: "..." or """..."""
            if ch == '"':
                tokens.append(self._lex_string(start_line, start_col))
                self._stamp(tokens[-1], token_start)
                continue

            # Identifiers and Keywords
            if (ch.isascii() and ch.isalpha()) or ch == '_':
                tokens.append(self._lex_identifier(start_line, start_col))
                self._stamp(tokens[-1], token_start)
                continue

            # Multi-character & single-character operators
            token = self._lex_operator(start_line, start_col)
            tokens.append(token)
            if token.type == TokenType.LPAREN:
                paren_depth += 1
            elif token.type == TokenType.RPAREN:
                paren_depth = max(0, paren_depth - 1)
            elif token.type == TokenType.LBRACKET:
                bracket_depth += 1
            elif token.type == TokenType.RBRACKET:
                bracket_depth = max(0, bracket_depth - 1)
            self._stamp(tokens[-1], token_start)

        # Append EOF token
        tokens.append(Token(value=None, type=TokenType.EOF, line=self.line, col=self.col))
        self._stamp(tokens[-1], self.pos)
        return tokens

    def _stamp(self, token, start):
        token.start_offset = start
        token.end_offset = self.pos
        token.end_line = self.line
        token.end_col = self.col

    def _lex_prefixed_int(self, start_line: int, start_col: int) -> Token:
        self.advance()  # '0'
        prefix = self.advance()  # 'x'/'X' or 'b'/'B'
        base = 16 if prefix in ('x', 'X') else 2
        valid_digits = "0123456789abcdefABCDEF" if base == 16 else "01"
        digits = ""
        prev_was_underscore = False
        saw_digit = False

        while self.peek() is not None:
            c = self.peek()
            if c.isascii() and c in valid_digits:
                digits += c
                self.advance()
                prev_was_underscore = False
                saw_digit = True
            elif c == '_':
                if not saw_digit or prev_was_underscore:
                    raise LexerError(
                        message="Consecutive or leading underscores in numeric literal are not allowed",
                        line=self.line,
                        col=self.col,
                        file=self.file,
                        code="W0002",
                    )
                nxt = self.peek(1)
                if nxt is None or not (nxt.isascii() and nxt in valid_digits):
                    raise LexerError(
                        message="Invalid numeric separator placement",
                        line=self.line,
                        col=self.col,
                        file=self.file,
                        code="W0002",
                    )
                self.advance()
                prev_was_underscore = True
            else:
                break

        if not saw_digit or prev_was_underscore:
            raise LexerError(
                message=f"Malformed base-{base} integer literal",
                line=start_line,
                col=start_col,
                file=self.file,
                code="W0002",
            )

        trailing = self.peek()
        if trailing is not None and ((trailing.isascii() and trailing.isalnum()) or trailing == '_' or (trailing == '.' and self.peek(1) != '.')):
            raise LexerError(
                message=f"Malformed numeric literal '0{prefix}{digits}{trailing}'",
                line=start_line,
                col=start_col,
                file=self.file,
                code="W0002",
            )

        ival = int(digits, base)
        if ival > 9223372036854775808:
            raise LexerError(
                message=f"Malformed or out-of-range numeric literal '0{prefix}{digits}'",
                line=start_line,
                col=start_col,
                file=self.file,
                code="W0002",
            )
        return Token(value=ival, type=TokenType.INT, line=start_line, col=start_col)

    def _lex_number(self, start_line: int, start_col: int, after_dot: bool = False) -> Token:
        if self.peek() == '0' and self.peek(1) in ('x', 'X', 'b', 'B'):
            return self._lex_prefixed_int(start_line, start_col)

        num_str = ""
        is_float = False
        prev_was_underscore = False

        while self.peek() is not None:
            c = self.peek()

            if c.isascii() and c.isdigit():
                num_str += c
                self.advance()
                prev_was_underscore = False

            elif c == '_':
                if prev_was_underscore:
                    raise LexerError(
                        message="Consecutive underscores in numeric literal are not allowed",
                        line=self.line,
                        col=self.col,
                        file=self.file,
                        code="W0002"
                    )
                # Next char must be digit or '.'
                nxt = self.peek(1)
                if nxt is None or not ((nxt.isascii() and nxt.isdigit()) or nxt == '.'):
                    raise LexerError(
                        message="Invalid numeric separator placement",
                        line=self.line,
                        col=self.col,
                        file=self.file,
                        code="W0002"
                    )
                num_str += c
                self.advance()
                prev_was_underscore = True

            elif c == '.':
                # Range check: if '..' or '..=', this is a range operator, not a float point
                if self.peek(1) == '.' or after_dot:
                    break

                # If we already have a decimal point, stop or error
                if is_float:
                    break

                # Must be followed by a digit: e.g. '12.34'
                nxt = self.peek(1)
                if nxt is None or not (nxt.isascii() and nxt.isdigit()):
                    raise LexerError(
                        message="Trailing decimal point in float literal is not allowed",
                        line=self.line,
                        col=self.col,
                        file=self.file,
                        code="W0002"
                    )

                if prev_was_underscore:
                    raise LexerError(
                        message="Numeric separator cannot precede decimal point",
                        line=self.line,
                        col=self.col,
                        file=self.file,
                        code="W0002"
                    )

                is_float = True
                num_str += c
                self.advance()
                prev_was_underscore = False

            else:
                break

        if prev_was_underscore:
            raise LexerError(
                message="Trailing underscore in numeric literal is not allowed",
                line=start_line,
                col=start_col,
                file=self.file,
                code="W0002"
            )

        clean_str = num_str.replace("_", "")

        # A literal immediately followed by an identifier character is malformed:
        # Wysna has no hex, binary, octal or exponent notation, so `0x10` and `1e5` used
        # to lex as `0`/`1` plus the identifiers `x10`/`e5`, which surfaced later as a
        # bogus "unknown identifier" instead of a numeric-literal error.
        trailing = self.peek()
        if trailing is not None and ((trailing.isascii() and trailing.isalnum()) or trailing == '_'):
            raise LexerError(
                message=f"Malformed numeric literal '{num_str}{trailing}'",
                line=start_line,
                col=start_col,
                file=self.file,
                code="W0002",
            )
        try:
            if is_float:
                value = float(clean_str)
                if value in (float("inf"), float("-inf")) and "inf" not in clean_str.lower():
                    raise ValueError("Floating-point literal overflows binary64")
                return Token(value=value, type=TokenType.FLOAT, line=start_line, col=start_col)
            else:
                if len(clean_str) > 20:
                    raise ValueError("Integer literal exceeds 64-bit range")
                ival = int(clean_str)
                # Keep the magnitude of INT64_MIN available for the parser's unary
                # minus rule. Positive out-of-range literals are rejected by typing.
                if ival > 9223372036854775808:
                    raise ValueError("Integer literal exceeds 64-bit range")
                return Token(value=ival, type=TokenType.INT, line=start_line, col=start_col)
        except (ValueError, OverflowError):
            raise LexerError(
                message=f"Malformed or out-of-range numeric literal '{num_str}'",
                line=start_line,
                col=start_col,
                file=self.file,
                code="W0002",
            )

    def _lex_escape(self, start_line: int, start_col: int) -> str:
        self.advance()  # consume '\'
        esc = self.peek()
        if esc is None:
            raise LexerError(
                message="Unterminated string escape at end of file",
                line=start_line,
                col=start_col,
                file=self.file,
                code="W0004",
            )
        escapes = {
            'n': '\n',
            'r': '\r',
            't': '\t',
            '\\': '\\',
            '"': '"',
            '0': '\0',
        }
        if esc in escapes:
            self.advance()
            return escapes[esc]
        if esc == 'u':
            self.advance()
            return self._lex_unicode_escape(start_line, start_col)
        raise LexerError(
            message=f"Invalid escape sequence '\\{esc}'",
            line=self.line,
            col=self.col,
            file=self.file,
            code="W0004",
        )

    def _consume_multiline_newline(self) -> str:
        if self.peek() == '\r':
            self.pos += 1
            if self.peek() == '\n':
                self.pos += 1
            self.line += 1
            self.col = 1
        else:
            self.advance()
        return '\n'

    def _lex_string(self, start_line: int, start_col: int) -> Token:
        triple = (self.peek(1) == '"' and self.peek(2) == '"')
        self.advance()
        if triple:
            self.advance()
            self.advance()
        val = ""

        while True:
            ch = self.peek()

            if ch is None:
                raise LexerError(
                    message=("Unterminated multiline string literal at end of file"
                             if triple else "Unterminated string literal at end of file"),
                    line=start_line,
                    col=start_col,
                    file=self.file,
                    code="W0003"
                )

            if ch in ('\n', '\r'):
                if not triple:
                    raise LexerError(
                        message="Unterminated string literal (newline in single-line string)",
                        line=start_line,
                        col=start_col,
                        file=self.file,
                        code="W0003"
                    )
                val += self._consume_multiline_newline()
                continue

            if triple and ch == '"' and self.peek(1) == '"' and self.peek(2) == '"':
                self.advance()
                self.advance()
                self.advance()
                break

            if not triple and ch == '"':
                self.advance()
                break

            if ch == '\\':
                val += self._lex_escape(start_line, start_col)
            else:
                val += ch
                self.advance()

        return Token(value=val, type=TokenType.STR, line=start_line, col=start_col)

    def _scan_fstring_expr(self, f_start_line: int, f_start_col: int, triple: bool) -> str:
        """Scan inside `{ ... }` of an f-string up to its matching `}`, skipping inner strings."""
        expr_start = self.pos
        bdepth = 1
        while True:
            ch = self.peek()
            if ch is None:
                raise LexerError(
                    message="Unterminated interpolation expression in formatted string",
                    line=f_start_line,
                    col=f_start_col,
                    file=self.file,
                    code="W0003",
                )
            if ch in ('\n', '\r'):
                if not triple:
                    raise LexerError(
                        message="Unterminated formatted string literal (newline inside interpolation)",
                        line=f_start_line,
                        col=f_start_col,
                        file=self.file,
                        code="W0003",
                    )
                self._consume_multiline_newline()
                continue
            if ch == '"':
                # Skip nested string literal inside `{ ... }`
                self._lex_string(self.line, self.col)
                continue
            if ch == '#':
                if not triple:
                    raise LexerError(
                        message="Comment inside single-line formatted string interpolation",
                        line=self.line,
                        col=self.col,
                        file=self.file,
                        code="W0006",
                    )
                while self.peek() is not None and self.peek() not in ('\n', '\r'):
                    self.advance()
                continue
            if ch == '{':
                bdepth += 1
                self.advance()
                continue
            if ch == '}':
                bdepth -= 1
                if bdepth == 0:
                    expr_src = self.source[expr_start:self.pos]
                    self.advance()  # consume closing '}'
                    return expr_src
                self.advance()
                continue
            self.advance()

    def _lex_fstring(self, tokens: List[Token], start_line: int, start_col: int, token_start: int) -> None:
        self.advance()  # consume 'f'
        triple = (self.peek(1) == '"' and self.peek(2) == '"')
        self.advance()
        if triple:
            self.advance()
            self.advance()

        parts = []
        buf = ""
        lit_line, lit_col = self.line, self.col

        while True:
            ch = self.peek()
            if ch is None:
                raise LexerError(
                    message="Unterminated formatted string literal at end of file",
                    line=start_line,
                    col=start_col,
                    file=self.file,
                    code="W0003",
                )
            if ch in ('\n', '\r'):
                if not triple:
                    raise LexerError(
                        message="Unterminated formatted string literal (newline in single-line string)",
                        line=start_line,
                        col=start_col,
                        file=self.file,
                        code="W0003",
                    )
                buf += self._consume_multiline_newline()
                continue
            if triple and ch == '"' and self.peek(1) == '"' and self.peek(2) == '"':
                self.advance()
                self.advance()
                self.advance()
                break
            if not triple and ch == '"':
                self.advance()
                break
            if ch == '\\':
                buf += self._lex_escape(start_line, start_col)
                continue
            if ch == '{':
                if self.peek(1) == '{':
                    self.advance()
                    self.advance()
                    buf += '{'
                    continue
                if buf:
                    parts.append(("lit", buf, lit_line, lit_col, self.pos))
                    buf = ""
                self.advance()  # consume '{'
                expr_line, expr_col = self.line, self.col
                expr_offset = self.pos
                expr_src = self._scan_fstring_expr(start_line, start_col, triple)
                if not expr_src.strip():
                    raise LexerError(
                        message="Empty expression in formatted string",
                        line=expr_line,
                        col=expr_col,
                        file=self.file,
                        code="W0006",
                    )
                parts.append(("expr", expr_src, expr_line, expr_col, expr_offset))
                lit_line, lit_col = self.line, self.col
                continue
            if ch == '}':
                if self.peek(1) == '}':
                    self.advance()
                    self.advance()
                    buf += '}'
                    continue
                raise LexerError(
                    message="Single '}' is not allowed in formatted string; use '}}'",
                    line=self.line,
                    col=self.col,
                    file=self.file,
                    code="W0006",
                )
            buf += ch
            self.advance()

        if buf:
            parts.append(("lit", buf, lit_line, lit_col, self.pos))

        has_expr = any(kind == "expr" for kind, _, _, _, _ in parts)
        if not has_expr:
            literal_value = "".join(text for _, text, _, _, _ in parts)
            tok = Token(value=literal_value, type=TokenType.STR, line=start_line, col=start_col)
            self._stamp(tok, token_start)
            tokens.append(tok)
            return

        def emit(val, ttype, l, c, s_off, e_off):
            t = Token(value=val, type=ttype, line=l, col=c)
            t.start_offset = s_off
            t.end_offset = e_off
            t.end_line = self.line
            t.end_col = self.col
            tokens.append(t)

        emit("(", TokenType.LPAREN, start_line, start_col, token_start, self.pos)
        for idx, (kind, text, pline, pcol, extra) in enumerate(parts):
            if idx > 0:
                emit("+", TokenType.PLUS, pline, pcol, token_start, self.pos)
            if kind == "lit":
                emit(text, TokenType.STR, pline, pcol, token_start, self.pos)
            else:
                sub_lexer = Lexer(text, file=self.file)
                sub_lexer.line = pline
                sub_lexer.col = pcol
                sub_tokens = sub_lexer.tokenize()
                if sub_tokens and sub_tokens[-1].type == TokenType.EOF:
                    sub_tokens.pop()
                while sub_tokens and sub_tokens[-1].type == TokenType.NEWLINE:
                    sub_tokens.pop()
                emit("str", TokenType.IDENTIFIER, pline, pcol, extra, extra)
                emit("(", TokenType.LPAREN, pline, pcol, extra, extra)
                for st in sub_tokens:
                    if hasattr(st, "start_offset"):
                        st.start_offset += extra
                        st.end_offset += extra
                    tokens.append(st)
                emit(")", TokenType.RPAREN, pline, pcol, extra, extra)
        emit(")", TokenType.RPAREN, start_line, start_col, token_start, self.pos)

    def _lex_unicode_escape(self, start_line: int, start_col: int) -> str:
        if self.peek() != '{':
            raise LexerError(
                message="Expected '{' after '\\u'",
                line=self.line,
                col=self.col,
                file=self.file,
                code="W0005"
            )
        self.advance()  # consume '{'

        hex_str = ""
        while self.peek() is not None and self.peek() != '}':
            ch = self.peek()
            if ch in "0123456789abcdefABCDEF":
                hex_str += ch
                self.advance()
            else:
                raise LexerError(
                    message=f"Invalid character '{ch}' in Unicode escape sequence",
                    line=self.line,
                    col=self.col,
                    file=self.file,
                    code="W0005"
                )

        if self.peek() != '}':
            raise LexerError(
                message="Unterminated Unicode escape sequence, expected '}'",
                line=self.line,
                col=self.col,
                file=self.file,
                code="W0005"
            )
        self.advance()  # consume '}'

        if len(hex_str) == 0 or len(hex_str) > 6:
            raise LexerError(
                message="Unicode escape sequence must contain between 1 and 6 hex digits",
                line=start_line,
                col=start_col,
                file=self.file,
                code="W0005"
            )

        code_point = int(hex_str, 16)
        # Valid Unicode scalar values: 0x0 to 0x10FFFF excluding surrogates (0xD800 to 0xDFFF)
        if code_point > 0x10FFFF or (0xD800 <= code_point <= 0xDFFF):
            raise LexerError(
                message=f"Invalid Unicode code point '0x{hex_str}'",
                line=start_line,
                col=start_col,
                file=self.file,
                code="W0005"
            )

        return chr(code_point)

    def _lex_identifier(self, start_line: int, start_col: int) -> Token:
        ident = ""
        while self.peek() is not None and ((self.peek().isascii() and self.peek().isalnum()) or self.peek() == '_'):
            ident += self.peek()
            self.advance()

        # Check if keyword
        if ident in KEYWORDS:
            kw_type = KEYWORDS[ident]
            if kw_type == TokenType.TRUE:
                return Token(value=True, type=TokenType.TRUE, line=start_line, col=start_col)
            elif kw_type == TokenType.FALSE:
                return Token(value=False, type=TokenType.FALSE, line=start_line, col=start_col)
            elif kw_type == TokenType.NONE:
                return Token(value=None, type=TokenType.NONE, line=start_line, col=start_col)
            else:
                return Token(value=ident, type=kw_type, line=start_line, col=start_col)

        return Token(value=ident, type=TokenType.IDENTIFIER, line=start_line, col=start_col)

    def _lex_operator(self, start_line: int, start_col: int) -> Token:
        ch = self.peek()

        # Check two / three character sequences first
        if ch == '?':
            self.advance()
            if self.peek() == '?':
                self.advance()
                return Token(value="??", type=TokenType.QUESTION_QUESTION, line=start_line, col=start_col)
            return Token(value="?", type=TokenType.QUESTION, line=start_line, col=start_col)

        if ch == '.':
            self.advance()
            if self.peek() == '.':
                self.advance()
                if self.peek() == '=':
                    self.advance()
                    return Token(value="..=", type=TokenType.DOT_DOT_EQ, line=start_line, col=start_col)
                return Token(value="..", type=TokenType.DOT_DOT, line=start_line, col=start_col)
            return Token(value=".", type=TokenType.DOT, line=start_line, col=start_col)

        if ch == '=':
            self.advance()
            if self.peek() == '=':
                self.advance()
                return Token(value="==", type=TokenType.EQ, line=start_line, col=start_col)
            if self.peek() == '>':
                self.advance()
                return Token(value="=>", type=TokenType.FAT_ARROW, line=start_line, col=start_col)
            return Token(value="=", type=TokenType.ASSIGN, line=start_line, col=start_col)

        if ch == '!':
            self.advance()
            if self.peek() == '=':
                self.advance()
                return Token(value="!=", type=TokenType.NOT_EQ, line=start_line, col=start_col)
            return Token(value="!", type=TokenType.EXCLAMATION, line=start_line, col=start_col)

        if ch == '<':
            self.advance()
            if self.peek() == '=':
                self.advance()
                return Token(value="<=", type=TokenType.LTE, line=start_line, col=start_col)
            return Token(value="<", type=TokenType.LT, line=start_line, col=start_col)

        if ch == '>':
            self.advance()
            if self.peek() == '=':
                self.advance()
                return Token(value=">=", type=TokenType.GTE, line=start_line, col=start_col)
            return Token(value=">", type=TokenType.GT, line=start_line, col=start_col)

        if ch == '-':
            self.advance()
            if self.peek() == '>':
                self.advance()
                return Token(value="->", type=TokenType.ARROW, line=start_line, col=start_col)
            if self.peek() == '=':
                self.advance()
                return Token(value="-=", type=TokenType.MINUS_ASSIGN, line=start_line, col=start_col)
            return Token(value="-", type=TokenType.MINUS, line=start_line, col=start_col)

        if ch == '+':
            self.advance()
            if self.peek() == '=':
                self.advance()
                return Token(value="+=", type=TokenType.PLUS_ASSIGN, line=start_line, col=start_col)
            return Token(value="+", type=TokenType.PLUS, line=start_line, col=start_col)

        if ch == '*':
            self.advance()
            if self.peek() == '=':
                self.advance()
                return Token(value="*=", type=TokenType.STAR_ASSIGN, line=start_line, col=start_col)
            return Token(value="*", type=TokenType.STAR, line=start_line, col=start_col)

        # Single character operators & delimiters
        single_ops = {
            '@': TokenType.AT,
            '|': TokenType.PIPE,
            '+': TokenType.PLUS,
            '*': TokenType.STAR,
            '/': TokenType.SLASH,
            '%': TokenType.PERCENT,
            ':': TokenType.COLON,
            ',': TokenType.COMMA,
            ';': TokenType.SEMICOLON,
            '(': TokenType.LPAREN,
            ')': TokenType.RPAREN,
            '{': TokenType.LBRACE,
            '}': TokenType.RBRACE,
            '[': TokenType.LBRACKET,
            ']': TokenType.RBRACKET,
        }

        if ch in single_ops:
            op_type = single_ops[ch]
            self.advance()
            return Token(value=ch, type=op_type, line=start_line, col=start_col)

        # Unrecognized character
        self.advance()
        raise LexerError(
            message=f"Unexpected character '{ch}'",
            line=start_line,
            col=start_col,
            file=self.file,
            code="W0001"
        )

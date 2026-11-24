import os
from typing import List, Optional, Tuple
from tokens.Token import Token
from tokens.TokenType import TokenType
from lexer.Lexer import Lexer
from error.Error import ParserError, ParserErrors
from node.NodeType import (
    clone_ast, ExternFnDecl, LambdaExpr, FunctionType as AstFunctionType, Node, Program, Stmt, Expr, TypeNode, Pattern, Block,
    NamedType, GenericType, TypeParam, OptionalType, ListType, DictType, TupleType, ResultType, ErrorUnionType,
    IdentifierPattern, TuplePattern, EnumPattern, LiteralPattern, WildcardPattern,
    OrPattern, RangePattern, StructPatternField, StructPattern, StaticAttribute,
    FnDecl, Param, StructDecl, StructField, EnumDecl, VariantDecl,
    InterfaceDecl, InterfaceMethod, ClassDecl, ClassField, MethodDecl,
    ImportDecl, UseDecl, UseSymbol, LetDecl,
    WhileStmt, ForStmt, ReturnStmt, BreakStmt, ContinueStmt, ExprStmt,
    IntLiteral, FloatLiteral, StringLiteral, BoolLiteral, NoneLiteral,
    IdentExpr, ThisExpr, SuperExpr, NewExpr,
    UnaryExpr, BinaryExpr, CoalesceExpr, RangeExpr, PostfixTryExpr, ElseErrExpr,
    CallExpr, MemberExpr, IndexExpr, AssignmentExpr, AwaitExpr,
    StructInitExpr, FieldInit, ListExpr, DictExpr, DictEntry, TupleExpr,
    IfExpr, ElifBranch, MatchExpr, MatchArm
)


# Infix Precedence Binding Power (higher = binds tighter)
PRECEDENCE = {
    TokenType.ASSIGN: 1,             # '=' (right-associative)
    TokenType.PLUS_ASSIGN: 1,        # '+=' (right-associative)
    TokenType.MINUS_ASSIGN: 1,       # '-=' (right-associative)
    TokenType.STAR_ASSIGN: 1,        # '*=' (right-associative)
    TokenType.QUESTION_QUESTION: 2,  # '??' (right-associative)
    TokenType.OR: 3,                 # 'or'
    TokenType.AND: 4,                # 'and'
    TokenType.EQ: 5,                 # '=='
    TokenType.NOT_EQ: 5,             # '!='
    TokenType.LT: 6,                 # '<'
    TokenType.LTE: 6,                # '<='
    TokenType.GT: 6,                 # '>'
    TokenType.GTE: 6,                # '>='
    TokenType.IN: 6,                 # 'in'
    TokenType.DOT_DOT: 7,            # '..'
    TokenType.DOT_DOT_EQ: 7,         # '..='
    TokenType.PLUS: 8,               # '+'
    TokenType.MINUS: 8,              # '-'
    TokenType.STAR: 9,               # '*'
    TokenType.SLASH: 9,              # '/'
    TokenType.PERCENT: 9,            # '%'
}

# Token types that can begin an expression. Used to decide whether `return` has a
# value and to detect missing statement separators.
_EXPRESSION_STARTERS = frozenset({
    TokenType.INT, TokenType.FLOAT, TokenType.STR, TokenType.TRUE, TokenType.FALSE,
    TokenType.NONE, TokenType.IDENTIFIER, TokenType.ASYNC, TokenType.THIS, TokenType.SUPER, TokenType.NEW,
    TokenType.MINUS, TokenType.NOT, TokenType.AWAIT, TokenType.LPAREN, TokenType.LBRACKET, TokenType.LBRACE,
    TokenType.PIPE, TokenType.IF, TokenType.MATCH,
})

# Token types that can legally follow a complete expression statement. Anything
# else on the same line means the author wrote two expressions without an operator
# between them (`let x = 1 2`), which the language does not allow.
_STATEMENT_END_TOKENS = frozenset({TokenType.RBRACE, TokenType.EOF})

class Parser:
    """
    Recursive-descent and Pratt parser for Wysna source.
    Never calls exit(); raises ParserError with exact diagnostic codes and positions.
    """
    def __init__(self, file: str = "<stdin>") -> None:
        self.file = file
        self.tokens: List[Token] = []
        self.pos = 0
        self.recover = False
        self.diagnostics = []
        self._source_text: Optional[str] = None

    def _stamp_error(self, error: ParserError) -> ParserError:
        """Attach direct UTF-8 byte spans and structured syntax kind to a ParserError."""
        token = next((t for t in self.tokens if t.line == error.line and t.col == error.col), None)
        if (token is not None and self._source_text is not None
                and hasattr(token, "start_offset") and hasattr(token, "end_offset")):
            try:
                error.start_byte = len(self._source_text[:token.start_offset].encode("utf-8"))
                error.end_byte = len(self._source_text[:token.end_offset].encode("utf-8"))
            except UnicodeEncodeError:
                pass
        if not error.data.get("kind"):
            msg = error.message
            if msg.startswith("Unexpected token"):
                error.with_data(kind="unexpected_token",
                                found=token.type.name if token is not None else None)
            elif "Unterminated" in msg:
                error.with_data(kind="unterminated_construct")
            elif msg.startswith("Expected"):
                error.with_data(kind="expected_construct",
                                found=token.type.name if token is not None else None)
            else:
                error.with_data(kind="syntax_error")
        return error

    def parse(self, source: str, show_tokens: bool = False, *, tokens=None, recover=False) -> Program:
        self._source_text = source
        self.tokens = tokens if tokens is not None else Lexer(source, file=self.file).tokenize()
        self.pos = 0
        self.recover = recover
        self.diagnostics = []
        if show_tokens:
            print("Tokens:", self.tokens)
        try:
            program = self.produce_ast()
            if self.diagnostics:
                raise ParserErrors(self.diagnostics)
            program.source_text = source
            return program
        except ParserErrors:
            raise
        except ParserError as error:
            raise self._stamp_error(error)
        except RecursionError:
            raise self._stamp_error(ParserError(
                message="Maximum syntax nesting depth exceeded",
                line=self.at().line,
                col=self.at().col,
                file=self.file,
                code="W0006",
            ))

    def produce_ast(self) -> Program:
        start_line = self.at().line
        start_col = self.at().col
        body: List[Stmt] = []

        while not self.is_at_end():
            self._skip_statement_separators()
            if self.is_at_end():
                break
            stmt = self._recover_statement(allow_top_level=True)
            if stmt is not None:
                body.append(stmt)

        return Program(body=body, line=start_line, col=start_col)

    def _recover_statement(self, allow_top_level=False):
        start = self.pos
        try:
            return self.parse_stmt(allow_top_level=allow_top_level)
        except ParserErrors:
            raise
        except ParserError as error:
            self._stamp_error(error)
            if not self.recover:
                raise
            self.diagnostics.append(error)
            if len(self.diagnostics) >= 100:
                raise ParserErrors(self.diagnostics)
            self._synchronize(start, allow_top_level)
            return None

    def _synchronize(self, start, top_level):
        starters = {TokenType.LET, TokenType.FN, TokenType.ASYNC, TokenType.STRUCT, TokenType.ENUM,
                    TokenType.CLASS, TokenType.INTERFACE, TokenType.IMPORT,
                    TokenType.WHILE, TokenType.FOR, TokenType.RETURN,
                    TokenType.BREAK, TokenType.CONTINUE}
        depth = 0
        for token in self.tokens[start:self.pos]:
            if token.type == TokenType.LBRACE:
                depth += 1
            elif token.type == TokenType.RBRACE:
                depth = max(0, depth - 1)
        while not self.is_at_end():
            token = self.at()
            if depth == 0:
                if token.type in (TokenType.NEWLINE, TokenType.SEMICOLON):
                    self._skip_statement_separators()
                    return
                if self.pos > start and token.type in starters:
                    return
                if token.type == TokenType.RBRACE and not top_level:
                    return
            if token.type == TokenType.LBRACE:
                depth += 1
            elif token.type == TokenType.RBRACE:
                depth = max(0, depth - 1)
            self.advance()

    # ==========================================
    # Stream Navigation Helpers
    # ==========================================

    def at(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return self.tokens[-1]

    def peek(self, offset: int = 1) -> Token:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return self.tokens[-1]

    def advance(self) -> Token:
        curr = self.at()
        if not self.is_at_end():
            self.pos += 1
        return curr

    def is_at_end(self) -> bool:
        return self.at().type == TokenType.EOF

    def check(self, type_: TokenType) -> bool:
        if self.is_at_end():
            return False
        return self.at().type == type_

    def match(self, *types: TokenType) -> bool:
        for t in types:
            if self.check(t):
                self.advance()
                return True
        return False

    def expect(self, type_: TokenType, code: str = "W0001", msg: str = None) -> Token:
        if self.check(type_):
            return self.advance()
        if type_ == TokenType.IDENTIFIER and self.check(TokenType.ASYNC) and self.peek(1).type != TokenType.FN:
            return self.advance()
        token = self.at()
        err_msg = msg if msg else f"Expected token '{type_.name}', found '{token.type.name}'"
        raise ParserError(message=err_msg, line=token.line, col=token.col, file=self.file,
                          code=code).with_data(kind="expected_token", expected=type_.name,
                                               found=token.type.name)

    # ==========================================
    # Type Annotation Parsing
    # ==========================================

    def parse_type(self) -> TypeNode:
        token = self.at()
        if self.match(TokenType.FN):
            self.expect(TokenType.LPAREN)
            params = []
            if not self.check(TokenType.RPAREN):
                params.append(self.parse_type())
                while self.match(TokenType.COMMA):
                    params.append(self.parse_type())
            self.expect(TokenType.RPAREN)
            self.expect(TokenType.ARROW)
            return AstFunctionType(params, self.parse_type(), token.line, token.col)
        if self.match(TokenType.LPAREN):
            if self.match(TokenType.RPAREN):
                inner_type = TupleType(element_types=[], line=token.line, col=token.col)
                return self._parse_type_suffix(inner_type, token.line, token.col)
            elems: List[TypeNode] = [self.parse_type()]
            saw_comma = False
            while self.match(TokenType.COMMA):
                saw_comma = True
                if self.check(TokenType.RPAREN):
                    break
                elems.append(self.parse_type())
            self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after tuple or grouped type")
            if len(elems) == 1 and not saw_comma:
                inner_type = elems[0]
            else:
                inner_type = TupleType(element_types=elems, line=token.line, col=token.col)
            return self._parse_type_suffix(inner_type, token.line, col=token.col)
        if not (self.check(TokenType.IDENTIFIER) or (self.check(TokenType.ASYNC) and self.peek(1).type != TokenType.FN)):
            raise ParserError(
                message=f"Expected type name, found '{token.type.name}'",
                line=token.line,
                col=token.col,
                file=self.file,
                code="W0006"
            )

        name_tok = self.advance()
        name = name_tok.value
        while self.match(TokenType.DOT):
            name += "." + self.expect(TokenType.IDENTIFIER).value
        line = name_tok.line
        col = name_tok.col

        inner_type: TypeNode
        if self.match(TokenType.LBRACKET):
            if name == "list":
                elem_type = self.parse_type()
                self.expect(TokenType.RBRACKET, code="W0006", msg="Expected ']' after list type")
                inner_type = ListType(element_type=elem_type, line=line, col=col)
            elif name == "dict":
                key_type = self.parse_type()
                self.expect(TokenType.COMMA, code="W0006", msg="Expected ',' between dict key and value types")
                val_type = self.parse_type()
                self.expect(TokenType.RBRACKET, code="W0006", msg="Expected ']' after dict types")
                inner_type = DictType(key_type=key_type, value_type=val_type, line=line, col=col)
            elif name == "tuple":
                elem_types: List[TypeNode] = []
                if not self.check(TokenType.RBRACKET):
                    elem_types.append(self.parse_type())
                    while self.match(TokenType.COMMA):
                        if self.check(TokenType.RBRACKET):
                            break
                        elem_types.append(self.parse_type())
                self.expect(TokenType.RBRACKET, code="W0006", msg="Expected ']' after tuple types")
                inner_type = TupleType(element_types=elem_types, line=line, col=col)
            else:
                # Generic custom type: Box[T], Pair[A, B], etc.
                type_args: List[TypeNode] = [self.parse_type()]
                while self.match(TokenType.COMMA):
                    if self.check(TokenType.RBRACKET):
                        break
                    type_args.append(self.parse_type())
                self.expect(TokenType.RBRACKET, code="W0006", msg="Expected ']' after generic type")
                inner_type = GenericType(name=name, type_args=type_args, line=line, col=col)
        else:
            inner_type = NamedType(name=name, line=line, col=col)

        return self._parse_type_suffix(inner_type, line, col)

    def _parse_type_suffix(self, inner_type: TypeNode, line: int, col: int) -> TypeNode:
        # Check for optional postfix '?'
        if self.match(TokenType.QUESTION):
            inner_type = OptionalType(inner=inner_type, line=line, col=col)

        # Check for fallible return type '!'
        if self.match(TokenType.EXCLAMATION):
            err_type = self.parse_type()
            error_types = [err_type]
            while self.match(TokenType.PIPE):
                error_types.append(self.parse_type())
            if len(error_types) > 1:
                err_type = ErrorUnionType(error_types, line=line, col=col)
            inner_type = ResultType(ok_type=inner_type, err_type=err_type, line=line, col=col)

        return inner_type

    # ==========================================
    # Pattern Parsing
    # ==========================================

    def _starts_struct_pattern_brace(self) -> bool:
        if not self.check(TokenType.LBRACE):
            return False
        idx = self.pos + 1
        while idx < len(self.tokens) and self.tokens[idx].type == TokenType.NEWLINE:
            idx += 1
        if idx >= len(self.tokens):
            return False
        first = self.tokens[idx].type
        if first not in (TokenType.RBRACE, TokenType.DOT_DOT, TokenType.IDENTIFIER):
            return False
        if first == TokenType.IDENTIFIER:
            nxt_idx = idx + 1
            if nxt_idx >= len(self.tokens):
                return False
            nxt = self.tokens[nxt_idx].type
            if nxt not in (TokenType.COLON, TokenType.COMMA, TokenType.RBRACE, TokenType.NEWLINE):
                return False
        # Find matching closing brace and check following token
        depth = 0
        scan = self.pos
        while scan < len(self.tokens):
            t = self.tokens[scan].type
            if t == TokenType.LBRACE:
                depth += 1
            elif t == TokenType.RBRACE:
                depth -= 1
                if depth == 0:
                    after = self.tokens[scan + 1].type if scan + 1 < len(self.tokens) else TokenType.EOF
                    return after in (
                        TokenType.FAT_ARROW, TokenType.IF, TokenType.PIPE,
                        TokenType.COMMA, TokenType.RPAREN, TokenType.RBRACE, TokenType.NEWLINE,
                    )
            scan += 1
        return False

    def _parse_struct_pattern(self, struct_name: str, type_args: Optional[List[TypeNode]], start_tok: Token, allow_or: bool) -> StructPattern:
        self.expect(TokenType.LBRACE, code="W0006", msg="Expected '{' in struct pattern")
        fields: List[StructPatternField] = []
        has_rest = False
        while True:
            while self.match(TokenType.NEWLINE):
                pass
            if self.check(TokenType.RBRACE) or self.is_at_end():
                break
            if self.match(TokenType.DOT_DOT):
                has_rest = True
                while self.match(TokenType.NEWLINE):
                    pass
                self.match(TokenType.COMMA)
                while self.match(TokenType.NEWLINE):
                    pass
                break
            ftok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected field name or '..' in struct pattern")
            if self.match(TokenType.COLON):
                sub_pat = self.parse_pattern(allow_tuple=False, allow_or=allow_or)
                fields.append(StructPatternField(name=ftok.value, pattern=sub_pat, shorthand=False, line=ftok.line, col=ftok.col))
            else:
                fields.append(StructPatternField(
                    name=ftok.value,
                    pattern=IdentifierPattern(name=ftok.value, line=ftok.line, col=ftok.col),
                    shorthand=True,
                    line=ftok.line,
                    col=ftok.col,
                ))
            while self.match(TokenType.NEWLINE):
                pass
            if not self.match(TokenType.COMMA):
                break
        while self.match(TokenType.NEWLINE):
            pass
        self.expect(TokenType.RBRACE, code="W0006", msg="Expected '}' after struct pattern")
        return StructPattern(struct_name=struct_name, fields=fields, has_rest=has_rest, line=start_tok.line, col=start_tok.col, type_args=type_args)

    def _parse_pattern_int_bound(self) -> int:
        tok = self.at()
        if self.match(TokenType.MINUS):
            lit_tok = self.expect(TokenType.INT, code="W0002", msg="Expected an integer after unary '-' in range pattern")
            mag = int(lit_tok.value)
            if mag > 1 << 63:
                raise ParserError(
                    message=f"Integer literal '-{mag}' is below the int64 minimum",
                    line=tok.line, col=tok.col, file=self.file, code="W0002",
                )
            return -mag
        lit_tok = self.expect(TokenType.INT, code="W0002", msg="Expected integer bound in range pattern")
        return int(lit_tok.value)

    def parse_pattern(self, allow_tuple: bool = True, allow_or: bool = True) -> Pattern:
        first = self._parse_atom_pattern(allow_tuple=allow_tuple, allow_or=allow_or)
        if allow_or and self.check(TokenType.PIPE):
            alternatives: List[Pattern] = [first]
            while self.match(TokenType.PIPE):
                alternatives.append(self._parse_atom_pattern(allow_tuple=False, allow_or=allow_or))
            return OrPattern(alternatives=alternatives, line=first.line, col=first.col)
        return first

    def _parse_atom_pattern(self, allow_tuple: bool = True, allow_or: bool = True) -> Pattern:
        token = self.at()

        if self.match(TokenType.STAR):
            raise ParserError(
                message="Wildcard '* =>' has been removed in match; use canonical '_ =>'",
                line=token.line,
                col=token.col,
                file=self.file,
                code="W0001",
            )

        if self.match(TokenType.LPAREN):
            elements: List[Pattern] = []
            if not self.check(TokenType.RPAREN):
                elements.append(self.parse_pattern(allow_tuple=False, allow_or=allow_or))
                while self.match(TokenType.COMMA):
                    if self.check(TokenType.RPAREN):
                        break
                    elements.append(self.parse_pattern(allow_tuple=False, allow_or=allow_or))
            self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after tuple pattern")
            if len(elements) == 1:
                return elements[0]
            return TuplePattern(elements=elements, line=token.line, col=token.col)

        if self.match(TokenType.IDENTIFIER):
            first_name = token.value

            if first_name == "_":
                return WildcardPattern(line=token.line, col=token.col)

            # Constructor-shaped syntax is resolved by the checker. Error-union
            # alternatives become ErrorTypePattern once the subject type is known.
            if self.match(TokenType.DOT):
                owner = [first_name]
                variant_tok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected enum variant name after '.'")
                while self.match(TokenType.DOT):
                    owner.append(variant_tok.value)
                    variant_tok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected enum variant name after '.'")
                pat_type_args = None
                if self.check(TokenType.LBRACKET):
                    saved_pat = self.pos
                    maybe_args = self._try_parse_generic_type_args(allow_struct_init=False, allow_pattern=True)
                    if maybe_args and (self.check(TokenType.LPAREN) or self._starts_struct_pattern_brace()):
                        pat_type_args = maybe_args
                    else:
                        self.pos = saved_pat
                if self._starts_struct_pattern_brace():
                    return self._parse_struct_pattern(".".join(owner + [variant_tok.value]), pat_type_args, token, allow_or)
                payload_args: List[Pattern] = []
                if self.match(TokenType.LPAREN):
                    if not self.check(TokenType.RPAREN):
                        payload_args.append(self.parse_pattern(allow_tuple=False, allow_or=allow_or))
                        while self.match(TokenType.COMMA):
                            if self.check(TokenType.RPAREN):
                                break
                            payload_args.append(self.parse_pattern(allow_tuple=False, allow_or=allow_or))
                    self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after pattern arguments")
                return EnumPattern(enum_name=".".join(owner), variant=variant_tok.value, args=payload_args, line=token.line, col=token.col, type_args=pat_type_args)

            # Check for generic error/enum/struct pattern: Name[T](x) or Name[T] { ... }
            pat_type_args = None
            if self.check(TokenType.LBRACKET):
                saved_pat = self.pos
                maybe_args = self._try_parse_generic_type_args(allow_struct_init=False, allow_pattern=True)
                if maybe_args and (self.check(TokenType.LPAREN) or self._starts_struct_pattern_brace()):
                    pat_type_args = maybe_args
                else:
                    self.pos = saved_pat

            if self._starts_struct_pattern_brace():
                return self._parse_struct_pattern(first_name, pat_type_args, token, allow_or)

            # Check for unqualified Enum variant / error type pattern: Some(x), BoxErr[int](e)
            if self.match(TokenType.LPAREN):
                payload_args = []
                if not self.check(TokenType.RPAREN):
                    payload_args.append(self.parse_pattern(allow_tuple=False, allow_or=allow_or))
                    while self.match(TokenType.COMMA):
                        if self.check(TokenType.RPAREN):
                            break
                        payload_args.append(self.parse_pattern(allow_tuple=False, allow_or=allow_or))
                self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after pattern arguments")
                return EnumPattern(enum_name=None, variant=first_name, args=payload_args, line=token.line, col=token.col, type_args=pat_type_args)

            if first_name == "none":
                raise ParserError(
                    message="Lowercase 'none' pattern has been removed; use canonical 'None'",
                    line=token.line,
                    col=token.col,
                    file=self.file,
                    code="W0001",
                )
            if first_name == "None":
                return EnumPattern(enum_name=None, variant="None", args=[], line=token.line, col=token.col)

            # Check for TuplePattern: a, b
            if allow_tuple and self.check(TokenType.COMMA):
                elements: List[Pattern] = [IdentifierPattern(first_name, line=token.line, col=token.col)]
                while self.match(TokenType.COMMA):
                    next_tok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected identifier in tuple pattern")
                    elements.append(IdentifierPattern(next_tok.value, line=next_tok.line, col=next_tok.col))
                return TuplePattern(elements=elements, line=token.line, col=token.col)

            return IdentifierPattern(name=first_name, line=token.line, col=token.col)

        if self.match(TokenType.INT):
            start_val = int(token.value)
            if self.match(TokenType.DOT_DOT) or self.match(TokenType.DOT_DOT_EQ):
                inclusive = self.tokens[self.pos - 1].type == TokenType.DOT_DOT_EQ
                end_val = self._parse_pattern_int_bound()
                return RangePattern(start=start_val, end=end_val, inclusive=inclusive, line=token.line, col=token.col)
            lit = IntLiteral(token.value, line=token.line, col=token.col)
            return LiteralPattern(literal=lit, line=token.line, col=token.col)

        if self.match(TokenType.MINUS):
            literal_token = self.expect(
                TokenType.INT,
                code="W0002",
                msg="Expected an integer after unary '-' in pattern",
            )
            magnitude = int(literal_token.value)
            if magnitude > 1 << 63:
                raise ParserError(
                    message=f"Integer literal '-{magnitude}' is below the int64 minimum",
                    line=token.line,
                    col=token.col,
                    file=self.file,
                    code="W0002",
                )
            start_val = -magnitude
            if self.match(TokenType.DOT_DOT) or self.match(TokenType.DOT_DOT_EQ):
                inclusive = self.tokens[self.pos - 1].type == TokenType.DOT_DOT_EQ
                end_val = self._parse_pattern_int_bound()
                return RangePattern(start=start_val, end=end_val, inclusive=inclusive, line=token.line, col=token.col)
            literal = IntLiteral(value=start_val, line=token.line, col=token.col)
            return LiteralPattern(literal=literal, line=token.line, col=token.col)

        if self.match(TokenType.STR):
            lit = StringLiteral(token.value, line=token.line, col=token.col)
            return LiteralPattern(literal=lit, line=token.line, col=token.col)

        if self.match(TokenType.TRUE, TokenType.FALSE):
            lit = BoolLiteral(token.value, line=token.line, col=token.col)
            return LiteralPattern(literal=lit, line=token.line, col=token.col)

        raise ParserError(
            message=f"Invalid pattern syntax, found '{token.type.name}'",
            line=token.line,
            col=token.col,
            file=self.file,
            code="W0006"
        )

    # ==========================================
    # Statement & Declaration Parsing
    # ==========================================

    def parse_attributed_function(self, allow_top_level):
        start = self.at()
        if not allow_top_level:
            raise ParserError('Static attributes require a top-level function declaration',
                              file=self.file, line=start.line, col=start.col, code='W0006')
        attributes, names = [], set()
        while self.match(TokenType.AT):
            token = self.expect(TokenType.IDENTIFIER, code='W0006', msg='Expected static attribute name')
            name = token.value
            while self.match(TokenType.DOT):
                name += '.' + self.expect(TokenType.IDENTIFIER, code='W0006', msg='Expected annotation type').value
            if name in names:
                raise ParserError('Duplicate static attribute: ' + name, file=self.file,
                                  line=token.line, col=token.col, code='W1002')
            names.add(name)
            message = ''
            arguments = {}
            if name == 'deprecated':
                self.expect(TokenType.LPAREN, code='W0006', msg='@deprecated requires one non-empty string literal')
                literal = self.expect(TokenType.STR, code='W0006', msg='@deprecated requires one non-empty string literal')
                if not literal.value.strip(' \t\r\n'):
                    raise ParserError('@deprecated requires a non-empty message', file=self.file,
                                      line=literal.line, col=literal.col, code='W0006')
                message = literal.value
                self.expect(TokenType.RPAREN, code='W0006', msg='@deprecated requires exactly one string literal')
            elif name == 'must_use' and self.check(TokenType.LPAREN):
                raise ParserError('@must_use does not accept arguments', file=self.file,
                                  line=token.line, col=token.col, code='W0006')
            elif name != 'must_use' and self.match(TokenType.LPAREN):
                while not self.check(TokenType.RPAREN):
                    field = self.expect(TokenType.IDENTIFIER, code='W0006', msg='Expected annotation field')
                    self.expect(TokenType.COLON, code='W0006', msg='Annotation arguments require field: literal')
                    if field.value in arguments:
                        raise ParserError('Duplicate annotation field: ' + field.value, file=self.file,
                                          line=field.line, col=field.col, code='W1002')
                    negative = self.match(TokenType.MINUS)
                    literal = self.at()
                    if self.match(TokenType.INT):
                        value = int(literal.value) * (-1 if negative else 1)
                        if not -(2**63) <= value < 2**63:
                            raise ParserError('Annotation integer exceeds Int64', file=self.file,
                                              line=literal.line, col=literal.col, code='W0002')
                    elif self.match(TokenType.STR) and not negative:
                        value = literal.value
                    elif self.match(TokenType.TRUE) and not negative:
                        value = True
                    elif self.match(TokenType.FALSE) and not negative:
                        value = False
                    else:
                        raise ParserError('Annotation values must be str, bool, or Int64 literals', file=self.file,
                                          line=literal.line, col=literal.col, code='W0006')
                    arguments[field.value] = value
                    if not self.match(TokenType.COMMA):
                        break
                self.expect(TokenType.RPAREN, code='W0006', msg='Expected annotation closing parenthesis')
            attribute = StaticAttribute(name, message, start.line, start.col)
            attribute.arguments = arguments
            attributes.append(attribute)
            self._skip_statement_separators()
            start = self.at()
        declaration = self.parse_stmt(allow_top_level=True)
        if not isinstance(declaration, FnDecl) or isinstance(declaration, ExternFnDecl):
            raise ParserError('Static attributes require a top-level function declaration', file=self.file,
                              line=declaration.line, col=declaration.col, code='W0006')
        declaration.attributes = attributes
        return declaration

    def parse_stmt(self, allow_top_level: bool = False) -> Stmt:
        if self.check(TokenType.AT):
            return self.parse_attributed_function(allow_top_level)
        if self.match(TokenType.PUB):
            if self.check(TokenType.AT):
                raise ParserError('Place static attributes before pub', file=self.file,
                                  line=self.at().line, col=self.at().col, code='W0006')
            if not allow_top_level:
                raise ParserError("pub requires a top-level declaration", file=self.file, code="W0006")
            statement = self.parse_stmt(allow_top_level=True)
            if not isinstance(statement, (FnDecl, StructDecl, EnumDecl, ClassDecl, InterfaceDecl, LetDecl, ImportDecl)):
                raise ParserError("pub requires a declaration", file=self.file, code="W0006")
            statement.is_exported = True
            return statement
        if self.match(TokenType.EXTERN):
            token = self.tokens[self.pos - 1]
            abi = self.expect(TokenType.STR)
            if abi.value != "C":
                raise ParserError("Only the C ABI is supported", file=self.file, line=abi.line, col=abi.col, code="W0006")
            self.expect(TokenType.FN)
            name = self.expect(TokenType.IDENTIFIER)
            self.expect(TokenType.LPAREN)
            params = self._parse_param_list()
            self.expect(TokenType.RPAREN)
            self.expect(TokenType.ARROW)
            returned = self.parse_type()
            return ExternFnDecl(name.value, params, returned, token.line, token.col)
        if (self.check(TokenType.FN) or (self.check(TokenType.ASYNC) and self.peek(1).type == TokenType.FN) or self.check(TokenType.STRUCT) or self.check(TokenType.ENUM)
                or self.check(TokenType.INTERFACE) or self.check(TokenType.CLASS)
                or self.check(TokenType.IMPORT) or self.check(TokenType.FROM)):
            if not allow_top_level:
                tok = self.at()
                raise ParserError(
                    message=f"Top-level declaration '{tok.value}' is not allowed inside a block",
                    line=tok.line,
                    col=tok.col,
                    file=self.file,
                    code="W0006",
                )
        if self.check(TokenType.ASYNC) and self.peek(1).type == TokenType.FN:
            self.advance()
            return self.parse_fn_decl(is_async=True)
        if self.check(TokenType.FN):
            return self.parse_fn_decl()
        if self.check(TokenType.LET):
            return self.parse_let_decl()
        if self.check(TokenType.STRUCT):
            return self.parse_struct_decl()
        if self.check(TokenType.ENUM):
            return self.parse_enum_decl()
        if self.check(TokenType.INTERFACE):
            return self.parse_interface_decl()
        if self.check(TokenType.CLASS):
            return self.parse_class_decl()
        if self.check(TokenType.IMPORT):
            return self.parse_import_decl()
        if self.check(TokenType.FROM):
            return self.parse_use_decl()
        if self.check(TokenType.WHILE):
            return self.parse_while_stmt()
        if self.check(TokenType.FOR):
            return self.parse_for_stmt()
        if self.check(TokenType.RETURN):
            return self.parse_return_stmt()
        if self.check(TokenType.BREAK):
            tok = self.advance()
            return BreakStmt(line=tok.line, col=tok.col)
        if self.check(TokenType.CONTINUE):
            tok = self.advance()
            return ContinueStmt(line=tok.line, col=tok.col)

        # Expression statement (including if/match used as statement)
        expr = self.parse_expr()
        return ExprStmt(expr=expr, line=expr.line, col=expr.col)

    def _skip_statement_separators(self) -> None:
        while self.match(TokenType.NEWLINE, TokenType.SEMICOLON):
            pass

    def _require_statement_end(self) -> None:
        """Reject a second literal while accepting established inline statement forms."""
        token = self.at()
        if token.type in _STATEMENT_END_TOKENS:
            return
        if self.at().type in (TokenType.NEWLINE, TokenType.SEMICOLON):
            self._skip_statement_separators()
            return
        previous = self.tokens[self.pos - 1] if self.pos > 0 else token
        if token.line > previous.end_line:
            return
        if token.type not in (TokenType.INT, TokenType.FLOAT, TokenType.STR,
                              TokenType.TRUE, TokenType.FALSE, TokenType.NONE,
                              TokenType.LPAREN, TokenType.LBRACKET):
            return
        raise ParserError(
            message=f"Unexpected '{token.type.name}' after a complete expression; "
                    f"statements must be separated by a line break or ';'",
            line=token.line,
            col=token.col,
            file=self.file,
            code="W0006",
        )

    def _parse_param_list(self, allow_receiver: bool = False) -> List[Param]:
        params: List[Param] = []
        if not self.check(TokenType.RPAREN):
            first = True
            while True:
                if first and allow_receiver and (
                    self.check(TokenType.THIS)
                    or (self.check(TokenType.IDENTIFIER) and self.at().value == "self" and self.peek(1).type != TokenType.COLON)
                ):
                    self.advance()
                    first = False
                    if self.match(TokenType.COMMA):
                        if self.check(TokenType.RPAREN):
                            break
                        continue
                    else:
                        break
                first = False
                p_name = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected parameter name")
                self.expect(TokenType.COLON, code="W0001", msg="Expected ':' after parameter name")
                p_type = self.parse_type()
                params.append(Param(p_name.value, p_type, line=p_name.line, col=p_name.col))
                if not self.match(TokenType.COMMA) or self.check(TokenType.RPAREN):
                    break
        return params

    def _parse_type_params(self) -> List[TypeParam]:
        type_params: List[TypeParam] = []
        if self.match(TokenType.LBRACKET):
            while not self.check(TokenType.RBRACKET) and not self.is_at_end():
                p_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected type parameter identifier")
                constraint: Optional[TypeNode] = None
                if self.match(TokenType.COLON):
                    constraint = self.parse_type()
                type_params.append(TypeParam(p_tok.value, constraint, line=p_tok.line, col=p_tok.col))
                if not self.match(TokenType.COMMA):
                    break
            self.expect(TokenType.RBRACKET, code="W0006", msg="Expected ']' after type parameters")
        return type_params

    def _parse_contracts(self, allow_ensures: bool = True) -> Tuple[List[Expr], List[Expr]]:
        requires: List[Expr] = []
        ensures: List[Expr] = []
        while True:
            saved = self.pos
            while self.match(TokenType.NEWLINE):
                pass
            if (
                self.check(TokenType.IDENTIFIER)
                and self.at().value == "requires"
                and self.peek(1).type != TokenType.COLON
            ):
                self.advance()
                requires.append(self.parse_expr(allow_struct_init=False))
                while self.match(TokenType.COMMA):
                    while self.match(TokenType.NEWLINE):
                        pass
                    requires.append(self.parse_expr(allow_struct_init=False))
                continue
            if (
                allow_ensures
                and self.check(TokenType.IDENTIFIER)
                and self.at().value == "ensures"
                and self.peek(1).type != TokenType.COLON
            ):
                self.advance()
                ensures.append(self.parse_expr(allow_struct_init=False))
                while self.match(TokenType.COMMA):
                    while self.match(TokenType.NEWLINE):
                        pass
                    ensures.append(self.parse_expr(allow_struct_init=False))
                continue
            self.pos = saved
            break
        return requires, ensures

    def parse_fn_decl(self, is_async: bool = False) -> FnDecl:
        if not is_async and self.match(TokenType.ASYNC):
            is_async = True
        start_tok = self.expect(TokenType.FN, code="W0006", msg="Expected 'fn'")
        name_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected function name after 'fn'")
        type_params = self._parse_type_params()
        self.expect(TokenType.LPAREN, code="W0006", msg="Expected '(' after function name")
        params = self._parse_param_list(allow_receiver=False)
        self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after parameters")

        # Return type: defaults to unit if omitted
        return_type = NamedType("unit", line=name_tok.line, col=name_tok.col)
        if self.match(TokenType.ARROW):
            return_type = self.parse_type()

        requires, ensures = self._parse_contracts()
        if requires or ensures:
            while self.match(TokenType.NEWLINE):
                pass

        body = self.parse_block()
        return FnDecl(
            name=name_tok.value,
            params=params,
            return_type=return_type,
            body=body,
            type_params=type_params,
            line=start_tok.line,
            col=start_tok.col,
            requires=requires,
            ensures=ensures,
            is_async=is_async,
        )

    def parse_interface_decl(self) -> InterfaceDecl:
        start_tok = self.expect(TokenType.INTERFACE, code="W0006", msg="Expected 'interface'")
        name_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected interface name")
        type_params = self._parse_type_params()
        self.expect(TokenType.LBRACE, code="W0006", msg="Expected '{' to start interface body")

        methods: List[InterfaceMethod] = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            self._skip_statement_separators()
            if self.check(TokenType.RBRACE) or self.is_at_end():
                break
            self.match(TokenType.PUBLIC)
            fn_tok = self.expect(TokenType.FN, code="W0006", msg="Expected 'fn' in interface method declaration")
            m_name = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected method name after 'fn'")
            if self.check(TokenType.LBRACKET):
                tok = self.peek()
                raise ParserError(
                    message="Interface methods cannot declare independent generic type parameters",
                    line=tok.line,
                    col=tok.col,
                    file=self.file,
                    code="W2001",
                )
            self.expect(TokenType.LPAREN, code="W0006", msg="Expected '(' after interface method name")
            params = self._parse_param_list(allow_receiver=True)
            self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after interface method parameters")
            ret_type = NamedType("unit", line=m_name.line, col=m_name.col)
            if self.match(TokenType.ARROW):
                ret_type = self.parse_type()
            requires, ensures = self._parse_contracts()
            methods.append(
                InterfaceMethod(
                    name=m_name.value,
                    params=params,
                    return_type=ret_type,
                    line=fn_tok.line,
                    col=fn_tok.col,
                    requires=requires,
                    ensures=ensures,
                )
            )

        self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated interface body, expected '}'")
        return InterfaceDecl(name=name_tok.value, methods=methods, line=start_tok.line, col=start_tok.col, type_params=type_params)

    def parse_class_decl(self) -> ClassDecl:
        start_tok = self.expect(TokenType.CLASS, code="W0006", msg="Expected 'class'")
        name_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected class name")
        type_params = self._parse_type_params()

        superclass: Optional[str] = None
        if self.match(TokenType.EXTENDS):
            saved_sup = self.pos
            sup_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected superclass name after 'extends'")
            superclass = sup_tok.value
            while self.match(TokenType.DOT):
                superclass += "." + self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected superclass name after '.'").value
            if self.check(TokenType.LBRACKET):
                self.pos = saved_sup
                superclass = self.parse_type()

        interfaces: List[str] = []
        if self.match(TokenType.IMPLEMENTS):
            saved_intf = self.pos
            first_intf = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected interface name after 'implements'")
            intf_name = first_intf.value
            while self.match(TokenType.DOT):
                intf_name += "." + self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected interface name after '.'").value
            interfaces.append(intf_name)
            if self.check(TokenType.LBRACKET):
                self.pos = saved_intf
                interfaces[-1] = self.parse_type()
            while self.match(TokenType.COMMA):
                saved_next = self.pos
                next_intf = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected interface name after ','")
                next_name = next_intf.value
                while self.match(TokenType.DOT):
                    next_name += "." + self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected interface name after '.'").value
                interfaces.append(next_name)
                if self.check(TokenType.LBRACKET):
                    self.pos = saved_next
                    interfaces[-1] = self.parse_type()

        self.expect(TokenType.LBRACE, code="W0006", msg="Expected '{' to start class body")

        fields: List[ClassField] = []
        methods: List[MethodDecl] = []

        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            self._skip_statement_separators()
            if self.check(TokenType.RBRACE) or self.is_at_end():
                break
            is_public = True
            if self.match(TokenType.PUBLIC):
                is_public = True
            elif self.match(TokenType.PRIVATE):
                is_public = False

            if self.check(TokenType.FN):
                fn_tok = self.advance()
                m_name = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected method name after 'fn'")
                m_type_params = self._parse_type_params()
                if m_name.value == "init" and m_type_params:
                    raise ParserError(
                        message="Constructor 'init' cannot declare generic type parameters",
                        line=m_name.line,
                        col=m_name.col,
                        file=self.file,
                        code="W2001",
                    )
                self.expect(TokenType.LPAREN, code="W0006", msg="Expected '(' after method name")
                params = self._parse_param_list(allow_receiver=True)
                self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after method parameters")
                ret_type = NamedType("unit", line=m_name.line, col=m_name.col)
                if self.match(TokenType.ARROW):
                    ret_type = self.parse_type()
                requires, ensures = self._parse_contracts()
                if requires or ensures:
                    while self.match(TokenType.NEWLINE):
                        pass
                body = self.parse_block()
                methods.append(
                    MethodDecl(
                        name=m_name.value,
                        params=params,
                        return_type=ret_type,
                        body=body,
                        is_public=is_public,
                        line=fn_tok.line,
                        col=fn_tok.col,
                        requires=requires,
                        ensures=ensures,
                        type_params=m_type_params,
                    )
                )
            else:
                f_name = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected class field or method declaration")
                self.expect(TokenType.COLON, code="W0006", msg="Expected ':' after class field name")
                f_type = self.parse_type()
                fields.append(ClassField(name=f_name.value, type_ann=f_type, is_public=is_public, line=f_name.line, col=f_name.col))
                self.match(TokenType.COMMA)

        self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated class body, expected '}'")
        return ClassDecl(
            name=name_tok.value,
            superclass=superclass,
            type_params=type_params,
            interfaces=interfaces,
            fields=fields,
            methods=methods,
            line=start_tok.line,
            col=start_tok.col,
        )

    def _validate_irrefutable_tuple_pattern(self, pattern: Pattern) -> None:
        if not isinstance(pattern, TuplePattern):
            raise ParserError(
                message="Only tuple destructuring patterns are allowed in 'let' declarations",
                line=pattern.line,
                col=pattern.col,
                file=self.file,
                code="W0006",
            )
        for elem in pattern.elements:
            if isinstance(elem, (IdentifierPattern, WildcardPattern)):
                continue
            if isinstance(elem, TuplePattern):
                self._validate_irrefutable_tuple_pattern(elem)
                continue
            raise ParserError(
                message="Tuple destructuring in 'let' only supports identifiers, '_', or nested tuples",
                line=elem.line,
                col=elem.col,
                file=self.file,
                code="W0006",
            )

    def parse_let_decl(self) -> LetDecl:
        start_tok = self.expect(TokenType.LET, code="W0006", msg="Expected 'let'")
        is_mut = self.match(TokenType.MUT)

        pattern: Optional[Pattern] = None
        var_name = ""
        if self.check(TokenType.LPAREN):
            pattern = self.parse_pattern(allow_tuple=False)
            self._validate_irrefutable_tuple_pattern(pattern)
        else:
            name_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected variable name after 'let'")
            var_name = name_tok.value

        type_ann: Optional[TypeNode] = None
        if self.match(TokenType.COLON):
            type_ann = self.parse_type()

        self.expect(TokenType.ASSIGN, code="W0006", msg="Expected '=' after variable declaration")
        initializer = self.parse_expr()

        self._require_statement_end()
        return LetDecl(name=var_name, is_mut=is_mut, type_ann=type_ann, initializer=initializer, line=start_tok.line, col=start_tok.col, pattern=pattern)

    def parse_struct_decl(self) -> StructDecl:
        start_tok = self.expect(TokenType.STRUCT, code="W0006", msg="Expected 'struct'")
        name_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected struct name")
        type_params = self._parse_type_params()
        self.expect(TokenType.LBRACE, code="W0006", msg="Expected '{' to start struct body")

        fields: List[StructField] = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            self._skip_statement_separators()
            if self.check(TokenType.RBRACE) or self.is_at_end():
                break
            f_name = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected struct field name")
            self.expect(TokenType.COLON, code="W0006", msg="Expected ':' after struct field name")
            f_type = self.parse_type()
            f_requires: List[Expr] = []
            while (
                self.check(TokenType.IDENTIFIER)
                and self.at().value == "requires"
                and self.peek(1).type != TokenType.COLON
            ):
                self.advance()
                f_requires.append(self.parse_expr(allow_struct_init=False))
            fields.append(StructField(f_name.value, f_type, line=f_name.line, col=f_name.col, requires=f_requires))
            self.match(TokenType.COMMA)

        self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated struct body, expected '}'")
        return StructDecl(name=name_tok.value, fields=fields, type_params=type_params, line=start_tok.line, col=start_tok.col)

    def parse_enum_decl(self) -> EnumDecl:
        start_tok = self.expect(TokenType.ENUM, code="W0006", msg="Expected 'enum'")
        name_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected enum name")
        type_params = self._parse_type_params()
        self.expect(TokenType.LBRACE, code="W0006", msg="Expected '{' to start enum body")

        variants: List[VariantDecl] = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            self._skip_statement_separators()
            if self.check(TokenType.RBRACE) or self.is_at_end():
                break
            v_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected enum variant identifier")
            payload_types: List[TypeNode] = []
            if self.match(TokenType.LPAREN):
                if not self.check(TokenType.RPAREN):
                    payload_types.append(self.parse_type())
                    while self.match(TokenType.COMMA):
                        if self.check(TokenType.RPAREN):
                            break
                        payload_types.append(self.parse_type())
                self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after variant payload types")
            variants.append(VariantDecl(name=v_tok.value, payload_types=payload_types, line=v_tok.line, col=v_tok.col))
            self.match(TokenType.COMMA)

        self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated enum body, expected '}'")
        return EnumDecl(name=name_tok.value, variants=variants, type_params=type_params, line=start_tok.line, col=start_tok.col)

    def parse_import_decl(self) -> ImportDecl:
        start_tok = self.expect(TokenType.IMPORT, code="W0006", msg="Expected 'import'")
        path_tok = self.expect(TokenType.STR, code="W0006", msg="Expected string path for import")

        # Derive namespace from filename (e.g. "lexer.wysna" -> "lexer")
        raw_path = str(path_tok.value)
        base = os.path.basename(raw_path)
        namespace = base.split(".")[0] if "." in base else base

        if self.check(TokenType.IDENTIFIER) and self.at().value == "as":
            self.advance()
            namespace = self.expect(TokenType.IDENTIFIER).value
        return ImportDecl(path=raw_path, namespace=namespace, line=start_tok.line, col=start_tok.col)

    def parse_use_decl(self) -> UseDecl:
        start_tok = self.expect(TokenType.FROM, code="W0006", msg="Expected 'from'")

        # Module path parsing:
        # Can be string literal: from "std/text" use ...
        # OR dot-separated path: from app.domain.user use ...
        # OR relative dot-path: from .user use ..., from ..domain.user use ...
        if self.check(TokenType.STR):
            path_tok = self.advance()
            path = str(path_tok.value)
        else:
            leading_dots = ""
            while self.check(TokenType.DOT) or self.check(TokenType.DOT_DOT):
                if self.match(TokenType.DOT_DOT):
                    leading_dots += ".."
                else:
                    self.advance()
                    leading_dots += "."

            if not (self.check(TokenType.IDENTIFIER) or (self.check(TokenType.ASYNC) and self.peek(1).type != TokenType.FN)):
                tok = self.at()
                raise ParserError(
                    message=f"Expected module path after 'from', found '{tok.type.name}'",
                    line=tok.line,
                    col=tok.col,
                    file=self.file,
                    code="W0006",
                )

            parts = [self.expect(TokenType.IDENTIFIER).value]
            while self.match(TokenType.DOT):
                parts.append(self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected identifier after '.' in module path").value)
            path = leading_dots + ".".join(parts)

        if self.check(TokenType.USE) or (self.check(TokenType.IDENTIFIER) and self.at().value == "use"):
            self.advance()
        else:
            raise ParserError(
                message="Expected 'use' after module path in 'from ... use' statement",
                line=self.at().line,
                col=self.at().col,
                file=self.file,
                code="W0006",
            )

        symbols: List[UseSymbol] = []
        is_namespace = False
        namespace_alias: Optional[str] = None
        is_braced = False

        if self.match(TokenType.LBRACE):
            is_braced = True
            while not self.check(TokenType.RBRACE) and not self.is_at_end():
                self._skip_statement_separators()
                if self.check(TokenType.RBRACE):
                    break
                sym_start = self.at()
                parts = [self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected symbol name in 'use' block").value]
                while self.match(TokenType.DOT):
                    parts.append(self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected identifier after '.'").value)

                if len(parts) > 1:
                    subpath = ".".join(parts[:-1])
                    sym_name = parts[-1]
                else:
                    subpath = None
                    sym_name = parts[0]

                alias = None
                if self.check(TokenType.IDENTIFIER) and self.at().value == "as":
                    self.advance()
                    alias = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected identifier after 'as'").value

                symbols.append(UseSymbol(name=sym_name, alias=alias, subpath=subpath, line=sym_start.line, col=sym_start.col))

                if not self.match(TokenType.COMMA):
                    self._skip_statement_separators()
                    if not self.check(TokenType.RBRACE):
                        break

            self._skip_statement_separators()
            self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated 'use' block, expected '}'")
        else:
            sym_start = self.at()
            parts = [self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected symbol or module name after 'use'").value]
            while self.match(TokenType.DOT):
                parts.append(self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected identifier after '.'").value)

            if len(parts) > 1:
                subpath = ".".join(parts[:-1])
                sym_name = parts[-1]
            else:
                subpath = None
                sym_name = parts[0]

            alias = None
            if self.check(TokenType.IDENTIFIER) and self.at().value == "as":
                self.advance()
                alias = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected identifier after 'as'").value

            symbols.append(UseSymbol(name=sym_name, alias=alias, subpath=subpath, line=sym_start.line, col=sym_start.col))

        return UseDecl(
            path=path,
            symbols=symbols,
            is_namespace=is_namespace,
            namespace_alias=namespace_alias,
            is_braced=is_braced,
            line=start_tok.line,
            col=start_tok.col,
        )

    def parse_while_stmt(self) -> WhileStmt:
        start_tok = self.expect(TokenType.WHILE, code="W0006", msg="Expected 'while'")
        condition = self.parse_expr(allow_struct_init=False)
        body = self.parse_block()
        return WhileStmt(condition=condition, body=body, line=start_tok.line, col=start_tok.col)

    def parse_for_stmt(self) -> ForStmt:
        start_tok = self.expect(TokenType.FOR, code="W0006", msg="Expected 'for'")
        pattern = self.parse_pattern()
        if not isinstance(pattern, (IdentifierPattern, TuplePattern)):
            raise ParserError(
                message="For loop binding must be an identifier or tuple pattern",
                line=pattern.line,
                col=pattern.col,
                file=self.file,
                code="W0006",
            )
        self.expect(TokenType.IN, code="W0006", msg="Expected 'in' after for loop pattern")
        iterable = self.parse_expr(allow_struct_init=False)
        body = self.parse_block()
        return ForStmt(pattern=pattern, iterable=iterable, body=body, line=start_tok.line, col=start_tok.col)

    # Tokens that can never begin an expression. `return` followed by one of these
    # is a bare return; anything else is the returned expression. Deciding on token
    # identity (rather than on line numbers) is what keeps `return` free of
    # whitespace ambiguity: `return` newline `a + b` still returns `a + b`, and a
    # bare `return` is still a bare return because the next token is `}` or EOF.
    _EXPRESSION_TERMINATORS = frozenset({
        TokenType.RBRACE, TokenType.RPAREN, TokenType.RBRACKET, TokenType.COMMA,
        TokenType.ELSE, TokenType.ELIF,
    })

    def _starts_expression(self) -> bool:
        token = self.at()
        if token.type in self._EXPRESSION_TERMINATORS:
            return False
        if token.type == TokenType.EOF:
            return False
        # `return` immediately followed by a token that cannot open an expression
        # belongs to the enclosing construct (e.g. the next statement's keyword).
        return token.type in _EXPRESSION_STARTERS

    def parse_return_stmt(self) -> ReturnStmt:
        start_tok = self.expect(TokenType.RETURN, code="W0006", msg="Expected 'return'")
        if not self._starts_expression():
            return ReturnStmt(value=None, line=start_tok.line, col=start_tok.col)
        val = self.parse_expr()
        return ReturnStmt(value=val, line=start_tok.line, col=start_tok.col)

    def parse_block(self) -> Block:
        start_tok = self.expect(TokenType.LBRACE, code="W0006", msg="Expected '{' to start block")
        statements: List[Stmt] = []
        tail_expr: Optional[Expr] = None

        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            self._skip_statement_separators()
            if self.check(TokenType.RBRACE) or self.is_at_end():
                break
            stmt = self._recover_statement()
            if stmt is not None:
                statements.append(stmt)

        self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated block, expected '}'")

        # If the last statement is an ExprStmt, it serves as the tail expression of the block
        if statements and isinstance(statements[-1], ExprStmt):
            tail_expr = statements.pop().expr

        block = Block(statements=statements, tail_expr=tail_expr, line=start_tok.line, col=start_tok.col)
        block.end_line, block.end_col = self.tokens[self.pos - 1].line, self.tokens[self.pos - 1].col
        return block


    # ==========================================
    # Expression Parsing (Pratt Parser)
    # ==========================================

    def parse_expr(self, min_prec: int = 0, allow_struct_init: bool = True) -> Expr:
        left = self.parse_prefix_expr(allow_struct_init=allow_struct_init)

        # Postfix and Infix loop
        while True:
            # 1. Postfix operators (Calls, Member access, Indexing, Postfix '?')
            while True:
                if min_prec < 9 and self.check(TokenType.NEWLINE) and self.peek(1).type == TokenType.ELSE:
                    saved = self.pos
                    self.advance()
                    if self.check(TokenType.ELSE) and not self._begins_match_arm():
                        continue
                    self.pos = saved

                if self.check(TokenType.NEWLINE) and self.peek(1).type == TokenType.DOT:
                    self.advance()
                    continue

                if self.check(TokenType.LPAREN):
                    # Call expression
                    paren = self.advance()
                    args: List[Expr] = []
                    if not self.check(TokenType.RPAREN):
                        args.append(self.parse_expr())
                        while self.match(TokenType.COMMA):
                            if self.check(TokenType.RPAREN):
                                break
                            args.append(self.parse_expr())
                    self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after call arguments")
                    left = CallExpr(callee=left, args=args, line=paren.line, col=paren.col)

                elif self.check(TokenType.DOT):
                    # Member access or positional tuple index (.0, .1, ...)
                    dot = self.advance()
                    if self.check(TokenType.INT):
                        idx_tok = self.advance()
                        left = MemberExpr(object=left, property=str(idx_tok.value), line=dot.line, col=dot.col)
                    else:
                        prop_tok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected property name after '.'")
                        left = MemberExpr(object=left, property=prop_tok.value, line=dot.line, col=dot.col)

                elif self.check(TokenType.LBRACKET):
                    if isinstance(left, (IdentExpr, MemberExpr)):
                        saved = self.pos
                        type_args = self._try_parse_generic_type_args(allow_struct_init, allow_dot=True)
                        if type_args and isinstance(left, MemberExpr) and self.match(TokenType.LPAREN):
                            args = []
                            if not self.check(TokenType.RPAREN):
                                args.append(self.parse_expr())
                                while self.match(TokenType.COMMA):
                                    if self.check(TokenType.RPAREN):
                                        break
                                    args.append(self.parse_expr())
                            self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after generic module call")
                            left = CallExpr(left, args, type_args, line=left.line, col=left.col)
                            left.index_alternative = self._parse_index_alternative(saved, type_args)
                            continue
                        if allow_struct_init and isinstance(left, MemberExpr) and type_args and self._starts_struct_init_brace():
                            chain_name = self._member_chain_name(left)
                            if chain_name is not None:
                                left = self._parse_struct_init_body(chain_name, type_args, left.line, left.col)
                                continue
                        if type_args and self.check(TokenType.DOT) and self.peek(1).type == TokenType.IDENTIFIER:
                            is_primitive = (
                                len(type_args) == 1
                                and isinstance(type_args[0], NamedType)
                                and type_args[0].name in ("int", "float", "bool", "str", "unit", "none")
                            )
                            alt = None if is_primitive else self._parse_index_alternative(saved, type_args=None)
                            if alt is None:
                                dot = self.advance()
                                prop_tok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected property name after '.'")
                                member = MemberExpr(object=left, property=prop_tok.value, line=dot.line, col=dot.col)
                                member.enum_type_args = type_args
                                left = member
                                continue
                            bracket_tok = self.tokens[saved]
                            idx_expr = IndexExpr(object=left, index=alt, line=bracket_tok.line, col=bracket_tok.col)
                            idx_expr.type_args_alternative = type_args
                            left = idx_expr
                            continue
                        self.pos = saved
                    # Indexing
                    bracket = self.advance()
                    index_expr = self.parse_expr()
                    self.expect(TokenType.RBRACKET, code="W0006", msg="Expected ']' after index")
                    left = IndexExpr(object=left, index=index_expr, line=bracket.line, col=bracket.col)

                elif allow_struct_init and isinstance(left, MemberExpr) and self._starts_struct_init_brace():
                    chain_name = self._member_chain_name(left)
                    if chain_name is None:
                        break
                    left = self._parse_struct_init_body(chain_name, None, left.line, left.col)

                elif min_prec < 10 and self.check(TokenType.QUESTION):
                    # Postfix try / unwrap
                    q_tok = self.advance()
                    left = PostfixTryExpr(operand=left, line=q_tok.line, col=q_tok.col)

                elif min_prec < 10 and self.check(TokenType.ELSE) and not self._begins_match_arm():
                    # In-place error/optional handling: expr else err { ... } or expr else { ... }
                    else_tok = self.advance()
                    err_name: Optional[str] = None
                    if self.check(TokenType.IDENTIFIER):
                        err_tok = self.advance()
                        err_name = err_tok.value
                    handler_block = self.parse_block()
                    left = ElseErrExpr(operand=left, err_name=err_name, handler=handler_block, line=else_tok.line, col=else_tok.col)

                else:
                    break


            # 2. Infix operators check
            op_tok = self.at()
            prec = PRECEDENCE.get(op_tok.type, 0)
            if prec <= min_prec:
                break

            self.advance()  # consume operator token

            # Right associativity for assignment (=, +=, -=, *=) and coalesce (??)
            next_min_prec = (
                prec - 1
                if op_tok.type in (
                    TokenType.ASSIGN,
                    TokenType.PLUS_ASSIGN,
                    TokenType.MINUS_ASSIGN,
                    TokenType.STAR_ASSIGN,
                    TokenType.QUESTION_QUESTION,
                )
                else prec
            )
            right = self.parse_expr(min_prec=next_min_prec, allow_struct_init=allow_struct_init)


            if op_tok.type in (TokenType.ASSIGN, TokenType.PLUS_ASSIGN, TokenType.MINUS_ASSIGN, TokenType.STAR_ASSIGN):
                # Target validation
                if not isinstance(left, (IdentExpr, IndexExpr, MemberExpr)):
                    raise ParserError(
                        message=f"Invalid assignment target '{left.__class__.__name__}'",
                        line=op_tok.line,
                        col=op_tok.col,
                        file=self.file,
                        code="W2002"
                    )
                if op_tok.type == TokenType.ASSIGN:
                    left = AssignmentExpr(target=left, value=right, line=op_tok.line, col=op_tok.col)
                else:
                    rhs = BinaryExpr(left=clone_ast(left), op=op_tok.value[0], right=right, line=op_tok.line, col=op_tok.col)
                    left = AssignmentExpr(target=left, value=rhs, line=op_tok.line, col=op_tok.col)

            elif op_tok.type == TokenType.QUESTION_QUESTION:
                left = CoalesceExpr(left=left, right=right, line=op_tok.line, col=op_tok.col)

            elif op_tok.type in (TokenType.DOT_DOT, TokenType.DOT_DOT_EQ):
                inclusive = (op_tok.type == TokenType.DOT_DOT_EQ)
                left = RangeExpr(start=left, end=right, inclusive=inclusive, line=op_tok.line, col=op_tok.col)

            elif op_tok.type == TokenType.IN:
                member = MemberExpr(object=right, property="contains", line=op_tok.line, col=op_tok.col)
                call = CallExpr(callee=member, args=[left], line=op_tok.line, col=op_tok.col)
                call.is_in_operator = True
                left = call

            else:
                left = BinaryExpr(left=left, op=op_tok.value, right=right, line=op_tok.line, col=op_tok.col)

        return left

    def _parse_index_alternative(self, start, type_args=None):
        if type_args is not None:
            if len(type_args) != 1 or not isinstance(type_args[0], NamedType):
                return None
            if type_args[0].name in ("int", "float", "bool", "str", "unit", "none"):
                return None
        previous = self.pos
        self.pos = start + 1
        try:
            expression = self.parse_expr()
            self.expect(TokenType.RBRACKET)
            return expression
        except ParserError:
            return None
        finally:
            self.pos = previous

    def _member_chain_name(self, expr: Expr) -> Optional[str]:
        parts: List[str] = []
        current = expr
        while isinstance(current, MemberExpr):
            parts.append(current.property)
            current = current.object
        if not isinstance(current, IdentExpr):
            return None
        parts.append(current.name)
        parts.reverse()
        return ".".join(parts)

    def _parse_struct_init_body(
        self,
        struct_name: str,
        type_args: Optional[List[TypeNode]],
        line: int,
        col: int,
    ) -> StructInitExpr:
        self.advance()  # consume '{'
        fields: List[FieldInit] = []
        spread: Optional[Expr] = None
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            self._skip_statement_separators()
            if self.check(TokenType.RBRACE) or self.is_at_end():
                break
            if self.match(TokenType.DOT_DOT):
                dot_tok = self.tokens[self.pos - 1]
                if spread is not None:
                    raise ParserError(
                        message="Struct initializer accepts at most one '..' base expression",
                        line=dot_tok.line,
                        col=dot_tok.col,
                        file=self.file,
                        code="W0006",
                    )
                spread = self.parse_expr()
                self.match(TokenType.COMMA)
                continue
            f_tok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected struct field name")
            self.expect(TokenType.COLON, code="W0001", msg="Expected ':' after struct field name")
            f_val = self.parse_expr()
            fields.append(FieldInit(name=f_tok.value, value=f_val, line=f_tok.line, col=f_tok.col))
            self.match(TokenType.COMMA)
        self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated struct initializer, expected '}'")
        return StructInitExpr(struct_name=struct_name, fields=fields, type_args=type_args, line=line, col=col, spread=spread)

    def _starts_struct_init_brace(self) -> bool:
        return self.check(TokenType.LBRACE) and (
            self.peek(1).type == TokenType.RBRACE
            or self.peek(1).type == TokenType.DOT_DOT
            or (self.peek(1).type == TokenType.IDENTIFIER and self.peek(2).type == TokenType.COLON)
        )

    def _try_parse_generic_type_args(self, allow_struct_init: bool = True, allow_dot: bool = False, allow_pattern: bool = False) -> Optional[List[TypeNode]]:
        if not self.check(TokenType.LBRACKET):
            return None
        saved_pos = self.pos
        try:
            self.advance()  # consume '['
            if self.check(TokenType.RBRACKET):
                self.pos = saved_pos
                return None
            args: List[TypeNode] = [self.parse_type()]
            while self.match(TokenType.COMMA):
                if self.check(TokenType.RBRACKET):
                    break
                args.append(self.parse_type())
            if not self.match(TokenType.RBRACKET):
                self.pos = saved_pos
                return None
            # Only treat as generic args if followed by '(', '{', or (when enabled) '.Variant'
            if not (
                self.check(TokenType.LPAREN)
                or (allow_struct_init and self._starts_struct_init_brace())
                or (allow_pattern and self._starts_struct_pattern_brace())
                or (allow_dot and self.check(TokenType.DOT) and self.peek(1).type == TokenType.IDENTIFIER)
            ):
                self.pos = saved_pos
                return None
            return args
        except ParserError:
            self.pos = saved_pos
            return None

    def _begins_match_arm(self) -> bool:
        """Decides whether a `else` token starts a match arm rather than the postfix
        error-recovery operator.

        The two uses overlap on a single token. From here we see `else` at the head:
          `else => ...`          wildcard arm, nothing may consume it
          `else name => ...`     binding arm, nothing may consume it
          `else { ... }`         error recovery without a bound error name
          `else name { ... }`    error recovery binding the error to `name`

        Only the block forms are the operator, so the lookahead is exact and needs no
        context flag. Without this, `match x { 1 => 10 else => 30 }` swallows the
        `else` of the arm and reports "expected '{' to start block".
        """
        after = self.peek(1)
        if after.type == TokenType.LBRACE:
            return False
        if after.type == TokenType.IDENTIFIER and self.peek(2).type == TokenType.LBRACE:
            return False
        return True

    def parse_prefix_expr(self, allow_struct_init: bool = True) -> Expr:
        token = self.at()
        if self.match(TokenType.PIPE):
            params = []
            if not self.check(TokenType.PIPE):
                while True:
                    name = self.expect(TokenType.IDENTIFIER)
                    annotation = self.parse_type() if self.match(TokenType.COLON) else None
                    params.append(Param(name.value, annotation, name.line, name.col))
                    if not self.match(TokenType.COMMA):
                        break
            self.expect(TokenType.PIPE)
            returned = self.parse_type() if self.match(TokenType.ARROW) else None
            if self.match(TokenType.FAT_ARROW):
                body = Block([], self.parse_expr(), token.line, token.col)
            else:
                body = self.parse_block()
            return LambdaExpr(params, body, returned, token.line, token.col)

        # Literals
        if self.match(TokenType.INT):
            return IntLiteral(value=token.value, line=token.line, col=token.col)

        if self.match(TokenType.FLOAT):
            return FloatLiteral(value=token.value, line=token.line, col=token.col)

        if self.match(TokenType.STR):
            return StringLiteral(value=token.value, line=token.line, col=token.col)

        if self.match(TokenType.TRUE):
            return BoolLiteral(value=True, line=token.line, col=token.col)

        if self.match(TokenType.FALSE):
            return BoolLiteral(value=False, line=token.line, col=token.col)

        if self.match(TokenType.THIS):
            return ThisExpr(line=token.line, col=token.col)

        if self.match(TokenType.SUPER):
            return SuperExpr(line=token.line, col=token.col)

        # Identifiers & Named Struct Initializations
        if self.match(TokenType.IDENTIFIER) or (self.check(TokenType.ASYNC) and self.peek(1).type != TokenType.FN and self.advance()):
            ident_name = token.value

            if ident_name == "new" and self.check(TokenType.IDENTIFIER):
                raise ParserError(
                    message="The 'new' keyword has been removed; construct classes directly with 'ClassName(...)'",
                    line=token.line,
                    col=token.col,
                    file=self.file,
                    code="W0006",
                )

            type_args: Optional[List[TypeNode]] = None
            bracket_start = self.pos
            if self.check(TokenType.LBRACKET):
                type_args = self._try_parse_generic_type_args(allow_struct_init)

            # Check if this is a generic call: ident[T](args) or Box[T](args)
            if type_args and self.match(TokenType.LPAREN):
                call_args: List[Expr] = []
                if not self.check(TokenType.RPAREN):
                    call_args.append(self.parse_expr())
                    while self.match(TokenType.COMMA):
                        if self.check(TokenType.RPAREN):
                            break
                        call_args.append(self.parse_expr())
                self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after call arguments")
                call = CallExpr(
                    callee=IdentExpr(name=ident_name, line=token.line, col=token.col),
                    args=call_args,
                    type_args=type_args,
                    line=token.line,
                    col=token.col
                )
                call.index_alternative = self._parse_index_alternative(bracket_start, type_args)
                return call

            # Check if this is a Struct Initialization: Identifier { field: val, ... } or Identifier { ..base, ... }
            if allow_struct_init and self._starts_struct_init_brace():
                return self._parse_struct_init_body(ident_name, type_args, token.line, token.col)

            return IdentExpr(name=ident_name, line=token.line, col=token.col)

        if self.match(TokenType.AWAIT):
            operand = self.parse_expr(min_prec=10, allow_struct_init=allow_struct_init)
            return AwaitExpr(operand=operand, line=token.line, col=token.col)

        # Unary operations: '-' and 'not'.
        # SPEC §5 puts unary at Level 3, tighter than multiplicative (Level 4, prec=9).
        # Passing min_prec=9 ensures that operators with precedence <= 9 (*, /, %, +, -, ==, etc.)
        # bind outside the unary operand, ensuring '-a * b' parses as '(-a) * b',
        # 'not a == b' parses as '(not a) == b', and preserving exact int64 minimum literals
        # like '-9223372036854775808 * 1'.
        if self.match(TokenType.MINUS, TokenType.NOT):
            operand = self.parse_expr(min_prec=9, allow_struct_init=allow_struct_init)
            if token.value == "-" and isinstance(operand, IntLiteral):
                if operand.value == 9223372036854775808:
                    return IntLiteral(value=-9223372036854775808, line=token.line, col=token.col)
                if operand.value > 9223372036854775807:
                    raise ParserError(
                        message=f"Integer literal '-{operand.value}' is below the int64 minimum",
                        line=token.line,
                        col=token.col,
                        file=self.file,
                        code="W0002",
                    )
                return IntLiteral(value=-operand.value, line=token.line, col=token.col)
            return UnaryExpr(op=token.value, operand=operand, line=token.line, col=token.col)

        # Parenthesized expressions & Tuples
        if self.match(TokenType.LPAREN):
            if self.match(TokenType.RPAREN):
                return TupleExpr(elements=[], line=token.line, col=token.col)

            first_expr = self.parse_expr()
            if self.match(TokenType.COMMA):
                elements: List[Expr] = [first_expr]
                if not self.check(TokenType.RPAREN):
                    elements.append(self.parse_expr())
                    while self.match(TokenType.COMMA):
                        if self.check(TokenType.RPAREN):
                            break
                        elements.append(self.parse_expr())
                self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after tuple elements")
                return TupleExpr(elements=elements, line=token.line, col=token.col)

            self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after expression")
            return first_expr

        # List Literals
        if self.match(TokenType.LBRACKET):
            elements: List[Expr] = []
            if not self.check(TokenType.RBRACKET):
                elements.append(self.parse_expr())
                while self.match(TokenType.COMMA):
                    if self.check(TokenType.RBRACKET):
                        break
                    elements.append(self.parse_expr())
            self.expect(TokenType.RBRACKET, code="W0006", msg="Expected ']' after list elements")
            return ListExpr(elements=elements, line=token.line, col=token.col)

        # Dict Literals: { key: value, ... }
        if self.match(TokenType.LBRACE):
            entries: List[DictEntry] = []
            while not self.check(TokenType.RBRACE) and not self.is_at_end():
                self._skip_statement_separators()
                if self.check(TokenType.RBRACE) or self.is_at_end():
                    break
                k = self.parse_expr()
                self.expect(TokenType.COLON, code="W0001", msg="Expected ':' after dict key")
                v = self.parse_expr()
                entries.append(DictEntry(key=k, value=v, line=k.line, col=k.col))
                self.match(TokenType.COMMA)
            self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated dict literal, expected '}'")
            return DictExpr(entries=entries, line=token.line, col=token.col)


        # Control flow expressions: 'if' and 'match'
        if self.check(TokenType.IF):
            return self.parse_if_expr()

        if self.check(TokenType.MATCH):
            return self.parse_match_expr()

        if token.type == TokenType.AT:
            raise ParserError('Static attributes require a top-level function declaration',
                              file=self.file, line=token.line, col=token.col, code='W0006')
        raise ParserError(
            message=f"Unexpected token '{token.type.name}' in expression",
            line=token.line,
            col=token.col,
            file=self.file,
            code="W0001"
        )

    def parse_if_expr(self) -> IfExpr:
        start_tok = self.expect(TokenType.IF, code="W0006", msg="Expected 'if'")
        condition = self.parse_expr(allow_struct_init=False)
        then_branch = self.parse_block()

        elif_branches: List[ElifBranch] = []
        while True:
            self._skip_newlines_before_branch()
            if self.match(TokenType.ELIF):
                e_tok = self.tokens[self.pos - 1]
            elif (
                self.check(TokenType.ELSE)
                and self.pos + 1 < len(self.tokens)
                and self.tokens[self.pos + 1].type == TokenType.IF
            ):
                e_tok = self.advance()
                self.advance()
            else:
                break
            elif_cond = self.parse_expr(allow_struct_init=False)
            elif_body = self.parse_block()
            elif_branches.append(ElifBranch(condition=elif_cond, body=elif_body, line=e_tok.line, col=e_tok.col))

        else_branch: Optional[Block] = None
        self._skip_newlines_before_branch()
        if self.match(TokenType.ELSE):
            else_branch = self.parse_block()

        return IfExpr(
            condition=condition,
            then_branch=then_branch,
            elif_branches=elif_branches,
            else_branch=else_branch,
            line=start_tok.line,
            col=start_tok.col
        )

    def _skip_newlines_before_branch(self) -> None:
        saved = self.pos
        while self.match(TokenType.NEWLINE):
            pass
        if not (self.check(TokenType.ELIF) or self.check(TokenType.ELSE)):
            self.pos = saved

    def parse_match_expr(self) -> MatchExpr:
        start_tok = self.expect(TokenType.MATCH, code="W0006", msg="Expected 'match'")
        subject = self.parse_expr(allow_struct_init=False)
        self.expect(TokenType.LBRACE, code="W0006", msg="Expected '{' after match subject")

        arms: List[MatchArm] = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            self._skip_statement_separators()
            if self.check(TokenType.RBRACE) or self.is_at_end():
                break
            arm_tok = self.at()
            pattern: Pattern
            if self.match(TokenType.ELSE):
                raise ParserError(
                    message="Wildcard 'else =>' has been removed in match; use canonical '_ =>'",
                    line=arm_tok.line,
                    col=arm_tok.col,
                    file=self.file,
                    code="W0001",
                )
            else:
                pattern = self.parse_pattern(allow_tuple=False)
                if self.check(TokenType.COMMA):
                    comma_tok = self.at()
                    raise ParserError(
                        message="Unparenthesized tuple patterns are not allowed in match; use '(a, b) =>'",
                        line=comma_tok.line,
                        col=comma_tok.col,
                        file=self.file,
                        code="W0001",
                    )

            guard: Optional[Expr] = None
            if self.match(TokenType.IF):
                guard = self.parse_expr()

            self.expect(TokenType.FAT_ARROW, code="W0001", msg="Expected '=>' after match pattern")

            body: Block
            if self.check(TokenType.LBRACE):
                body = self.parse_block()
            else:
                # Single expression arm
                expr = self.parse_expr()
                body = Block(statements=[], tail_expr=expr, line=expr.line, col=expr.col)

            self.match(TokenType.COMMA)
            arms.append(MatchArm(pattern=pattern, body=body, guard=guard, line=arm_tok.line, col=arm_tok.col))

        self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated match body, expected '}'")
        return MatchExpr(subject=subject, arms=arms, line=start_tok.line, col=start_tok.col)

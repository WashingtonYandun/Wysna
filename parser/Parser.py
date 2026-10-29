import os
from typing import List, Optional
from tokens.Token import Token
from tokens.TokenType import TokenType
from lexer.Lexer import Lexer
from error.Error import ParserError, ParserErrors
from node.NodeType import (
    ExternFnDecl, LambdaExpr, FunctionType as AstFunctionType, Node, Program, Stmt, Expr, TypeNode, Pattern, Block,
    NamedType, GenericType, TypeParam, OptionalType, ListType, DictType, TupleType, ResultType, ErrorUnionType,
    IdentifierPattern, TuplePattern, EnumPattern, LiteralPattern, WildcardPattern,
    FnDecl, Param, StructDecl, StructField, EnumDecl, VariantDecl,
    InterfaceDecl, InterfaceMethod, ClassDecl, ClassField, MethodDecl,
    ImportDecl, LetDecl,
    WhileStmt, ForStmt, ReturnStmt, BreakStmt, ContinueStmt, ExprStmt,
    IntLiteral, FloatLiteral, StringLiteral, BoolLiteral, NoneLiteral,
    IdentExpr, ThisExpr, SuperExpr, NewExpr,
    UnaryExpr, BinaryExpr, CoalesceExpr, RangeExpr, PostfixTryExpr, ElseErrExpr,
    CallExpr, MemberExpr, IndexExpr, AssignmentExpr,
    StructInitExpr, FieldInit, ListExpr, DictExpr, DictEntry, TupleExpr,
    IfExpr, ElifBranch, MatchExpr, MatchArm
)


# Infix Precedence Binding Power (higher = binds tighter)
PRECEDENCE = {
    TokenType.ASSIGN: 1,             # '=' (right-associative)
    TokenType.QUESTION_QUESTION: 2,  # '??' (right-associative)
    TokenType.OR: 3,                 # 'or'
    TokenType.AND: 4,                # 'and'
    TokenType.EQ: 5,                 # '=='
    TokenType.NOT_EQ: 5,             # '!='
    TokenType.LT: 6,                 # '<'
    TokenType.LTE: 6,                # '<='
    TokenType.GT: 6,                 # '>'
    TokenType.GTE: 6,                # '>='
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
    TokenType.NONE, TokenType.IDENTIFIER, TokenType.THIS, TokenType.SUPER, TokenType.NEW,
    TokenType.MINUS, TokenType.NOT, TokenType.LPAREN, TokenType.LBRACKET, TokenType.LBRACE,
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

    def parse(self, source: str, show_tokens: bool = False, *, tokens=None, recover=False) -> Program:
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
        except RecursionError:
            raise ParserError(
                message="Maximum syntax nesting depth exceeded",
                line=self.at().line,
                col=self.at().col,
                file=self.file,
                code="W0006",
            )

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
            if not self.recover:
                raise
            self.diagnostics.append(error)
            if len(self.diagnostics) >= 100:
                raise ParserErrors(self.diagnostics)
            self._synchronize(start, allow_top_level)
            return None

    def _synchronize(self, start, top_level):
        starters = {TokenType.LET, TokenType.FN, TokenType.STRUCT, TokenType.ENUM,
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
        token = self.at()
        err_msg = msg if msg else f"Expected token '{type_.name}', found '{token.type.name}'"
        raise ParserError(message=err_msg, line=token.line, col=token.col, file=self.file, code=code)

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
            inner_type = self.parse_type()
            self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after grouped type")
            return self._parse_type_suffix(inner_type, token.line, token.col)
        if not self.check(TokenType.IDENTIFIER):
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

    def parse_pattern(self, allow_tuple: bool = True) -> Pattern:
        token = self.at()

        if self.match(TokenType.LPAREN):
            elements: List[Pattern] = []
            if not self.check(TokenType.RPAREN):
                elements.append(self.parse_pattern(allow_tuple=False))
                while self.match(TokenType.COMMA):
                    if self.check(TokenType.RPAREN):
                        break
                    elements.append(self.parse_pattern(allow_tuple=False))
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
                variant_tok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected enum variant name after '.'")
                payload_args: List[Pattern] = []
                if self.match(TokenType.LPAREN):
                    if not self.check(TokenType.RPAREN):
                        payload_args.append(self.parse_pattern(allow_tuple=False))
                        while self.match(TokenType.COMMA):
                            if self.check(TokenType.RPAREN):
                                break
                            payload_args.append(self.parse_pattern(allow_tuple=False))
                    self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after pattern arguments")
                return EnumPattern(enum_name=first_name, variant=variant_tok.value, args=payload_args, line=token.line, col=token.col)

            # Check for unqualified Enum variant with payload: Some(x), Ok(v), Err(e)
            if self.match(TokenType.LPAREN):
                payload_args = []
                if not self.check(TokenType.RPAREN):
                    payload_args.append(self.parse_pattern(allow_tuple=False))
                    while self.match(TokenType.COMMA):
                        if self.check(TokenType.RPAREN):
                            break
                        payload_args.append(self.parse_pattern(allow_tuple=False))
                self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after pattern arguments")
                return EnumPattern(enum_name=None, variant=first_name, args=payload_args, line=token.line, col=token.col)

            if first_name in ("None", "none"):
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
            literal = IntLiteral(value=-magnitude, line=token.line, col=token.col)
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

    def parse_stmt(self, allow_top_level: bool = False) -> Stmt:
        if self.match(TokenType.PUB):
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
        if self.check(TokenType.FN) or self.check(TokenType.STRUCT) or self.check(TokenType.ENUM) or self.check(TokenType.INTERFACE) or self.check(TokenType.CLASS) or self.check(TokenType.IMPORT):
            if not allow_top_level:
                tok = self.at()
                raise ParserError(
                    message=f"Top-level declaration '{tok.value}' is not allowed inside a block",
                    line=tok.line,
                    col=tok.col,
                    file=self.file,
                    code="W0006",
                )
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

    def parse_fn_decl(self) -> FnDecl:
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

        body = self.parse_block()
        return FnDecl(name=name_tok.value, params=params, return_type=return_type, body=body, type_params=type_params, line=start_tok.line, col=start_tok.col)

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
            self.expect(TokenType.LPAREN, code="W0006", msg="Expected '(' after interface method name")
            params = self._parse_param_list(allow_receiver=True)
            self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after interface method parameters")
            ret_type = NamedType("unit", line=m_name.line, col=m_name.col)
            if self.match(TokenType.ARROW):
                ret_type = self.parse_type()
            methods.append(InterfaceMethod(name=m_name.value, params=params, return_type=ret_type, line=fn_tok.line, col=fn_tok.col))

        self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated interface body, expected '}'")
        return InterfaceDecl(name=name_tok.value, methods=methods, line=start_tok.line, col=start_tok.col, type_params=type_params)

    def parse_class_decl(self) -> ClassDecl:
        start_tok = self.expect(TokenType.CLASS, code="W0006", msg="Expected 'class'")
        name_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected class name")
        type_params = self._parse_type_params()

        superclass: Optional[str] = None
        if self.match(TokenType.EXTENDS):
            sup_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected superclass name after 'extends'")
            superclass = sup_tok.value
            if self.check(TokenType.LBRACKET):
                self.pos -= 1
                superclass = self.parse_type()

        interfaces: List[str] = []
        if self.match(TokenType.IMPLEMENTS):
            first_intf = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected interface name after 'implements'")
            interfaces.append(first_intf.value)
            if self.check(TokenType.LBRACKET):
                self.pos -= 1
                interfaces[-1] = self.parse_type()
            while self.match(TokenType.COMMA):
                next_intf = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected interface name after ','")
                interfaces.append(next_intf.value)
                if self.check(TokenType.LBRACKET):
                    self.pos -= 1
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
                self.expect(TokenType.LPAREN, code="W0006", msg="Expected '(' after method name")
                params = self._parse_param_list(allow_receiver=True)
                self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after method parameters")
                ret_type = NamedType("unit", line=m_name.line, col=m_name.col)
                if self.match(TokenType.ARROW):
                    ret_type = self.parse_type()
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

    def parse_let_decl(self) -> LetDecl:
        start_tok = self.expect(TokenType.LET, code="W0006", msg="Expected 'let'")
        is_mut = self.match(TokenType.MUT)

        name_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected variable name after 'let'")
        type_ann: Optional[TypeNode] = None
        if self.match(TokenType.COLON):
            type_ann = self.parse_type()

        self.expect(TokenType.ASSIGN, code="W0006", msg="Expected '=' after variable declaration")
        initializer = self.parse_expr()

        self._require_statement_end()
        return LetDecl(name=name_tok.value, is_mut=is_mut, type_ann=type_ann, initializer=initializer, line=start_tok.line, col=start_tok.col)

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
            fields.append(StructField(f_name.value, f_type, line=f_name.line, col=f_name.col))
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
                if self.check(TokenType.NEWLINE) and self.peek(1).type == TokenType.ELSE:
                    saved = self.pos
                    self.advance()
                    if self.check(TokenType.ELSE) and not self._begins_match_arm():
                        continue
                    self.pos = saved

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
                    # Member access
                    dot = self.advance()
                    prop_tok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected property name after '.'")
                    left = MemberExpr(object=left, property=prop_tok.value, line=dot.line, col=dot.col)

                elif self.check(TokenType.LBRACKET):
                    if isinstance(left, MemberExpr):
                        saved = self.pos
                        type_args = self._try_parse_generic_type_args()
                        if type_args and self.match(TokenType.LPAREN):
                            args = []
                            if not self.check(TokenType.RPAREN):
                                args.append(self.parse_expr())
                                while self.match(TokenType.COMMA):
                                    if self.check(TokenType.RPAREN):
                                        break
                                    args.append(self.parse_expr())
                            self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after generic module call")
                            left = CallExpr(left, args, type_args, line=left.line, col=left.col)
                            left.index_alternative = self._parse_index_alternative(saved)
                            continue
                        self.pos = saved
                    # Indexing
                    bracket = self.advance()
                    index_expr = self.parse_expr()
                    self.expect(TokenType.RBRACKET, code="W0006", msg="Expected ']' after index")
                    left = IndexExpr(object=left, index=index_expr, line=bracket.line, col=bracket.col)

                elif self.check(TokenType.QUESTION):
                    # Postfix try / unwrap
                    q_tok = self.advance()
                    left = PostfixTryExpr(operand=left, line=q_tok.line, col=q_tok.col)

                elif self.check(TokenType.ELSE) and not self._begins_match_arm():
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

            # Right associativity for assignment (=) and coalesce (??)
            next_min_prec = prec - 1 if op_tok.type in (TokenType.ASSIGN, TokenType.QUESTION_QUESTION) else prec
            right = self.parse_expr(min_prec=next_min_prec, allow_struct_init=allow_struct_init)


            if op_tok.type == TokenType.ASSIGN:
                # Target validation
                if not isinstance(left, (IdentExpr, IndexExpr, MemberExpr)):
                    raise ParserError(
                        message=f"Invalid assignment target '{left.__class__.__name__}'",
                        line=op_tok.line,
                        col=op_tok.col,
                        file=self.file,
                        code="W2002"
                    )
                left = AssignmentExpr(target=left, value=right, line=op_tok.line, col=op_tok.col)

            elif op_tok.type == TokenType.QUESTION_QUESTION:
                left = CoalesceExpr(left=left, right=right, line=op_tok.line, col=op_tok.col)

            elif op_tok.type in (TokenType.DOT_DOT, TokenType.DOT_DOT_EQ):
                inclusive = (op_tok.type == TokenType.DOT_DOT_EQ)
                left = RangeExpr(start=left, end=right, inclusive=inclusive, line=op_tok.line, col=op_tok.col)

            else:
                left = BinaryExpr(left=left, op=op_tok.value, right=right, line=op_tok.line, col=op_tok.col)

        return left

    def _parse_index_alternative(self, start):
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

    def _try_parse_generic_type_args(self, allow_struct_init: bool = True) -> Optional[List[TypeNode]]:
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
            # Only treat as generic args if followed by '{' or '('
            if not (self.check(TokenType.LPAREN) or (allow_struct_init and self.check(TokenType.LBRACE)
                    and (self.peek(1).type == TokenType.RBRACE or
                         (self.peek(1).type == TokenType.IDENTIFIER and self.peek(2).type == TokenType.COLON)))):
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

        if self.match(TokenType.NONE):
            return NoneLiteral(line=token.line, col=token.col)

        if self.match(TokenType.THIS):
            return ThisExpr(line=token.line, col=token.col)

        if self.match(TokenType.SUPER):
            return SuperExpr(line=token.line, col=token.col)

        if self.match(TokenType.NEW):
            cls_tok = self.expect(TokenType.IDENTIFIER, code="W0006", msg="Expected class name after 'new'")
            class_name = cls_tok.value
            while self.match(TokenType.DOT):
                class_name += "." + self.expect(TokenType.IDENTIFIER).value
            type_args = self._try_parse_generic_type_args(False) if self.check(TokenType.LBRACKET) else None
            self.expect(TokenType.LPAREN, code="W0006", msg="Expected '(' after class name in 'new' expression")
            args: List[Expr] = []
            if not self.check(TokenType.RPAREN):
                args.append(self.parse_expr())
                while self.match(TokenType.COMMA):
                    if self.check(TokenType.RPAREN):
                        break
                    args.append(self.parse_expr())
            self.expect(TokenType.RPAREN, code="W0006", msg="Expected ')' after 'new' arguments")
            expression = NewExpr(class_name=class_name, args=args, line=token.line, col=token.col)
            expression.type_args = type_args
            return expression

        # Identifiers & Named Struct Initializations
        if self.match(TokenType.IDENTIFIER):
            ident_name = token.value

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
                call.index_alternative = self._parse_index_alternative(bracket_start)
                return call

            # Check if this is a Struct Initialization: Identifier { field: val, ... } or Identifier[T] { field: val, ... }
            if allow_struct_init and self.check(TokenType.LBRACE):
                # Disambiguate struct init from block: next must be IDENTIFIER followed by COLON, or empty RBRACE
                if self.peek(1).type == TokenType.RBRACE or (self.peek(1).type == TokenType.IDENTIFIER and self.peek(2).type == TokenType.COLON):
                    self.advance()  # consume '{'
                    fields: List[FieldInit] = []
                    while not self.check(TokenType.RBRACE) and not self.is_at_end():
                        self._skip_statement_separators()
                        if self.check(TokenType.RBRACE) or self.is_at_end():
                            break
                        f_tok = self.expect(TokenType.IDENTIFIER, code="W0001", msg="Expected struct field name")
                        self.expect(TokenType.COLON, code="W0001", msg="Expected ':' after struct field name")
                        f_val = self.parse_expr()
                        fields.append(FieldInit(name=f_tok.value, value=f_val, line=f_tok.line, col=f_tok.col))
                        self.match(TokenType.COMMA)
                    self.expect(TokenType.RBRACE, code="W0006", msg="Unterminated struct initializer, expected '}'")
                    return StructInitExpr(struct_name=ident_name, fields=fields, type_args=type_args, line=token.line, col=token.col)

            return IdentExpr(name=ident_name, line=token.line, col=token.col)

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
            if not self.match(TokenType.ELIF):
                break
            e_tok = self.tokens[self.pos - 1]
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
                pattern = WildcardPattern(line=arm_tok.line, col=arm_tok.col)
            else:
                pattern = self.parse_pattern()

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

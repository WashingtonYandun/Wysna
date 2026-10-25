from typing import Dict, Optional
from checker.WysnaType import (
    WysnaType, IntType, FloatType, BoolType, StrType, UnitType, NoneType,
    OptionalType, ListType, DictType, TupleType, StructType, EnumType,
    ResultType, FunctionType, AnyType
)
from error.Error import TypeError

class Symbol:
    def __init__(self, name: str, type: WysnaType, is_mut: bool = False, kind: str = "var", line: int = 1, col: int = 1):
        self.name = name
        self.type = type
        self.is_mut = is_mut
        self.kind = kind  # 'var', 'param', 'fn', 'struct', 'enum', 'builtin'
        self.line = line
        self.col = col

    def __repr__(self) -> str:
        mut_str = "mut " if self.is_mut else ""
        return f"Symbol({self.kind} {mut_str}{self.name}: {self.type})"


class Scope:
    def __init__(self, parent: Optional['Scope'] = None, is_function_scope: bool = False):
        self.parent = parent
        self.is_function_scope = is_function_scope
        self.symbols: Dict[str, Symbol] = {}
        self.types: Dict[str, WysnaType] = {}

    def define_symbol(self, symbol: Symbol, file: str = "<stdin>") -> None:
        if symbol.name in self.symbols:
            prev = self.symbols[symbol.name]
            raise TypeError(
                message=f"Duplicate declaration of symbol '{symbol.name}' (previously declared at line {prev.line}:{prev.col})",
                line=symbol.line,
                col=symbol.col,
                file=file,
                code="W1002"
            )
        symbol.file = file
        self.symbols[symbol.name] = symbol

    def lookup_symbol(self, name: str) -> Optional[Symbol]:
        if name in self.symbols:
            return self.symbols[name]
        if self.parent is not None:
            return self.parent.lookup_symbol(name)
        return None

    def define_type(self, name: str, wysna_type: WysnaType, line: int = 1, col: int = 1, file: str = "<stdin>") -> None:
        if name in self.types:
            raise TypeError(
                message=f"Duplicate type definition for '{name}'",
                line=line,
                col=col,
                file=file,
                code="W1002"
            )
        self.types[name] = wysna_type

    def lookup_type(self, name: str) -> Optional[WysnaType]:
        if name in self.types:
            return self.types[name]
        if self.parent is not None:
            return self.parent.lookup_type(name)
        return None


def create_global_scope() -> Scope:
    """Instantiate standard global scope pre-populated with built-in types and functions."""
    scope = Scope()

    # 1. Built-in Types
    scope.define_type("int", IntType)
    scope.define_type("int32", IntType)
    scope.define_type("int64", IntType)
    scope.define_type("double", FloatType)
    from checker.WysnaType import OpaqueType
    scope.define_type("opaque", OpaqueType)
    scope.define_type("float", FloatType)
    scope.define_type("bool", BoolType)
    scope.define_type("str", StrType)
    scope.define_type("unit", UnitType)
    scope.define_type("none", NoneType)
    scope.define_type("IoError", StructType("IoError", {"message": StrType}))
    scope.define_type("NetError", StructType("NetError", {"message": StrType}))

    # 2. Built-in Functions
    # print(any...) -> unit
    scope.define_symbol(Symbol("print", FunctionType([AnyType()], UnitType), kind="builtin"))
    scope.define_symbol(Symbol("eprint", FunctionType([AnyType()], UnitType), kind="builtin"))
    scope.define_symbol(Symbol("exit", FunctionType([IntType], UnitType), kind="builtin"))
    
    # conversions & string primitives
    scope.define_symbol(Symbol("float", FunctionType([AnyType()], FloatType), kind="builtin"))
    scope.define_symbol(Symbol("int", FunctionType([AnyType()], IntType), kind="builtin"))
    scope.define_symbol(Symbol("str", FunctionType([AnyType()], StrType), kind="builtin"))
    scope.define_symbol(Symbol("bytes", FunctionType([StrType], ListType(IntType)), kind="builtin"))
    scope.define_symbol(Symbol("ord", FunctionType([StrType], IntType), kind="builtin"))
    scope.define_symbol(Symbol("chr", FunctionType([IntType], StrType), kind="builtin"))
    # slice() is polymorphic over str and list[T]; the checker validates each case and
    # computes the result type, so this entry only has to make the name resolvable.
    scope.define_symbol(Symbol("slice", FunctionType([AnyType(), IntType, IntType], AnyType()), kind="builtin"))

    scope.define_symbol(Symbol("join_strings", FunctionType([ListType(StrType), StrType], StrType), kind="builtin"))

    # File I/O & CLI
    io_err = scope.lookup_type("IoError")
    scope.define_symbol(Symbol("read_file", FunctionType([StrType], ResultType(StrType, io_err)), kind="builtin"))
    scope.define_symbol(Symbol("write_file", FunctionType([StrType, StrType], ResultType(UnitType, io_err)), kind="builtin"))
    scope.define_symbol(Symbol("args", FunctionType([], ListType(StrType)), kind="builtin"))
    scope.define_symbol(Symbol("env", FunctionType([StrType], OptionalType(StrType)), kind="builtin"))
    scope.define_symbol(Symbol("unix_time", FunctionType([], IntType), kind="builtin"))

    # Special generic builtins (typechecked contextually by checker)
    scope.define_symbol(Symbol("len", FunctionType([AnyType()], IntType), kind="builtin"))
    scope.define_symbol(Symbol("enumerate", FunctionType([AnyType()], AnyType()), kind="builtin"))
    scope.define_symbol(Symbol("dict_items", FunctionType([AnyType()], AnyType()), kind="builtin"))

    scope.define_symbol(Symbol("weak", FunctionType([AnyType()], AnyType()), kind="builtin"))
    scope.define_symbol(Symbol("upgrade", FunctionType([AnyType()], AnyType()), kind="builtin"))
    return scope


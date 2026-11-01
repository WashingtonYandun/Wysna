from typing import Dict, List, Optional, Any

class WysnaType:
    """Base class for semantic types in Wysna."""
    def __eq__(self, other: Any) -> bool:
        raise NotImplementedError

    def __hash__(self) -> int:
        return hash(str(self))

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        return self.__str__()


class PrimitiveType(WysnaType):
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, PrimitiveType) and self.name == other.name

    def __str__(self) -> str:
        return self.name


IntType = PrimitiveType("int")
FloatType = PrimitiveType("float")
BoolType = PrimitiveType("bool")
StrType = PrimitiveType("str")
UnitType = PrimitiveType("unit")
NoneType = PrimitiveType("none")
OpaqueType = PrimitiveType("opaque")


class OptionalType(WysnaType):
    def __init__(self, inner: WysnaType):
        self.inner = inner

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, OptionalType):
            return self.inner == other.inner
        if isinstance(other, EnumType) and getattr(other, "template_name", None) == "Option" and getattr(other, "type_args", None) and len(other.type_args) == 1:
            return self.inner == other.type_args[0]
        return False

    def __str__(self) -> str:
        return f"{self.inner}?"


class WeakType(WysnaType):
    def __init__(self, target: WysnaType):
        self.target = target

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, WeakType) and self.target == other.target

    def __str__(self) -> str:
        return f"Weak[{self.target}]"


class ListType(WysnaType):
    def __init__(self, element: WysnaType):
        self.element = element

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, ListType) and self.element == other.element

    def __str__(self) -> str:
        return f"list[{self.element}]"


class DictType(WysnaType):
    def __init__(self, key: WysnaType, value: WysnaType):
        self.key = key
        self.value = value

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, DictType) and self.key == other.key and self.value == other.value

    def __str__(self) -> str:
        return f"dict[{self.key}, {self.value}]"


class TupleType(WysnaType):
    def __init__(self, elements: List[WysnaType]):
        self.elements = elements

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, TupleType) and self.elements == other.elements

    def __str__(self) -> str:
        elems = ", ".join(str(e) for e in self.elements)
        return f"tuple[{elems}]"


class StructType(WysnaType):
    def __init__(self, name: str, fields: Dict[str, WysnaType]):
        self.name = name
        self.fields = fields

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, StructType) and self.name == other.name

    def __str__(self) -> str:
        return self.name


class EnumType(WysnaType):
    def __init__(self, name: str, variants: List[str], variant_payloads: Optional[Dict[str, List[WysnaType]]] = None):
        self.name = name
        self.variants = variants
        self.variant_payloads: Dict[str, List[WysnaType]] = variant_payloads if variant_payloads is not None else {v: [] for v in variants}
        self.template_name: Optional[str] = None
        self.type_args: Optional[List[WysnaType]] = None

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, EnumType):
            return self.name == other.name
        if self.template_name == "Option" and self.type_args and len(self.type_args) == 1 and isinstance(other, OptionalType):
            return self.type_args[0] == other.inner
        if self.template_name == "Result" and self.type_args and len(self.type_args) == 2 and isinstance(other, ResultType):
            return self.type_args[0] == other.ok and self.type_args[1] == other.err
        return False

    def __str__(self) -> str:
        return self.name


class ResultType(WysnaType):
    def __init__(self, ok: WysnaType, err: WysnaType):
        self.ok = ok
        self.err = err

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, ResultType):
            return self.ok == other.ok and self.err == other.err
        if isinstance(other, EnumType) and getattr(other, "template_name", None) == "Result" and getattr(other, "type_args", None) and len(other.type_args) == 2:
            return self.ok == other.type_args[0] and self.err == other.type_args[1]
        return False

    def __str__(self) -> str:
        return f"{self.ok} ! {self.err}"


class ErrorUnionType(WysnaType):
    """A statically known, closed set of alternative error payload types."""
    def __init__(self, alternatives: List[WysnaType]):
        unique: List[WysnaType] = []
        for alternative in alternatives:
            if not any(alternative == existing for existing in unique):
                unique.append(alternative)
        self.alternatives = tuple(sorted(unique, key=str))

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, ErrorUnionType) and self.alternatives == other.alternatives

    def __str__(self) -> str:
        return " | ".join(str(alternative) for alternative in self.alternatives)


def is_error_channel_assignable(source: WysnaType, target: WysnaType) -> bool:
    """Compatibility adapter to the canonical session-local relation facade."""
    from checker.TypeInterner import TypeInterner
    from checker.TypeRelations import TypeRelations
    return TypeRelations(TypeInterner()).is_error_channel_assignable(source, target)


class FunctionType(WysnaType):
    def __init__(self, params: List[WysnaType], ret: WysnaType, is_mutating: bool = False):
        self.params = params
        self.ret = ret
        self.is_mutating = is_mutating

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, FunctionType) and self.params == other.params and self.ret == other.ret

    def __str__(self) -> str:
        params_str = ", ".join(str(p) for p in self.params)
        return f"fn({params_str}) -> {self.ret}"


class InterfaceType(WysnaType):
    def __init__(self, name: str, methods: Optional[Dict[str, FunctionType]] = None):
        self.name = name
        self.methods: Dict[str, FunctionType] = methods if methods is not None else {}

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, InterfaceType) and self.name == other.name

    def __str__(self) -> str:
        return self.name


class ClassFieldInfo:
    def __init__(self, type: WysnaType, is_public: bool, owner_class: str):
        self.type = type
        self.is_public = is_public
        self.owner_class = owner_class


class ClassMethodInfo:
    def __init__(self, fn_type: FunctionType, is_public: bool, owner_class: str):
        self.fn_type = fn_type
        self.is_public = is_public
        self.owner_class = owner_class


class ClassType(WysnaType):
    def __init__(
        self,
        name: str,
        superclass: Optional['ClassType'] = None,
        interfaces: Optional[List[InterfaceType]] = None,
        fields: Optional[Dict[str, ClassFieldInfo]] = None,
        methods: Optional[Dict[str, ClassMethodInfo]] = None,
        init_type: Optional[FunctionType] = None,
    ):
        self.name = name
        self.superclass = superclass
        self.interfaces: List[InterfaceType] = interfaces if interfaces is not None else []
        self.fields: Dict[str, ClassFieldInfo] = fields if fields is not None else {}
        self.methods: Dict[str, ClassMethodInfo] = methods if methods is not None else {}
        self.init_type = init_type

    def is_subclass_of(self, target_class_name: str) -> bool:
        curr: Optional[ClassType] = self
        while curr is not None:
            if curr.name == target_class_name:
                return True
            curr = curr.superclass
        return False

    def implements_interface(self, target_intf_name: str) -> bool:
        curr: Optional[ClassType] = self
        while curr is not None:
            for intf in curr.interfaces:
                if intf.name == target_intf_name:
                    return True
            curr = curr.superclass
        return False

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, ClassType) and self.name == other.name

    def __str__(self) -> str:
        return self.name


class ModuleType(WysnaType):
    def __init__(self, name: str, scope: Any):
        self.name = name
        self.scope = scope

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, ModuleType) and self.name == other.name

    def __str__(self) -> str:
        return f"module {self.name}"


class AnyType(WysnaType):
    """Wildcard type for variadic builtins like print()."""
    def __eq__(self, other: Any) -> bool:
        return isinstance(other, AnyType)

    def __hash__(self) -> int:
        return hash("AnyType")

    def __str__(self) -> str:
        return "any"


class TypeParamType(WysnaType):
    """Represents a type parameter in generic declarations: T, U, A, B."""
    def __init__(self, name: str, constraint: Optional[WysnaType] = None, owner: Optional[str] = None):
        self.name = name
        self.constraint = constraint
        self.owner = owner

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, TypeParamType) and self.name == other.name and self.owner == other.owner

    def __str__(self) -> str:
        return self.name


class GenericStructTemplate(WysnaType):
    """Represents an uninstantiated generic struct template: struct Box[T] { val: T }"""
    def __init__(self, name: str, type_params: List[Any], ast_decl: Any):
        self.name = name
        self.type_params = type_params
        self.ast_decl = ast_decl

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, GenericStructTemplate) and self.name == other.name

    def __str__(self) -> str:
        params_str = ", ".join(p.name if hasattr(p, "name") else str(p) for p in self.type_params)
        return f"{self.name}[{params_str}]"


class GenericFnTemplate(WysnaType):
    """Represents an uninstantiated generic function template: fn id[T](x: T) -> T"""
    def __init__(self, name: str, type_params: List[Any], ast_decl: Any):
        self.name = name
        self.type_params = type_params
        self.ast_decl = ast_decl

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, GenericFnTemplate) and self.name == other.name

    def __str__(self) -> str:
        params_str = ", ".join(p.name if hasattr(p, "name") else str(p) for p in self.type_params)
        return f"{self.name}[{params_str}]"


class GenericEnumTemplate(WysnaType):
    """Represents an uninstantiated generic enum template: enum Option[T] { Some(T), None }"""
    def __init__(self, name: str, type_params: List[Any], ast_decl: Any):
        self.name = name
        self.type_params = type_params
        self.ast_decl = ast_decl

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, GenericEnumTemplate) and self.name == other.name

    def __str__(self) -> str:
        params_str = ", ".join(p.name if hasattr(p, "name") else str(p) for p in self.type_params)
        return f"{self.name}[{params_str}]"


def is_assignable(source: WysnaType, target: WysnaType) -> bool:
    """Compatibility adapter to the canonical session-local relation facade."""
    from checker.TypeInterner import TypeInterner
    from checker.TypeRelations import TypeRelations
    return TypeRelations(TypeInterner()).is_assignable(source, target)

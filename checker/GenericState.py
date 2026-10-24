"""Generic identity, naming, recursion bounds, and telemetry contracts."""
from enum import Enum
from typing import Any, Dict, Optional, Set, Tuple, NamedTuple, Sequence
from checker.WysnaType import (
    WysnaType,
    IntType,
    FloatType,
    BoolType,
    StrType,
    UnitType,
    NoneType,
    OptionalType,
    ListType,
    DictType,
    TupleType,
    StructType,
    EnumType,
    InterfaceType,
    ClassType,
    ResultType,
    ErrorUnionType,
    AnyType,
    TypeParamType,
)
from checker.SymbolTable import Symbol


class GenericInstanceKey(NamedTuple):
    """Session-local specialization identity; not a persistent hash or ABI name."""
    template_name: str
    type_ids: Tuple[int, ...]
    module_id: Optional[str] = None
    decl_id: Optional[Any] = None


class InstantiationFrame(NamedTuple):
    """Arguments needed to prove structural termination and render its diagnostic."""
    template_name: str
    arguments: Tuple[WysnaType, ...]


class SpecializationState(Enum):
    PENDING = "pending"
    SPECIALIZING = "specializing"
    COMPLETE = "complete"
    FAILED = "failed"


class SpecializationEntry:
    def __init__(
        self,
        key: GenericInstanceKey,
        state: SpecializationState = SpecializationState.PENDING,
        specialized_type: Optional[WysnaType] = None,
        specialized_symbol: Optional[Symbol] = None,
        ast_decl: Optional[Any] = None
    ):
        self.key = key
        self.state = state
        self.specialized_type = specialized_type
        self.specialized_symbol = specialized_symbol
        self.ast_decl = ast_decl
        self.dependencies: Set[GenericInstanceKey] = set()


# Largest structural size a single generic specialization may reach. The depth
# limit below bounds recursion; this bounds growth, which a depth limit alone
# does not catch because every level is a distinct, cached specialization.
MAX_SPECIALIZATION_COMPLEXITY = 256

def _mangle_type_static(t: WysnaType) -> str:
    if isinstance(t, AnyType):
        return "any"
    if t == IntType:
        return "int"
    if t == FloatType:
        return "float"
    if t == BoolType:
        return "bool"
    if t == StrType:
        return "str"
    if t == UnitType:
        return "unit"
    if t == NoneType:
        return "none"
    if isinstance(t, (StructType, EnumType, ClassType, InterfaceType)):
        return f"{len(t.name)}_{t.name}"
    if isinstance(t, OptionalType):
        return f"opt__{_mangle_type_static(t.inner)}"
    if isinstance(t, ListType):
        return f"list__{_mangle_type_static(t.element)}"
    if isinstance(t, DictType):
        return f"dict__{_mangle_type_static(t.key)}__{_mangle_type_static(t.value)}"
    if isinstance(t, TupleType):
        elems = "__".join(_mangle_type_static(e) for e in t.elements)
        return f"tuple_{len(t.elements)}__{elems}"
    if isinstance(t, ResultType):
        return f"res__{_mangle_type_static(t.ok)}__{_mangle_type_static(t.err)}"
    if isinstance(t, ErrorUnionType):
        members = "__".join(_mangle_type_static(member) for member in t.alternatives)
        return f"errunion_{len(t.alternatives)}__{members}"
    if isinstance(t, TypeParamType):
        if t.owner is None:
            return t.name
        return f"typeparam__{len(t.owner)}_{t.owner}__{t.name}"
    safe_str = str(t).replace("[", "__").replace("]", "").replace(", ", "__").replace(" ", "_")
    return f"{len(safe_str)}_{safe_str}"


def mangle_generic_name(
    template_name: str,
    concrete_args: Sequence[WysnaType],
    module_name: Optional[str] = None
) -> str:
    args_part = "__".join(_mangle_type_static(a) for a in concrete_args)
    if template_name in ("Option", "Result"):
        return f"{template_name}__{args_part}"
    mod_prefix = f"{len(module_name)}_{module_name}__" if module_name else ""
    return f"_WYSNA_G_{mod_prefix}{len(template_name)}_{template_name}__{args_part}"


def type_strictly_contains(sub: WysnaType, container: WysnaType) -> bool:
    """Returns True if sub is a strict structural sub-component of container."""
    if sub == container:
        return False
    return _type_contains(sub, container)


def _type_contains(sub: WysnaType, container: WysnaType) -> bool:
    if sub == container:
        return True
    if isinstance(container, ListType):
        return _type_contains(sub, container.element)
    if isinstance(container, DictType):
        return _type_contains(sub, container.key) or _type_contains(sub, container.value)
    if isinstance(container, TupleType):
        return any(_type_contains(sub, e) for e in container.elements)
    if isinstance(container, OptionalType):
        return _type_contains(sub, container.inner)
    if isinstance(container, ResultType):
        return _type_contains(sub, container.ok) or _type_contains(sub, container.err)
    if isinstance(container, ErrorUnionType):
        return any(_type_contains(sub, member) for member in container.alternatives)
    if isinstance(container, (StructType, EnumType)) and getattr(container, "type_args", None):
        return any(_type_contains(sub, arg) for arg in container.type_args)
    return False


class MonomorphizationTelemetry:
    """Telemetry tracking generic specialization, cache hits, and code bloat."""
    def __init__(
        self,
        specialization_requests: int = 0,
        deduplicated_hits: int = 0,
        specialized_structs: int = 0,
        specialized_enums: int = 0,
        specialized_functions: int = 0
    ) -> None:
        self.specialization_requests = specialization_requests
        self.deduplicated_hits = deduplicated_hits
        self.specialized_structs = specialized_structs
        self.specialized_enums = specialized_enums
        self.specialized_functions = specialized_functions

    def to_dict(self) -> Dict[str, int]:
        return {
            "specialization_requests": self.specialization_requests,
            "deduplicated_hits": self.deduplicated_hits,
            "specialized_structs": self.specialized_structs,
            "specialized_enums": self.specialized_enums,
            "specialized_functions": self.specialized_functions,
        }

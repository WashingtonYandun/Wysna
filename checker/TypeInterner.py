from itertools import count
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from checker.WysnaType import (
    OpaqueType, WysnaType, PrimitiveType, IntType, FloatType, BoolType, StrType, UnitType, NoneType,
    OptionalType, WeakType, ListType, DictType, TupleType, StructType, EnumType,
    InterfaceType, ClassFieldInfo, ClassMethodInfo, ClassType,
    ResultType, ErrorUnionType, FunctionType, ModuleType, AnyType, TypeParamType
)

_ARENA_IDS = count(1)

# Canonical Reserved TypeId Constants (docs/TYPE_SYSTEM_DESIGN.md)
TYPE_ERROR: int = 0
TYPE_UNIT: int = 1
TYPE_BOOL: int = 2
TYPE_INT: int = 3
TYPE_FLOAT: int = 4
TYPE_STR: int = 5
TYPE_NONE: int = 6


class TypeCapabilities:
    """Precomputed capability and lifetime descriptor for a TypeId."""
    def __init__(
        self,
        needs_retain: bool = False,
        needs_release: bool = False,
        is_value: bool = True,
        is_reference: bool = False,
        is_copyable: bool = True,
        has_identity: bool = False,
        is_comparable: bool = True,
        is_valid_dict_key: bool = False,
    ):
        self.needs_retain = needs_retain
        self.needs_release = needs_release
        self.is_value = is_value
        self.is_reference = is_reference
        self.is_copyable = is_copyable
        self.has_identity = has_identity
        self.is_comparable = is_comparable
        self.is_valid_dict_key = is_valid_dict_key

    def to_dict(self) -> Dict[str, bool]:
        return {
            "needs_retain": self.needs_retain,
            "needs_release": self.needs_release,
            "is_value": self.is_value,
            "is_reference": self.is_reference,
            "is_copyable": self.is_copyable,
            "has_identity": self.has_identity,
            "is_comparable": self.is_comparable,
            "is_valid_dict_key": self.is_valid_dict_key,
        }

    def __repr__(self) -> str:
        return f"TypeCapabilities({self.to_dict()})"


class TypeEntry:
    """Immutable entry stored in the TypeInterner arena."""
    def __init__(self, tag: str, data: Any = None):
        self.tag = tag
        self.data = data

    def __repr__(self) -> str:
        return f"TypeEntry({self.tag}, {self.data})"


class StructDef:
    def __init__(self, def_id: int, name: str, fields: Dict[str, int]):
        self.def_id = def_id
        self.name = name
        self.fields = fields  # field_name -> TypeId


class EnumDef:
    def __init__(
        self,
        def_id: int,
        name: str,
        variants: List[str],
        variant_payloads: Optional[Dict[str, List[int]]] = None,
        type_args: Tuple[int, ...] = (),
        template_name: Optional[str] = None,
    ):
        self.def_id = def_id
        self.name = name
        self.variants = variants
        self.variant_payloads = variant_payloads if variant_payloads is not None else {}
        self.type_args = type_args
        self.template_name = template_name




class InterfaceDef:
    def __init__(self, def_id: int, name: str, methods: Dict[str, int]):
        self.def_id = def_id
        self.name = name
        self.methods = methods  # method_name -> Function TypeId


class ClassDef:
    def __init__(
        self,
        def_id: int,
        name: str,
        superclass: Optional[int] = None,
        interfaces: Optional[List[int]] = None,
        fields: Optional[Dict[str, Tuple[int, bool]]] = None,  # name -> (TypeId, is_public)
        methods: Optional[Dict[str, Tuple[int, bool]]] = None,  # name -> (Function TypeId, is_public)
        init_sig: Optional[int] = None
    ):
        self.def_id = def_id
        self.name = name
        self.superclass = superclass  # Class TypeId
        self.interfaces = interfaces if interfaces is not None else []  # Interface TypeIds
        self.fields = fields if fields is not None else {}
        self.methods = methods if methods is not None else {}
        self.init_sig = init_sig


class TypeInterner:
    """
    Central Type Interner for Wysna.
    Guarantees:
    - O(1) comparison between canonical TypeIds (id1 == id2).
    - Hash-consing of all structural and nominal type representations.
    - Pre-computed TypeCapabilities for ARC lifetime and CoW lowering.
    - Bidirectional mapping with legacy WysnaType hierarchy.
    """
    def __init__(self) -> None:
        self.arena_id = next(_ARENA_IDS)
        self._entries: List[TypeEntry] = []
        self._capabilities: List[TypeCapabilities] = []
        self._key_to_id: Dict[Tuple[Any, ...], int] = {}
        self._type_to_id: Dict[Any, int] = {}
        self._id_to_type: Dict[int, WysnaType] = {}

        self.struct_defs: Dict[str, StructDef] = {}
        self.enum_defs: Dict[str, EnumDef] = {}
        self.interface_defs: Dict[str, InterfaceDef] = {}
        self.class_defs: Dict[str, ClassDef] = {}

        self._init_primitives()

    def _init_primitives(self) -> None:
        # 0: TYPE_ERROR
        self._register_fixed(
            TYPE_ERROR,
            ("error",),
            TypeEntry("error"),
            TypeCapabilities(is_value=True, is_comparable=True, is_valid_dict_key=False),
            AnyType()
        )
        # 1: TYPE_UNIT
        self._register_fixed(
            TYPE_UNIT,
            ("primitive", "unit"),
            TypeEntry("primitive", "unit"),
            TypeCapabilities(is_value=True, is_comparable=True, is_valid_dict_key=False),
            UnitType
        )
        # 2: TYPE_BOOL
        self._register_fixed(
            TYPE_BOOL,
            ("primitive", "bool"),
            TypeEntry("primitive", "bool"),
            TypeCapabilities(is_value=True, is_comparable=True, is_valid_dict_key=True),
            BoolType
        )
        # 3: TYPE_INT
        self._register_fixed(
            TYPE_INT,
            ("primitive", "int"),
            TypeEntry("primitive", "int"),
            TypeCapabilities(is_value=True, is_comparable=True, is_valid_dict_key=True),
            IntType
        )
        # 4: TYPE_FLOAT
        self._register_fixed(
            TYPE_FLOAT,
            ("primitive", "float"),
            TypeEntry("primitive", "float"),
            TypeCapabilities(is_value=True, is_comparable=True, is_valid_dict_key=False),
            FloatType
        )
        # 5: TYPE_STR
        self._register_fixed(
            TYPE_STR,
            ("primitive", "str"),
            TypeEntry("primitive", "str"),
            TypeCapabilities(needs_retain=True, needs_release=True, is_value=True, is_comparable=True, is_valid_dict_key=True),
            StrType
        )
        # 6: TYPE_NONE
        self._register_fixed(
            TYPE_NONE,
            ("primitive", "none"),
            TypeEntry("primitive", "none"),
            TypeCapabilities(is_value=True, is_comparable=True, is_valid_dict_key=False),
            NoneType
        )

    def _register_fixed(
        self,
        expected_id: int,
        key: Tuple[Any, ...],
        entry: TypeEntry,
        caps: TypeCapabilities,
        type_: WysnaType
    ) -> None:
        assigned_id = len(self._entries)
        assert assigned_id == expected_id, f"Primitive ID mismatch: expected {expected_id}, got {assigned_id}"
        self._entries.append(entry)
        self._capabilities.append(caps)
        self._key_to_id[key] = assigned_id
        self._id_to_type[assigned_id] = type_

    def _intern(self, key: Tuple[Any, ...], entry: TypeEntry, caps: TypeCapabilities) -> int:
        if key in self._key_to_id:
            return self._key_to_id[key]
        type_id = len(self._entries)
        self._entries.append(entry)
        self._capabilities.append(caps)
        self._key_to_id[key] = type_id
        return type_id

    # -------------------------------------------------------------
    # Constructors for Composite Types
    # -------------------------------------------------------------

    def mk_weak(self, target_id: int) -> int:
        if self.get_entry(target_id).tag != "class":
            return TYPE_ERROR
        tid = self._intern(("weak", target_id), TypeEntry("weak", target_id),
                           TypeCapabilities(needs_retain=True, needs_release=True,
                                            is_value=True, has_identity=True,
                                            is_comparable=True, is_valid_dict_key=False))
        if tid not in self._id_to_type:
            self._id_to_type[tid] = WeakType(self.to_type(target_id))
        return tid

    def mk_optional(self, inner_id: int) -> int:
        if inner_id == TYPE_ERROR:
            return TYPE_ERROR
        key = ("optional", inner_id)
        caps = TypeCapabilities(
            needs_retain=True,
            needs_release=True,
            is_value=self._capabilities[inner_id].is_value,
            is_reference=self._capabilities[inner_id].is_reference,
            has_identity=self._capabilities[inner_id].has_identity,
            is_comparable=True,
            is_valid_dict_key=False,
        )
        tid = self._intern(key, TypeEntry("optional", inner_id), caps)
        if tid not in self._id_to_type:
            self._id_to_type[tid] = OptionalType(self.to_type(inner_id))
        return tid

    def mk_result(self, ok_id: int, err_id: int) -> int:
        if ok_id == TYPE_ERROR or err_id == TYPE_ERROR:
            return TYPE_ERROR
        key = ("result", ok_id, err_id)
        caps = TypeCapabilities(
            needs_retain=True,
            needs_release=True,
            is_value=True,
            is_reference=False,
            is_comparable=False,
            is_valid_dict_key=False,
        )
        tid = self._intern(key, TypeEntry("result", (ok_id, err_id)), caps)
        if tid not in self._id_to_type:
            self._id_to_type[tid] = ResultType(self.to_type(ok_id), self.to_type(err_id))
        return tid

    def mk_error_union(self, alternatives: Tuple[int, ...]) -> int:
        if not alternatives or any(type_id == TYPE_ERROR for type_id in alternatives):
            return TYPE_ERROR
        canonical = tuple(sorted(set(alternatives)))
        if len(canonical) < 2:
            return canonical[0] if canonical else TYPE_ERROR
        key = ("error_union", canonical)
        # A union carries one of its member values directly. Conservatively mark
        # it as owning so cleanup remains correct when any alternative contains
        # ARC-managed storage (for example a struct with strings or collections).
        caps = TypeCapabilities(needs_retain=True, needs_release=True, is_value=True,
                                is_reference=False, is_copyable=True,
                                is_comparable=False, is_valid_dict_key=False)
        type_id = self._intern(key, TypeEntry("error_union", canonical), caps)
        if type_id not in self._id_to_type:
            self._id_to_type[type_id] = ErrorUnionType([self.to_type(item) for item in canonical])
        return type_id

    def mk_list(self, elem_id: int) -> int:
        if elem_id == TYPE_ERROR:
            return TYPE_ERROR
        key = ("list", elem_id)
        caps = TypeCapabilities(
            needs_retain=True,
            needs_release=True,
            is_value=True,
            is_reference=False,
            is_comparable=True,
            is_valid_dict_key=False,
        )
        tid = self._intern(key, TypeEntry("list", elem_id), caps)
        if tid not in self._id_to_type:
            self._id_to_type[tid] = ListType(self.to_type(elem_id))
        return tid

    def mk_dict(self, key_id: int, val_id: int) -> int:
        if key_id == TYPE_ERROR or val_id == TYPE_ERROR:
            return TYPE_ERROR
        key = ("dict", key_id, val_id)
        caps = TypeCapabilities(
            needs_retain=True,
            needs_release=True,
            is_value=True,
            is_reference=False,
            is_comparable=True,
            is_valid_dict_key=False,
        )
        tid = self._intern(key, TypeEntry("dict", (key_id, val_id)), caps)
        if tid not in self._id_to_type:
            self._id_to_type[tid] = DictType(self.to_type(key_id), self.to_type(val_id))
        return tid

    def mk_tuple(self, elem_ids: Tuple[int, ...]) -> int:
        if any(eid == TYPE_ERROR for eid in elem_ids):
            return TYPE_ERROR
        key = ("tuple", elem_ids)
        needs_rc = any(self._capabilities[eid].needs_release for eid in elem_ids)
        caps = TypeCapabilities(
            needs_retain=needs_rc,
            needs_release=needs_rc,
            is_value=True,
            is_reference=False,
            is_comparable=True,
            is_valid_dict_key=False,
        )
        tid = self._intern(key, TypeEntry("tuple", elem_ids), caps)
        if tid not in self._id_to_type:
            self._id_to_type[tid] = TupleType([self.to_type(eid) for eid in elem_ids])
        return tid

    def mk_function(self, params: Tuple[int, ...], ret: int, is_mutating: bool = False) -> int:
        key = ("function", params, ret, is_mutating)
        caps = TypeCapabilities(
            needs_retain=True,
            needs_release=True,
            is_value=True,
            is_reference=False,
            is_comparable=False,
            is_valid_dict_key=False,
        )
        tid = self._intern(key, TypeEntry("function", (params, ret, is_mutating)), caps)
        if tid not in self._id_to_type:
            self._id_to_type[tid] = FunctionType([self.to_type(p) for p in params], self.to_type(ret), is_mutating)
        return tid

    # -------------------------------------------------------------
    # Constructors for Nominal User-Defined Types
    # -------------------------------------------------------------

    def mk_struct(self, name: str, fields: Optional[Dict[str, int]] = None) -> int:
        key = ("struct", name)
        if key in self._key_to_id:
            tid = self._key_to_id[key]
            if fields is not None and name in self.struct_defs:
                self.struct_defs[name].fields = fields
            return tid
        caps = TypeCapabilities(
            needs_retain=True,
            needs_release=True,
            is_value=True,
            is_reference=False,
            is_comparable=True,
            is_valid_dict_key=False,
        )
        tid = self._intern(key, TypeEntry("struct", name), caps)
        f_dict = fields if fields is not None else {}
        self.struct_defs[name] = StructDef(tid, name, f_dict)
        return tid

    def mk_enum(
        self,
        name: str,
        variants: Optional[List[str]] = None,
        variant_payloads: Optional[Dict[str, List[int]]] = None,
        type_args: Tuple[int, ...] = (),
        template_name: Optional[str] = None,
    ) -> int:
        key = ("enum", name)
        if key in self._key_to_id:
            tid = self._key_to_id[key]
            if name in self.enum_defs:
                if variants is not None:
                    self.enum_defs[name].variants = variants
                if variant_payloads is not None:
                    self.enum_defs[name].variant_payloads = variant_payloads
                if type_args:
                    self.enum_defs[name].type_args = type_args
                if template_name:
                    self.enum_defs[name].template_name = template_name
                cached = self._id_to_type.get(tid)
                if isinstance(cached, EnumType):
                    definition = self.enum_defs[name]
                    cached.variants = list(definition.variants)
                    cached.variant_payloads = {variant: [self.to_type(item) for item in payload]
                                               for variant, payload in definition.variant_payloads.items()}
                    cached.template_name = definition.template_name
                    cached.type_args = [self.to_type(item) for item in definition.type_args] or None
            return tid
        caps = TypeCapabilities(
            needs_retain=False,
            needs_release=False,
            is_value=True,
            is_reference=False,
            is_comparable=True,
            is_valid_dict_key=True,
        )
        tid = self._intern(key, TypeEntry("enum", name), caps)
        v_list = variants if variants is not None else []
        v_payloads = variant_payloads if variant_payloads is not None else {}
        self.enum_defs[name] = EnumDef(tid, name, v_list, v_payloads, type_args=type_args, template_name=template_name)
        return tid


    def mk_interface(self, name: str, methods: Optional[Dict[str, int]] = None) -> int:
        key = ("interface", name)
        if key in self._key_to_id:
            tid = self._key_to_id[key]
            if methods is not None and name in self.interface_defs:
                self.interface_defs[name].methods = methods
            return tid
        caps = TypeCapabilities(
            needs_retain=True,
            needs_release=True,
            is_value=False,
            is_reference=True,
            has_identity=True,
            is_comparable=True,
            is_valid_dict_key=False,
        )
        tid = self._intern(key, TypeEntry("interface", name), caps)
        m_dict = methods if methods is not None else {}
        self.interface_defs[name] = InterfaceDef(tid, name, m_dict)
        return tid

    def mk_class(
        self,
        name: str,
        superclass: Optional[int] = None,
        interfaces: Optional[List[int]] = None,
        fields: Optional[Dict[str, Tuple[int, bool]]] = None,
        methods: Optional[Dict[str, Tuple[int, bool]]] = None,
        init_sig: Optional[int] = None
    ) -> int:
        key = ("class", name)
        if key in self._key_to_id:
            tid = self._key_to_id[key]
            cdef = self.class_defs.get(name)
            if cdef is not None:
                if superclass is not None:
                    cdef.superclass = superclass
                if interfaces is not None:
                    cdef.interfaces = interfaces
                if fields is not None:
                    cdef.fields = fields
                if methods is not None:
                    cdef.methods = methods
                if init_sig is not None:
                    cdef.init_sig = init_sig
            return tid
        caps = TypeCapabilities(
            needs_retain=True,
            needs_release=True,
            is_value=False,
            is_reference=True,
            has_identity=True,
            is_comparable=True,
            is_valid_dict_key=False,
        )
        tid = self._intern(key, TypeEntry("class", name), caps)
        self.class_defs[name] = ClassDef(
            tid,
            name,
            superclass=superclass,
            interfaces=interfaces,
            fields=fields,
            methods=methods,
            init_sig=init_sig
        )
        return tid

    def mk_module(self, name: str) -> int:
        key = ("module", name)
        caps = TypeCapabilities(
            needs_retain=False,
            needs_release=False,
            is_value=False,
            is_reference=False,
            is_comparable=False,
            is_valid_dict_key=False,
        )
        return self._intern(key, TypeEntry("module", name), caps)

    def mk_any(self) -> int:
        key = ("any",)
        caps = TypeCapabilities(
            needs_retain=True,
            needs_release=True,
            is_value=True,
            is_reference=True,
            is_comparable=True,
            is_valid_dict_key=False,
        )
        return self._intern(key, TypeEntry("any"), caps)

    def mk_type_param(self, name: str, owner: Optional[str] = None) -> int:
        key = ("type_param", owner, name)
        caps = TypeCapabilities(is_value=False, is_reference=False, is_copyable=False,
                                is_comparable=False, is_valid_dict_key=False)
        type_id = self._intern(key, TypeEntry("type_param", (owner, name)), caps)
        self._id_to_type.setdefault(type_id, TypeParamType(name, owner=owner))
        return type_id

    # -------------------------------------------------------------
    # Queries & Inspections
    # -------------------------------------------------------------

    def get_entry(self, type_id: int) -> TypeEntry:
        if 0 <= type_id < len(self._entries):
            return self._entries[type_id]
        return TypeEntry("error")

    def get_capabilities(self, type_id: int) -> TypeCapabilities:
        if 0 <= type_id < len(self._capabilities):
            return self._capabilities[type_id]
        return TypeCapabilities()

    def is_class_subclass_of(self, sub_id: int, sup_name: str) -> bool:
        sub_entry = self.get_entry(sub_id)
        if sub_entry.tag != "class":
            return False
        curr_name = sub_entry.data
        visited = set()
        while curr_name:
            if curr_name == sup_name:
                return True
            if curr_name in visited:
                break
            visited.add(curr_name)
            cdef = self.class_defs.get(curr_name)
            if not cdef or cdef.superclass is None:
                break
            sup_entry = self.get_entry(cdef.superclass)
            curr_name = sup_entry.data if sup_entry.tag == "class" else None
        return False

    def class_implements_interface(self, class_id: int, intf_name: str) -> bool:
        c_entry = self.get_entry(class_id)
        if c_entry.tag != "class":
            return False
        curr_name = c_entry.data
        visited = set()
        while curr_name:
            if curr_name in visited:
                break
            visited.add(curr_name)
            cdef = self.class_defs.get(curr_name)
            if not cdef:
                break
            for iid in cdef.interfaces:
                ientry = self.get_entry(iid)
                if ientry.tag == "interface" and ientry.data == intf_name:
                    return True
            if cdef.superclass is not None:
                sup_entry = self.get_entry(cdef.superclass)
                curr_name = sup_entry.data if sup_entry.tag == "class" else None
            else:
                break
        return False

    def is_assignable(self, src_id: int, dst_id: int) -> bool:
        """
        Fast canonical assignability check between TypeIds: src <: dst.
        Rules:
        - O(1) identity check.
        - `any` is assignable *to* anything (builtin wildcard params, inference
          placeholders) but nothing is assignable *to* `any`; see
          checker/WysnaType.py:is_assignable for the rationale.
        - Optional T? accepts T, none, and S? if S <: T.
        - Result T ! E accepts T.
        - Invariant list/dict subtyping (list[S] -> list[T] iff S == T).
        - OOP subtyping: Class -> Superclass, Class -> Interface.
        """
        if src_id == dst_id:
            return True

        if src_id == TYPE_ERROR or dst_id == TYPE_ERROR:
            return True

        src_entry = self.get_entry(src_id)
        dst_entry = self.get_entry(dst_id)

        if src_entry.tag == "any":
            return True
        if dst_entry.tag == "any":
            return False
        if src_entry.tag == "type_param" or dst_entry.tag == "type_param":
            return False

        if src_entry.tag == "error_union" or dst_entry.tag == "error_union":
            src_members = src_entry.data if src_entry.tag == "error_union" else (src_id,)
            dst_members = dst_entry.data if dst_entry.tag == "error_union" else (dst_id,)
            return all(
                any(src_member == dst_member for dst_member in dst_members)
                for src_member in src_members
            )

        # OOP Subtyping: Class -> Superclass
        if src_entry.tag == "class" and dst_entry.tag == "class":
            return self.is_class_subclass_of(src_id, dst_entry.data)

        # OOP Subtyping: Class -> Interface
        if src_entry.tag == "class" and dst_entry.tag == "interface":
            return self.class_implements_interface(src_id, dst_entry.data)

        # Invariant List subtyping: list[S] -> list[T] iff S == T (SPEC 4.1 & ADR-002)
        if src_entry.tag == "list" and dst_entry.tag == "list":
            return src_entry.data == dst_entry.data

        # Invariant Dict subtyping: dict[K1, V1] -> dict[K2, V2] iff K1 == K2 and V1 == V2
        if src_entry.tag == "dict" and dst_entry.tag == "dict":
            return src_entry.data == dst_entry.data

        # Optional target: T? accepts T, none, and S? if S <: T
        if dst_entry.tag == "optional":
            if src_id == TYPE_NONE:
                return True
            inner_dst = dst_entry.data
            if src_entry.tag == "optional":
                if (self.get_entry(src_entry.data).tag == "optional") != (self.get_entry(inner_dst).tag == "optional"):
                    return False
                return self.is_assignable(src_entry.data, inner_dst)
            return self.is_assignable(src_id, inner_dst)

        # Result target: (T ! E) accepts T
        if dst_entry.tag == "result":
            ok_id, err_id = dst_entry.data
            if src_entry.tag == "result":
                if src_entry.data[0] == ok_id and self.is_error_channel_assignable(src_entry.data[1], err_id):
                    return True
            return self.is_assignable(src_id, ok_id)

        return False

    def equality_compatible(self, left_id: int, right_id: int) -> bool:
        """Compare canonical shapes, including unconstrained constructor channels."""
        pending, seen = [left_id, right_id], set()
        while pending:
            type_id = pending.pop()
            if type_id in seen:
                continue
            seen.add(type_id)
            entry = self.get_entry(type_id)
            if entry.tag == 'function':
                return False
            if entry.tag in ('list', 'optional'):
                pending.append(entry.data)
            elif entry.tag in ('tuple', 'result', 'error_union', 'dict'):
                pending.extend(entry.data)
            elif entry.tag == 'struct':
                pending.extend(self.struct_defs[entry.data].fields.values())
            elif entry.tag == 'enum':
                pending.extend(item for payload in self.enum_defs[entry.data].variant_payloads.values() for item in payload)
        return self._equality_shapes(left_id, right_id)

    def _equality_shapes(self, left_id: int, right_id: int) -> bool:
        if self.is_assignable(left_id, right_id) or self.is_assignable(right_id, left_id):
            return True
        left, right = self.get_entry(left_id), self.get_entry(right_id)
        if left.tag != right.tag:
            return False
        if left.tag in ('list', 'optional'):
            return self._equality_shapes(left.data, right.data)
        if left.tag in ('tuple', 'result', 'error_union'):
            return len(left.data) == len(right.data) and all(
                self._equality_shapes(a, b) for a, b in zip(left.data, right.data))
        if left.tag == 'enum':
            a, b = self.enum_defs[left.data], self.enum_defs[right.data]
            return bool(a.template_name) and a.template_name == b.template_name and len(a.type_args) == len(b.type_args) and all(
                self._equality_shapes(x, y) for x, y in zip(a.type_args, b.type_args))
        return False

    def is_error_channel_assignable(self, src_id: int, dst_id: int) -> bool:
        """Return whether every source error alternative exists in the target set.

        Error channels are type positions: normal Result success-value injection
        must not turn a scalar error into a nested Result error type.
        """
        if src_id == dst_id or src_id == TYPE_ERROR or dst_id == TYPE_ERROR:
            return True

        src_entry = self.get_entry(src_id)
        dst_entry = self.get_entry(dst_id)
        src_members = src_entry.data if src_entry.tag == "error_union" else (src_id,)
        dst_members = dst_entry.data if dst_entry.tag == "error_union" else (dst_id,)
        return all(any(src_member == dst_member for dst_member in dst_members)
                   for src_member in src_members)

    def to_string(self, type_id: int) -> str:
        """User-friendly canonical string representation of a TypeId."""
        entry = self.get_entry(type_id)
        if entry.tag == "error":
            return "<error>"
        elif entry.tag == "primitive":
            return str(entry.data)
        elif entry.tag == "optional":
            return f"{self.to_string(entry.data)}?"
        elif entry.tag == "result":
            ok_id, err_id = entry.data
            return f"{self.to_string(ok_id)} ! {self.to_string(err_id)}"
        elif entry.tag == "error_union":
            return " | ".join(self.to_string(member) for member in entry.data)
        elif entry.tag == "weak":
            return f"Weak[{self.to_string(entry.data)}]"
        elif entry.tag == "list":
            return f"list[{self.to_string(entry.data)}]"
        elif entry.tag == "dict":
            k_id, v_id = entry.data
            return f"dict[{self.to_string(k_id)}, {self.to_string(v_id)}]"
        elif entry.tag == "tuple":
            elem_strs = [self.to_string(eid) for eid in entry.data]
            return f"tuple[{', '.join(elem_strs)}]"
        elif entry.tag == "function":
            param_ids, ret_id, _ = entry.data
            p_strs = [self.to_string(pid) for pid in param_ids]
            return f"fn({', '.join(p_strs)}) -> {self.to_string(ret_id)}"
        elif entry.tag in ("struct", "enum", "interface", "class"):
            return str(entry.data)
        elif entry.tag == "type_param":
            return str(entry.data[1])
        elif entry.tag == "module":
            return f"module {entry.data}"
        elif entry.tag == "any":
            return "any"
        return f"<unknown:{type_id}>"

    # -------------------------------------------------------------
    # Bidirectional Mapping: WysnaType <-> TypeId
    # -------------------------------------------------------------

    def intern(self, type_: WysnaType) -> int:
        """Alias for from_type(type_) to intern a WysnaType to TypeId."""
        return self.from_type(type_)

    def from_type(self, type_: WysnaType) -> int:
        """Converts an existing WysnaType instance to its canonical TypeId."""
        if type_ is None:
            return TYPE_NONE
        if isinstance(type_, PrimitiveType):
            if type_.name == "int":
                return TYPE_INT
            elif type_.name == "float":
                return TYPE_FLOAT
            elif type_.name == "bool":
                return TYPE_BOOL
            elif type_.name == "str":
                return TYPE_STR
            elif type_.name == "unit":
                return TYPE_UNIT
            elif type_.name == "none":
                return TYPE_NONE
            if type_.name == "opaque":
                type_id = self._intern(("primitive", "opaque"), TypeEntry("primitive", "opaque"), TypeCapabilities())
                self._id_to_type[type_id] = OpaqueType
                return type_id
            return TYPE_ERROR
        elif isinstance(type_, WeakType):
            return self.mk_weak(self.from_type(type_.target))
        elif isinstance(type_, OptionalType):
            return self.mk_optional(self.from_type(type_.inner))
        elif isinstance(type_, ResultType):
            return self.mk_result(self.from_type(type_.ok), self.from_type(type_.err))
        elif isinstance(type_, ErrorUnionType):
            return self.mk_error_union(tuple(self.from_type(item) for item in type_.alternatives))
        elif isinstance(type_, ListType):
            return self.mk_list(self.from_type(type_.element))
        elif isinstance(type_, DictType):
            return self.mk_dict(self.from_type(type_.key), self.from_type(type_.value))
        elif isinstance(type_, TupleType):
            return self.mk_tuple(tuple(self.from_type(e) for e in type_.elements))
        elif isinstance(type_, FunctionType):
            return self.mk_function(
                tuple(self.from_type(p) for p in type_.params),
                self.from_type(type_.ret),
                type_.is_mutating
            )
        elif isinstance(type_, StructType):
            key = ("struct", type_.name)
            if key in self._key_to_id:
                return self._key_to_id[key]
            tid = self.mk_struct(type_.name)
            fields = {fname: self.from_type(ftype) for fname, ftype in type_.fields.items()} if hasattr(type_, "fields") and type_.fields else {}
            return self.mk_struct(type_.name, fields)
        elif isinstance(type_, EnumType):
            args = getattr(type_, "type_args", None)
            template = getattr(type_, "template_name", None)
            if template == "Option" and args and len(args) == 1:
                tid = self.mk_optional(self.from_type(args[0]))
                self._key_to_id[("enum", type_.name)] = tid
                return tid
            if template == "Result" and args and len(args) == 2:
                tid = self.mk_result(self.from_type(args[0]), self.from_type(args[1]))
                self._key_to_id[("enum", type_.name)] = tid
                return tid
            key = ("enum", type_.name)
            if key in self._key_to_id:
                return self._key_to_id[key]
            variants = type_.variants if hasattr(type_, "variants") else []
            type_arg_ids = tuple(self.from_type(arg) for arg in args) if args else ()
            tid = self.mk_enum(type_.name, variants, type_args=type_arg_ids, template_name=template)
            payloads: Dict[str, List[int]] = {}
            if hasattr(type_, "variant_payloads") and type_.variant_payloads:
                for vname, vtypes in type_.variant_payloads.items():
                    payloads[vname] = [self.from_type(vt) for vt in vtypes]
            if type_.name in self.enum_defs:
                self.enum_defs[type_.name].variant_payloads = payloads
            return tid

        elif isinstance(type_, InterfaceType):
            key = ("interface", type_.name)
            if key in self._key_to_id:
                return self._key_to_id[key]
            tid = self.mk_interface(type_.name)
            methods = {mname: self.from_type(mfn) for mname, mfn in type_.methods.items()} if hasattr(type_, "methods") and type_.methods else {}
            return self.mk_interface(type_.name, methods)
        elif isinstance(type_, ClassType):
            key = ("class", type_.name)
            if key in self._key_to_id:
                return self._key_to_id[key]
            tid = self.mk_class(type_.name)
            sup_id = self.from_type(type_.superclass) if type_.superclass else None
            intf_ids = [self.from_type(i) for i in type_.interfaces] if type_.interfaces else []
            fields = {fname: (self.from_type(finfo.type), finfo.is_public) for fname, finfo in type_.fields.items()} if type_.fields else {}
            methods = {mname: (self.from_type(minfo.fn_type), minfo.is_public) for mname, minfo in type_.methods.items()} if type_.methods else {}
            init_sig = self.from_type(type_.init_type) if type_.init_type else None
            return self.mk_class(type_.name, superclass=sup_id, interfaces=intf_ids, fields=fields, methods=methods, init_sig=init_sig)
        elif isinstance(type_, ModuleType):
            return self.mk_module(type_.name)
        elif isinstance(type_, TypeParamType):
            owner = str(type_.owner) if type_.owner is not None else None
            type_id = self.mk_type_param(type_.name, owner)
            self._id_to_type[type_id] = type_
            return type_id
        elif isinstance(type_, AnyType):
            return self.mk_any()
        return TYPE_ERROR

    def to_type(self, type_id: int) -> WysnaType:
        """Converts a TypeId back to a WysnaType object."""
        if type_id in self._id_to_type:
            return self._id_to_type[type_id]

        entry = self.get_entry(type_id)
        res: WysnaType = AnyType()

        if entry.tag == "error":
            res = AnyType()
        elif entry.tag == "type_param":
            owner, name = entry.data
            res = TypeParamType(name, owner=owner)
        elif entry.tag == "primitive":
            pname = entry.data
            if pname == "int":
                res = IntType
            elif pname == "float":
                res = FloatType
            elif pname == "bool":
                res = BoolType
            elif pname == "str":
                res = StrType
            elif pname == "unit":
                res = UnitType
            elif pname == "none":
                res = OpaqueType if entry.data == "opaque" else NoneType
        elif entry.tag == "weak":
            res = WeakType(self.to_type(entry.data))
        elif entry.tag == "optional":
            res = OptionalType(self.to_type(entry.data))
        elif entry.tag == "result":
            ok_id, err_id = entry.data
            res = ResultType(self.to_type(ok_id), self.to_type(err_id))
        elif entry.tag == "error_union":
            res = ErrorUnionType([self.to_type(member) for member in entry.data])
        elif entry.tag == "list":
            res = ListType(self.to_type(entry.data))
        elif entry.tag == "dict":
            k_id, v_id = entry.data
            res = DictType(self.to_type(k_id), self.to_type(v_id))
        elif entry.tag == "tuple":
            res = TupleType([self.to_type(eid) for eid in entry.data])
        elif entry.tag == "function":
            params, ret, is_mut = entry.data
            res = FunctionType([self.to_type(p) for p in params], self.to_type(ret), is_mut)
        elif entry.tag == "struct":
            sdef = self.struct_defs.get(entry.data)
            res = StructType(entry.data, {})
            self._id_to_type[type_id] = res
            res.fields = {fname: self.to_type(fid) for fname, fid in sdef.fields.items()} if sdef else {}
        elif entry.tag == "enum":
            edef = self.enum_defs.get(entry.data)
            v_list = edef.variants if edef else []
            res = EnumType(entry.data, v_list)
            self._id_to_type[type_id] = res
            if edef:
                res.variant_payloads = {variant: [self.to_type(item) for item in payload]
                                        for variant, payload in edef.variant_payloads.items()}
                res.template_name = edef.template_name
                res.type_args = [self.to_type(item) for item in edef.type_args] or None
        elif entry.tag == "interface":
            idef = self.interface_defs.get(entry.data)
            m_types = {mname: self.to_type(mid) for mname, mid in idef.methods.items()} if idef else {}
            res = InterfaceType(entry.data, m_types)
        elif entry.tag == "class":
            cdef = self.class_defs.get(entry.data)
            if cdef:
                sup_type = self.to_type(cdef.superclass) if cdef.superclass is not None else None
                intf_types = [self.to_type(iid) for iid in cdef.interfaces]
                f_infos = {fname: ClassFieldInfo(self.to_type(fid), is_pub, entry.data) for fname, (fid, is_pub) in cdef.fields.items()}
                m_infos = {mname: ClassMethodInfo(self.to_type(mid), is_pub, entry.data) for mname, (mid, is_pub) in cdef.methods.items()}
                init_type = self.to_type(cdef.init_sig) if cdef.init_sig is not None else None
                res = ClassType(entry.data, superclass=sup_type, interfaces=intf_types, fields=f_infos, methods=m_infos, init_type=init_type)
            else:
                res = ClassType(entry.data)
        elif entry.tag == "module":
            res = ModuleType(entry.data, None)
        elif entry.tag == "any":
            res = AnyType()

        self._id_to_type[type_id] = res
        return res

    def to_snapshot(self) -> Dict[str, Any]:
        """Return a JSON-safe, versioned snapshot of this type arena."""
        def encode(value):
            if isinstance(value, tuple):
                return [encode(item) for item in value]
            if isinstance(value, list):
                return [encode(item) for item in value]
            if isinstance(value, dict):
                return {str(key): encode(item) for key, item in value.items()}
            return value

        return {
            "version": 1,
            "entries": [{"tag": entry.tag, "data": encode(entry.data)} for entry in self._entries],
            "capabilities": [caps.to_dict() for caps in self._capabilities],
            "structs": {
                name: {"def_id": definition.def_id, "fields": dict(definition.fields)}
                for name, definition in self.struct_defs.items()
            },
            "enums": {
                name: {
                    "def_id": definition.def_id,
                    "variants": list(definition.variants),
                    "variant_payloads": encode(definition.variant_payloads),
                    "type_args": list(definition.type_args),
                    "template_name": definition.template_name,
                }
                for name, definition in self.enum_defs.items()
            },
            "interfaces": {
                name: {"def_id": definition.def_id, "methods": dict(definition.methods)}
                for name, definition in self.interface_defs.items()
            },
            "classes": {
                name: {
                    "def_id": definition.def_id,
                    "superclass": definition.superclass,
                    "interfaces": list(definition.interfaces),
                    "fields": {field: list(value) for field, value in definition.fields.items()},
                    "methods": {method: list(value) for method, value in definition.methods.items()},
                    "init_sig": definition.init_sig,
                }
                for name, definition in self.class_defs.items()
            },
        }

    @classmethod
    def from_snapshot(cls, snapshot: Dict[str, Any]) -> "TypeInterner":
        """Restore a validated type arena snapshot without trusting external IDs."""
        if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
            raise ValueError("Unsupported type arena snapshot version")
        raw_entries = snapshot.get("entries")
        raw_capabilities = snapshot.get("capabilities")
        if not isinstance(raw_entries, list) or len(raw_entries) < 7:
            raise ValueError("Invalid type arena entry table")
        if not isinstance(raw_capabilities, list) or len(raw_capabilities) != len(raw_entries):
            raise ValueError("Invalid type arena capability table")

        def require_id(value):
            if type(value) is not int or not 0 <= value < len(raw_entries):
                raise ValueError(f"Invalid TypeId reference: {value!r}")
            return value

        normalized = []
        allowed_tags = {
            "error", "primitive", "optional", "weak", "result", "error_union", "list",
            "dict", "tuple", "function", "struct", "enum", "interface", "class", "module",
            "any", "type_param",
        }
        for type_id, raw in enumerate(raw_entries):
            if not isinstance(raw, dict) or raw.get("tag") not in allowed_tags:
                raise ValueError(f"Invalid type arena entry at TypeId {type_id}")
            tag, data = raw["tag"], raw.get("data")
            if tag in ("error", "any"):
                if data is not None:
                    raise ValueError(f"Unexpected data for {tag} TypeId {type_id}")
            elif tag in ("primitive", "struct", "enum", "interface", "class", "module"):
                if not isinstance(data, str):
                    raise ValueError(f"Invalid {tag} data at TypeId {type_id}")
            elif tag in ("optional", "weak", "list"):
                data = require_id(data)
            elif tag in ("result", "dict"):
                if not isinstance(data, list) or len(data) != 2:
                    raise ValueError(f"Invalid {tag} data at TypeId {type_id}")
                data = tuple(require_id(item) for item in data)
            elif tag in ("tuple", "error_union"):
                if not isinstance(data, list):
                    raise ValueError(f"Invalid {tag} data at TypeId {type_id}")
                data = tuple(require_id(item) for item in data)
                if tag == "error_union" and (len(data) < 2 or tuple(sorted(set(data))) != data):
                    raise ValueError(f"Noncanonical error union at TypeId {type_id}")
            elif tag == "function":
                if (not isinstance(data, list) or len(data) != 3 or not isinstance(data[0], list)
                        or type(data[2]) is not bool):
                    raise ValueError(f"Invalid function data at TypeId {type_id}")
                data = (tuple(require_id(item) for item in data[0]), require_id(data[1]), data[2])
            elif tag == "type_param":
                if (not isinstance(data, list) or len(data) != 2 or
                        (data[0] is not None and not isinstance(data[0], str)) or
                        not isinstance(data[1], str)):
                    raise ValueError(f"Invalid type parameter at TypeId {type_id}")
                data = tuple(data)
            normalized.append(TypeEntry(tag, data))

        capabilities = []
        cap_names = {
            "needs_retain", "needs_release", "is_value", "is_reference", "is_copyable",
            "has_identity", "is_comparable", "is_valid_dict_key",
        }
        for type_id, raw in enumerate(raw_capabilities):
            if (not isinstance(raw, dict) or set(raw) != cap_names or
                    any(type(value) is not bool for value in raw.values())):
                raise ValueError(f"Invalid capabilities for TypeId {type_id}")
            capabilities.append(TypeCapabilities(**raw))

        interner = cls()
        fixed = interner._entries
        for type_id in range(7):
            if fixed[type_id].tag != normalized[type_id].tag or fixed[type_id].data != normalized[type_id].data:
                raise ValueError("Type arena snapshot changes reserved primitive TypeIds")

        def entry_key(entry):
            tag, data = entry.tag, entry.data
            if tag in ("error", "any"):
                return (tag,)
            if tag in ("primitive", "struct", "enum", "interface", "class", "module"):
                return (tag, data)
            if tag in ("optional", "weak", "list"):
                return (tag, data)
            if tag in ("result", "dict"):
                return (tag, *data)
            if tag in ("tuple", "error_union"):
                return (tag, data)
            if tag == "function":
                params, returned, mutating = data
                return (tag, params, returned, mutating)
            if tag == "type_param":
                owner, name = data
                return (tag, owner, name)
            raise ValueError(f"Unsupported type arena entry tag: {tag}")

        keys = [entry_key(entry) for entry in normalized]
        if len(set(keys)) != len(keys):
            raise ValueError("Type arena contains duplicate canonical entries")

        interner._entries = normalized
        interner._capabilities = capabilities
        interner._key_to_id = {key: type_id for type_id, key in enumerate(keys)}
        interner._type_to_id = {}
        interner._id_to_type = {type_id: interner._id_to_type[type_id] for type_id in range(7)}

        def definition_id(name, raw, kind):
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid {kind} definition: {name}")
            type_id = require_id(raw.get("def_id"))
            entry = interner.get_entry(type_id)
            if entry.tag != kind or entry.data != name:
                raise ValueError(f"Mismatched {kind} definition identity: {name}")
            return type_id

        structs = snapshot.get("structs", {})
        enums = snapshot.get("enums", {})
        interfaces = snapshot.get("interfaces", {})
        classes = snapshot.get("classes", {})
        if not all(isinstance(items, dict) for items in (structs, enums, interfaces, classes)):
            raise ValueError("Invalid nominal type definitions")
        for name, raw in structs.items():
            type_id = definition_id(name, raw, "struct")
            fields = raw.get("fields")
            if not isinstance(fields, dict) or any(not isinstance(key, str) for key in fields):
                raise ValueError(f"Invalid struct fields: {name}")
            interner.struct_defs[name] = StructDef(type_id, name, {field: require_id(value) for field, value in fields.items()})
        for name, raw in enums.items():
            type_id = definition_id(name, raw, "enum")
            variants = raw.get("variants")
            payloads = raw.get("variant_payloads")
            type_args = raw.get("type_args")
            template_name = raw.get("template_name")
            if (not isinstance(variants, list) or any(not isinstance(item, str) for item in variants) or
                    not isinstance(payloads, dict) or not isinstance(type_args, list) or
                    (template_name is not None and not isinstance(template_name, str))):
                raise ValueError(f"Invalid enum definition: {name}")
            normalized_payloads = {}
            for variant, payload in payloads.items():
                if variant not in variants or not isinstance(payload, list):
                    raise ValueError(f"Invalid enum payload: {name}.{variant}")
                normalized_payloads[variant] = [require_id(item) for item in payload]
            interner.enum_defs[name] = EnumDef(
                type_id, name, list(variants), normalized_payloads,
                tuple(require_id(item) for item in type_args), template_name
            )
        for name, raw in interfaces.items():
            type_id = definition_id(name, raw, "interface")
            methods = raw.get("methods")
            if not isinstance(methods, dict) or any(not isinstance(key, str) for key in methods):
                raise ValueError(f"Invalid interface methods: {name}")
            normalized_methods = {method: require_id(value) for method, value in methods.items()}
            if any(interner.get_entry(value).tag != "function" for value in normalized_methods.values()):
                raise ValueError(f"Interface method signature is not a function: {name}")
            interner.interface_defs[name] = InterfaceDef(type_id, name, normalized_methods)
        for name, raw in classes.items():
            type_id = definition_id(name, raw, "class")
            superclass = raw.get("superclass")
            interfaces_raw = raw.get("interfaces")
            fields = raw.get("fields")
            methods = raw.get("methods")
            init_sig = raw.get("init_sig")
            if (superclass is not None and interner.get_entry(require_id(superclass)).tag != "class"):
                raise ValueError(f"Invalid superclass for {name}")
            if not isinstance(interfaces_raw, list) or any(
                    interner.get_entry(require_id(item)).tag != "interface" for item in interfaces_raw):
                raise ValueError(f"Invalid implemented interfaces for {name}")
            if not isinstance(fields, dict) or not isinstance(methods, dict):
                raise ValueError(f"Invalid class members: {name}")
            normalized_fields, normalized_methods = {}, {}
            for field, value in fields.items():
                if not isinstance(value, list) or len(value) != 2 or type(value[1]) is not bool:
                    raise ValueError(f"Invalid class field: {name}.{field}")
                normalized_fields[field] = (require_id(value[0]), value[1])
            for method, value in methods.items():
                if (not isinstance(value, list) or len(value) != 2 or type(value[1]) is not bool or
                        interner.get_entry(require_id(value[0])).tag != "function"):
                    raise ValueError(f"Invalid class method: {name}.{method}")
                normalized_methods[method] = (require_id(value[0]), value[1])
            if init_sig is not None and interner.get_entry(require_id(init_sig)).tag != "function":
                raise ValueError(f"Invalid initializer signature for {name}")
            interner.class_defs[name] = ClassDef(
                type_id, name, require_id(superclass) if superclass is not None else None,
                [require_id(item) for item in interfaces_raw], normalized_fields,
                normalized_methods, require_id(init_sig) if init_sig is not None else None
            )

        nominal_ids = {
            "struct": {entry.data for entry in normalized if entry.tag == "struct"},
            "enum": {entry.data for entry in normalized if entry.tag == "enum"},
            "interface": {entry.data for entry in normalized if entry.tag == "interface"},
            "class": {entry.data for entry in normalized if entry.tag == "class"},
        }
        definitions = {
            "struct": set(interner.struct_defs), "enum": set(interner.enum_defs),
            "interface": set(interner.interface_defs), "class": set(interner.class_defs),
        }
        if any(nominal_ids[kind] != definitions[kind] for kind in nominal_ids):
            raise ValueError("Type arena has incomplete nominal definitions")
        return interner

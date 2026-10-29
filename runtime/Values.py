import weakref
from typing import Any, Dict, List, Optional

def nominal_display_name(name):
    generic_prefix = '_WYSNA_G_'
    if name.startswith(generic_prefix):
        length_end = name.find('_', len(generic_prefix))
        length_text = name[len(generic_prefix):length_end]
        if length_end >= 0 and length_text.isascii() and length_text.isdigit():
            start = length_end + 1
            end = start + int(length_text)
            if end <= len(name) and name[end:end + 2] == '__':
                name = name[start:end]
    module_prefix = '__wysna_module_'
    if name.startswith(module_prefix):
        boundary = name.find('__', len(module_prefix))
        if boundary >= 0:
            return name[boundary + 2:]
    return name

def display_value(value, quote_string=False):
    parts = []
    active_objects = set()
    tasks = [("value", value, quote_string)]
    while tasks:
        kind, current, quoted = tasks.pop()
        if kind == "text":
            parts.append(current)
            continue
        if kind == "leave":
            active_objects.remove(current)
            continue
        if isinstance(current, WysnaEnumValue) and current.enum_name == "Option" and current.variant == "Some" and current.payload:
            tasks.append(("value", current.payload[0], quoted))
            continue
        if isinstance(current, WysnaObjectInstance):
            identity = id(current)
            if identity in active_objects:
                parts.append("<cycle>")
                continue
            active_objects.add(identity)
            tasks.append(("leave", identity, False))
            fields = list(current.fields.items())
            prefix, suffix = nominal_display_name(current.class_name) + " { ", " }"
        elif isinstance(current, WysnaStructInstance):
            fields = list(current.storage.data.items())
            prefix, suffix = nominal_display_name(current.struct_name) + " { ", " }"
        else:
            fields = None
        if fields is not None:
            tasks.append(("text", suffix, False))
            for index in range(len(fields) - 1, -1, -1):
                name, child = fields[index]
                tasks.append(("value", child, True))
                tasks.append(("text", name + ": ", False))
                if index:
                    tasks.append(("text", ", ", False))
            tasks.append(("text", prefix, False))
            continue
        if isinstance(current, WysnaList):
            elements = current.storage.data
            prefix, suffix = "[", "]"
        elif isinstance(current, WysnaTuple):
            elements = current.elements
            prefix, suffix = "(", ")"
        elif isinstance(current, WysnaEnumValue) and current.payload:
            elements = current.payload
            prefix, suffix = nominal_display_name(current.enum_name) + "." + current.variant + "(", ")"
        else:
            elements = None
        if elements is not None:
            tasks.append(("text", suffix, False))
            for index in range(len(elements) - 1, -1, -1):
                tasks.append(("value", elements[index], True))
                if index:
                    tasks.append(("text", ", ", False))
            tasks.append(("text", prefix, False))
        elif isinstance(current, WysnaDict):
            entries = list(current.storage.data.items())
            tasks.append(("text", "}", False))
            for index in range(len(entries) - 1, -1, -1):
                key, child = entries[index]
                tasks.extend([("value", child, True), ("text", ": ", False), ("value", key, True)])
                if index:
                    tasks.append(("text", ", ", False))
            tasks.append(("text", "{", False))
        elif isinstance(current, (WysnaOk, WysnaErr)):
            child = current.value if isinstance(current, WysnaOk) else current.error
            tasks.extend([("text", ")", False), ("value", child, False),
                          ("text", "Ok(" if isinstance(current, WysnaOk) else "Err(", False)])
        else:
            parts.append(repr(current) if quoted and isinstance(current, WysnaStr) else str(current))
    return "".join(parts)

class WysnaValue:
    """Base class for Wysna runtime values."""
    def clone_value(self) -> 'WysnaValue':
        return self

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        raise NotImplementedError

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        return self.__str__()


# ==========================================
# Primitive Runtime Values
# ==========================================

class WysnaInt(WysnaValue):
    def __init__(self, value: int):
        v = int(value) & 0xFFFFFFFFFFFFFFFF
        if v >= 0x8000000000000000:
            v -= 0x10000000000000000
        self.value = v

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaInt) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __str__(self) -> str:
        return str(self.value)


class WysnaFloat(WysnaValue):
    def __init__(self, value: float):
        self.value = float(value)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaFloat) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __str__(self) -> str:
        return str(self.value)


class WysnaBool(WysnaValue):
    def __init__(self, value: bool):
        self.value = bool(value)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaBool) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __str__(self) -> str:
        return "true" if self.value else "false"


class WysnaStr(WysnaValue):
    def __init__(self, value: str):
        self.value = str(value)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaStr) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return '"' + self.value.replace("\0", "\\0") + '"'


class WysnaUnit(WysnaValue):
    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaUnit)

    def __str__(self) -> str:
        return "unit"


class WysnaNone(WysnaValue):
    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        if isinstance(other, WysnaNone):
            return True
        if isinstance(other, WysnaEnumValue) and (other.enum_name == "Option" or other.enum_name.startswith("Option__")) and other.variant == "None":
            return True
        return False

    def __str__(self) -> str:
        return "none"


class WysnaTuple(WysnaValue):
    def __init__(self, elements: List[WysnaValue]):
        self.elements = elements

    def clone_value(self) -> 'WysnaTuple':
        return WysnaTuple([e.clone_value() for e in self.elements])

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaTuple) and self.elements == other.elements

    def __str__(self) -> str:
        return display_value(self)


class WysnaRange(WysnaValue):
    def __init__(self, start: int, end: int, inclusive: bool = False):
        self.start = start
        self.end = end
        self.inclusive = inclusive

    def __iter__(self):
        stop = self.end + 1 if self.inclusive else self.end
        for i in range(self.start, stop):
            yield WysnaInt(i)

    def __str__(self) -> str:
        op = "..=" if self.inclusive else ".."
        return f"{self.start}{op}{self.end}"


class WysnaEnumValue(WysnaValue):
    def __new__(cls, enum_name, variant, payload=None):
        builtin = enum_name.split("__", 1)[0]
        if builtin == "Result" and variant in ("Ok", "Err") and payload is not None:
            return (WysnaOk if variant == "Ok" else WysnaErr)(payload[0] if payload else WysnaUnit())
        if builtin == "Option" and variant in ("None", "none"):
            return WysnaNone()
        return super().__new__(cls)

    def __init__(self, enum_name: str, variant: str, payload: Optional[List[WysnaValue]] = None):
        self.enum_name = "Option" if enum_name.startswith("Option__") else enum_name
        self.variant = variant
        self.payload: List[WysnaValue] = payload if payload is not None else []

    def __hash__(self) -> int:
        return hash((self.enum_name, self.variant, tuple(self.payload)))

    def clone_value(self) -> 'WysnaEnumValue':
        return WysnaEnumValue(self.enum_name, self.variant, [p.clone_value() for p in self.payload])

    def __eq__(self, other: Any) -> bool:
        self_some = self.enum_name == "Option" and self.variant == "Some"
        other_some = isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some"
        if self_some and not other_some and not isinstance(other, WysnaNone):
            return bool(self.payload) and self.payload[0] == other
        if other_some and not self_some:
            return other == self
        if isinstance(other, WysnaEnumValue):
            return self.enum_name == other.enum_name and self.variant == other.variant and self.payload == other.payload
        if (self.enum_name == "Option" or self.enum_name.startswith("Option__")) and self.variant == "None" and isinstance(other, WysnaNone):
            return True
        if (self.enum_name == "Result" or self.enum_name.startswith("Result__")) and self.variant == "Ok" and isinstance(other, WysnaOk):
            return len(self.payload) == 1 and self.payload[0] == other.value
        if (self.enum_name == "Result" or self.enum_name.startswith("Result__")) and self.variant == "Err" and isinstance(other, WysnaErr):
            return len(self.payload) == 1 and self.payload[0] == other.error
        return False

    def __str__(self) -> str:
        if self.payload:
            return display_value(self)
        return f"{nominal_display_name(self.enum_name)}.{self.variant}"


# ==========================================
# Result & Fallible Values (T ! E)
# ==========================================

class WysnaResult(WysnaValue):
    pass

class WysnaOk(WysnaResult):
    def __init__(self, value: WysnaValue):
        self.value = value

    def clone_value(self) -> 'WysnaOk':
        return WysnaOk(self.value.clone_value())

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        if isinstance(other, WysnaOk):
            return self.value == other.value
        if isinstance(other, WysnaEnumValue) and (other.enum_name == "Result" or other.enum_name.startswith("Result__")) and other.variant == "Ok" and len(other.payload) == 1:
            return self.value == other.payload[0]
        return False

    def __str__(self) -> str:
        return display_value(self)

class WysnaErr(WysnaResult):
    def __init__(self, error: WysnaValue):
        self.error = error

    def clone_value(self) -> 'WysnaErr':
        return WysnaErr(self.error.clone_value())

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        if isinstance(other, WysnaErr):
            return self.error == other.error
        if isinstance(other, WysnaEnumValue) and (other.enum_name == "Result" or other.enum_name.startswith("Result__")) and other.variant == "Err" and len(other.payload) == 1:
            return self.error == other.payload[0]
        return False

    def __str__(self) -> str:
        return display_value(self)


# ==========================================
# Copy-on-Write Backing Storage
# ==========================================

class CowTelemetry:
    """Process-wide logical CoW counters; Python storage has no C ABI byte model."""
    def __init__(self):
        self.detach_list: int = 0
        self.detach_dict: int = 0
        self.detach_struct: int = 0
        self.copied_slots: int = 0

    def reset(self) -> None:
        self.detach_list = 0
        self.detach_dict = 0
        self.detach_struct = 0
        self.copied_slots = 0

cow_telemetry = CowTelemetry()


class SharedBuffer:
    """Refcounted container to implement pure Value Semantics with Copy-on-Write."""
    def __init__(self, data: Any):
        self.data = data
        self.refcount = 1

    def __repr__(self) -> str:
        return f"SharedBuffer(refcount={self.refcount}, data={self.data})"


class CowOwner(WysnaValue):
    """Each live value wrapper owns one reference to its shared buffer."""
    def __del__(self):
        storage = getattr(self, "storage", None)
        if storage is not None:
            storage.refcount -= 1


class WysnaList(CowOwner):
    def __init__(self, storage: Optional[SharedBuffer] = None, elements: Optional[List[WysnaValue]] = None):
        if storage is not None:
            self.storage = storage
        else:
            cloned_elems = [e.clone_value() for e in elements] if elements is not None else []
            self.storage = SharedBuffer(cloned_elems)

    def clone_value(self) -> 'WysnaList':
        self.storage.refcount += 1
        return WysnaList(storage=self.storage)

    def ensure_unique(self) -> None:
        if self.storage.refcount > 1:
            self.storage.refcount -= 1
            n = len(self.storage.data)
            cow_telemetry.detach_list += 1
            cow_telemetry.copied_slots += n
            # Perform CoW detachment
            self.storage = SharedBuffer([elem.clone_value() for elem in self.storage.data])

    def append(self, elem: WysnaValue) -> None:
        self.ensure_unique()
        self.storage.data.append(elem.clone_value())

    def pop(self, idx: int = -1) -> WysnaValue:
        self.ensure_unique()
        return self.storage.data.pop(idx)

    def clear(self) -> None:
        self.ensure_unique()
        self.storage.data.clear()

    def get(self, idx: int) -> WysnaValue:
        return self.storage.data[idx]

    def set(self, idx: int, val: WysnaValue) -> None:
        self.ensure_unique()
        self.storage.data[idx] = val.clone_value()

    def __len__(self) -> int:
        return len(self.storage.data)

    def __iter__(self):
        return iter(self.storage.data)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaList) and self.storage.data == other.storage.data

    def __str__(self) -> str:
        return display_value(self)


def dict_key(key):
    if isinstance(key, WysnaValue):
        return key
    if isinstance(key, bool):
        return WysnaBool(key)
    if isinstance(key, str):
        return WysnaStr(key)
    return WysnaInt(key)


class WysnaDict(CowOwner):
    def __init__(self, storage: Optional[SharedBuffer] = None, pairs: Optional[Dict[Any, WysnaValue]] = None):
        if storage is not None:
            self.storage = storage
        else:
            cloned_pairs = {dict_key(k): v.clone_value() for k, v in pairs.items()} if pairs is not None else {}
            self.storage = SharedBuffer(cloned_pairs)

    def clone_value(self) -> 'WysnaDict':
        self.storage.refcount += 1
        return WysnaDict(storage=self.storage)

    def ensure_unique(self) -> None:
        if self.storage.refcount > 1:
            self.storage.refcount -= 1
            n = len(self.storage.data)
            cow_telemetry.detach_dict += 1
            cow_telemetry.copied_slots += n * 2
            self.storage = SharedBuffer({k: v.clone_value() for k, v in self.storage.data.items()})

    def set(self, key: Any, val: WysnaValue) -> None:
        self.ensure_unique()
        self.storage.data[dict_key(key)] = val.clone_value()

    def get(self, key: Any) -> Optional[WysnaValue]:
        return self.storage.data.get(dict_key(key))

    def keys(self) -> List[WysnaValue]:
        return [k.clone_value() for k in self.storage.data.keys()]

    def values(self) -> List[WysnaValue]:
        return list(self.storage.data.values())

    def __len__(self) -> int:
        return len(self.storage.data)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaDict) and self.storage.data == other.storage.data

    def __str__(self) -> str:
        return display_value(self)


class WysnaStructInstance(CowOwner):
    def __init__(self, struct_name: str, storage: Optional[SharedBuffer] = None, fields: Optional[Dict[str, WysnaValue]] = None):
        self.struct_name = struct_name
        if storage is not None:
            self.storage = storage
        else:
            cloned_fields = {k: v.clone_value() for k, v in fields.items()} if fields is not None else {}
            self.storage = SharedBuffer(cloned_fields)

    def clone_value(self) -> 'WysnaStructInstance':
        self.storage.refcount += 1
        return WysnaStructInstance(self.struct_name, storage=self.storage)

    def ensure_unique(self) -> None:
        if self.storage.refcount > 1:
            self.storage.refcount -= 1
            n = len(self.storage.data)
            cow_telemetry.detach_struct += 1
            cow_telemetry.copied_slots += n
            self.storage = SharedBuffer({k: v.clone_value() for k, v in self.storage.data.items()})

    def get_field(self, name: str) -> WysnaValue:
        return self.storage.data[name]

    def set_field(self, name: str, val: WysnaValue) -> None:
        self.ensure_unique()
        self.storage.data[name] = val.clone_value()

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaStructInstance) and self.struct_name == other.struct_name and self.storage.data == other.storage.data

    def __str__(self) -> str:
        return display_value(self)


# ==========================================
# Functions & Closures
# ==========================================

class WysnaFunction(WysnaValue):
    def __init__(self, decl: Any, closure_env: Any):
        self.decl = decl
        self.closure_env = closure_env

    def __str__(self) -> str:
        return f"<fn {self.decl.name}>"


class WysnaNativeFunction(WysnaValue):
    def __init__(self, name: str, call_fn: Any):
        self.name = name
        self.call_fn = call_fn

    def __str__(self) -> str:
        return f"<native fn {self.name}>"


class WysnaBoundMethod(WysnaValue):
    def __init__(self, receiver: WysnaValue, method_name: str, target_class_name: Optional[str] = None):
        self.receiver = receiver
        self.method_name = method_name
        self.target_class_name = target_class_name

    def __str__(self) -> str:
        return f"<bound method {self.method_name}>"


class WysnaClassDef(WysnaValue):
    def __init__(self, name: str, superclass_name: Optional[str], fields: List[Any], methods: Dict[str, Any], closure_env: Any):
        self.name = name
        self.superclass_name = superclass_name
        self.fields = fields
        self.methods = methods
        self.closure_env = closure_env

    def __str__(self) -> str:
        return f"<class {self.name}>"


class WysnaObjectInstance(WysnaValue):
    """Heap-allocated class instance with reference semantics (mutations happen in-place)."""
    def __init__(self, class_name: str, fields: Optional[Dict[str, WysnaValue]] = None):
        self.class_name = class_name
        self.fields: Dict[str, WysnaValue] = {k: v.clone_value() for k, v in fields.items()} if fields is not None else {}

    def clone_value(self) -> 'WysnaObjectInstance':
        # Reference semantics: copying a reference points to the same object instance
        return self

    def get_field(self, name: str) -> WysnaValue:
        return self.fields[name]

    def set_field(self, name: str, val: WysnaValue) -> None:
        self.fields[name] = val.clone_value()

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return self is other

    def __str__(self) -> str:
        return display_value(self)


class WysnaWeak(WysnaValue):
    def __init__(self, target: Optional[WysnaObjectInstance] = None):
        if target is None:
            self.identity = object()
            self.reference = lambda: None
            return
        if not hasattr(target, "weak_identity"):
            target.weak_identity = object()
        self.identity = target.weak_identity
        self.reference = weakref.ref(target)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WysnaEnumValue) and other.enum_name == "Option" and other.variant == "Some":
            return other == self
        return isinstance(other, WysnaWeak) and self.identity is other.identity

    def __str__(self) -> str:
        return "weak(alive)" if self.reference() is not None else "weak(expired)"


class WysnaSuperProxy(WysnaValue):
    """Proxy binding 'super' inside a method to dispatch starting at the defining class's superclass."""
    def __init__(self, receiver: WysnaObjectInstance, superclass_name: str):
        self.receiver = receiver
        self.superclass_name = superclass_name

    def __str__(self) -> str:
        return f"<super {self.superclass_name}>"


class WysnaEnumNamespace(WysnaValue):
    def __init__(self, name: str, variants: Any):
        self.name = name
        self.variants: Set[str] = set()
        if isinstance(variants, (list, set)):
            for v in variants:
                if hasattr(v, "name"):
                    self.variants.add(v.name)
                else:
                    self.variants.add(str(v))

    def get_variant(self, variant: str) -> WysnaEnumValue:
        if variant not in self.variants:
            raise KeyError(f"Enum '{self.name}' has no variant '{variant}'")
        return WysnaEnumValue(self.name, variant)

    def __str__(self) -> str:
        return f"<enum {self.name}>"


class WysnaModule(WysnaValue):
    def __init__(self, name: str, env: Any):
        self.name = name
        self.env = env

    def get_member(self, member: str) -> WysnaValue:
        return self.env.lookup(member)

    def __str__(self) -> str:
        return f"<module {self.name}>"




class WysnaPointer(WysnaValue):
    def __init__(self, address, close=None):
        self.address = address
        self._close = close

    def __del__(self):
        if self._close is not None and self.address:
            self._close(self.address)

    def __eq__(self, other):
        return isinstance(other, WysnaPointer) and self.address == other.address

    def __str__(self):
        return "<opaque>"

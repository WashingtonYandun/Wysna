"""Canonical queries over Wysna's session-local type arena."""
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional

from checker.TypeInterner import TypeInterner
from checker.WysnaType import TypeParamType, WysnaType


@dataclass(frozen=True)
class RelationObligation:
    """A relation whose answer depends on a generic type parameter."""

    kind: str
    source: Any
    target: Any = None
    operator: Optional[str] = None
    file: str = "<generic>"
    line: int = 1
    col: int = 1


class TypeRelations:
    """Single semantic entry point for type relations within one arena.

    The interner remains the canonical representation and implementation of
    concrete relations. This facade accepts legacy ``WysnaType`` objects only
    during migration and immediately converts them to arena-local TypeIds.
    Generic definition checking can capture symbolic relations; ordinary
    checking never treats an unresolved relation as proven.
    """

    def __init__(self, interner: TypeInterner):
        self.interner = interner
        self._obligation_stack: List[List[RelationObligation]] = []

    def _type_id(self, value: int | WysnaType) -> int:
        if isinstance(value, bool):
            raise TypeError("Boolean values are not TypeIds")
        if isinstance(value, int):
            if not 0 <= value < len(self.interner._entries):
                raise ValueError(f"TypeId {value} is outside its arena")
            return value
        return self.interner.intern(value)

    def _contains_type_parameter(self, type_id: int, seen=None) -> bool:
        if seen is None:
            seen = set()
        if type_id in seen:
            return False
        seen.add(type_id)
        entry = self.interner.get_entry(type_id)
        if entry.tag == "type_param":
            return True
        if entry.tag in ("optional", "weak", "list"):
            return self._contains_type_parameter(entry.data, seen)
        if entry.tag in ("result", "error_union", "tuple", "dict"):
            return any(self._contains_type_parameter(item, seen) for item in entry.data)
        if entry.tag == "function":
            params, returned, _ = entry.data
            return any(self._contains_type_parameter(item, seen) for item in (*params, returned))
        name = entry.data if entry.tag in ("struct", "enum", "class", "interface") else None
        if name is None:
            return False
        if entry.tag == "struct":
            definition = self.interner.struct_defs.get(name)
            children = definition.fields.values() if definition else ()
        elif entry.tag == "enum":
            definition = self.interner.enum_defs.get(name)
            children = list(definition.type_args) if definition else []
            if definition:
                children.extend(item for payload in definition.variant_payloads.values() for item in payload)
        elif entry.tag == "class":
            definition = self.interner.class_defs.get(name)
            children = []
            if definition:
                children.extend(type_id for type_id, _ in definition.fields.values())
                children.extend(type_id for type_id, _ in definition.methods.values())
                children.extend(definition.interfaces)
                if definition.superclass is not None:
                    children.append(definition.superclass)
                if definition.init_sig is not None:
                    children.append(definition.init_sig)
        else:
            definition = self.interner.interface_defs.get(name)
            children = definition.methods.values() if definition else ()
        return any(self._contains_type_parameter(child, seen) for child in children)

    def _record(self, obligation: RelationObligation) -> bool:
        if not self._obligation_stack:
            return False
        self._obligation_stack[-1].append(obligation)
        return True

    @contextmanager
    def capture_obligations(self) -> Iterator[List[RelationObligation]]:
        obligations: List[RelationObligation] = []
        self._obligation_stack.append(obligations)
        try:
            yield obligations
        finally:
            self._obligation_stack.pop()

    def record_operation(self, operator: str, operands, *, file="<generic>", line=1, col=1) -> bool:
        return self._record(RelationObligation(
            "operator", tuple(self._type_id(item) for item in operands),
            operator=operator, file=file, line=line, col=col))

    def record_requirement(self, kind: str, subject, detail=None, *, file="<generic>", line=1, col=1) -> bool:
        return self._record(RelationObligation(
            kind, self._type_id(subject), detail, file=file, line=line, col=col))

    def is_assignable(self, source: int | WysnaType, target: int | WysnaType) -> bool:
        source_id, target_id = self._type_id(source), self._type_id(target)
        if source_id == target_id:
            return True
        if self._contains_type_parameter(source_id) or self._contains_type_parameter(target_id):
            if self._record(RelationObligation("assignable", source_id, target_id)):
                return True
            return False
        return self.interner.is_assignable(source_id, target_id)

    def equivalent(self, left: int | WysnaType, right: int | WysnaType) -> bool:
        left_id, right_id = self._type_id(left), self._type_id(right)
        if left_id == right_id:
            return True
        if self._contains_type_parameter(left_id) or self._contains_type_parameter(right_id):
            return self._record(RelationObligation("equivalent", left_id, right_id))
        return False

    def equality_compatible(self, left: int | WysnaType, right: int | WysnaType) -> bool:
        left_id, right_id = self._type_id(left), self._type_id(right)
        if self._contains_type_parameter(left_id) or self._contains_type_parameter(right_id):
            if self._record(RelationObligation("equality", left_id, right_id)):
                return True
            return False
        return self.interner.equality_compatible(left_id, right_id)

    def is_error_channel_assignable(self, source: int | WysnaType, target: int | WysnaType) -> bool:
        source_id, target_id = self._type_id(source), self._type_id(target)
        if source_id == target_id:
            return True
        if self._contains_type_parameter(source_id) or self._contains_type_parameter(target_id):
            if self._record(RelationObligation("error_channel", source_id, target_id)):
                return True
            return False
        return self.interner.is_error_channel_assignable(source_id, target_id)

    def capabilities(self, type_: int | WysnaType):
        return self.interner.get_capabilities(self._type_id(type_))

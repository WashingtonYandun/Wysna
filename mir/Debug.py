"""Optional MIR-owned debug sidecar; it never carries syntax or type-checker state."""
from dataclasses import dataclass, field
from mir.Nodes import Source
from error.Error import WysnaError


@dataclass
class DebugScope:
    parent: int | None
    source: Source


@dataclass(frozen=True)
class DebugBinding:
    slot: int
    name: str
    scope: int
    source: Source
    argument: int = 0


@dataclass
class FunctionDebug:
    scopes: list = field(default_factory=list)
    bindings: list = field(default_factory=list)
    instructions: dict = field(default_factory=dict)

    def mark(self, instruction, scope):
        self.instructions[id(instruction)] = (instruction, scope)

    def scope_for(self, instruction):
        recorded = self.instructions.get(id(instruction))
        return recorded[1] if recorded and recorded[0] is instruction else 0


class DebugModule:
    def __init__(self):
        self.module = None
        self.functions = {}
        self.globals = []

    def require_owner(self, module):
        if self.module is not module:
            raise WysnaError('Debug sidecar belongs to a different MIR module', code='W9999', stage='mir-debug')

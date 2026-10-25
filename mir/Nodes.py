"""Backend-independent MIR data. No syntax nodes or C fragments are operands."""
from dataclasses import dataclass, field, asdict
import json


@dataclass(frozen=True)
class Source:
    file: str = '<stdin>'
    line: int = 1
    col: int = 1


@dataclass(frozen=True)
class Slot:
    id: int
    type_id: int
    name: str
    storage: str = 'value'
    ownership: str = 'owned'
    parameter: bool = False


@dataclass(frozen=True)
class Projection:
    kind: str
    index: int
    owner_type: int
    type_id: int
    source: Source = field(default_factory=Source)


@dataclass(frozen=True)
class Place:
    root: int
    projections: tuple = ()


EFFECTS = {
    'const': 'pure', 'clone': 'retain', 'narrow': 'retain', 'store': 'move', 'release': 'release',
    'runtime': 'call', 'call': 'call', 'virtual': 'call', 'indirect': 'call',
    'construct': 'allocate', 'function': 'allocate', 'load_field': 'retain',
    'store_place': 'write', 'mutate': 'write', 'test': 'read', 'tuple_get': 'retain',
    'range_count': 'check', 'counter': 'pure', 'increment': 'pure',
    'iterate_test': 'read', 'iterate_value': 'retain', 'set_vtable': 'write',
}


@dataclass
class Operation:
    opcode: str
    result: int | None = None
    args: tuple = ()
    data: dict = field(default_factory=dict)
    moves: tuple = ()
    source: Source = field(default_factory=Source)
    effect: str = ''

    def __post_init__(self):
        if not self.effect:
            self.effect = EFFECTS.get(self.opcode, '')


@dataclass
class Terminator:
    kind: str
    targets: tuple = ()
    value: int | None = None
    source: Source = field(default_factory=Source)


@dataclass
class BasicBlock:
    id: int
    operations: list = field(default_factory=list)
    terminator: Terminator | None = None


@dataclass
class Function:
    name: str
    return_type: int
    slots: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    params: list = field(default_factory=list)
    source: Source = field(default_factory=Source)


@dataclass
class Layout:
    name: str
    kind: str
    type_id: int
    fields: list
    parent: str | None = None
    methods: dict = field(default_factory=dict)


@dataclass
class Module:
    file: str
    arena_id: str
    functions: list = field(default_factory=list)
    globals: list = field(default_factory=list)
    layouts: dict = field(default_factory=dict)
    virtual_slots: list = field(default_factory=list)
    externs: dict = field(default_factory=dict)
    exports: list = field(default_factory=list)
    library_mode: bool = False
    enum_count: int = 0


def dump(module):
    return json.dumps(asdict(module), indent=2, sort_keys=True) + '\n'

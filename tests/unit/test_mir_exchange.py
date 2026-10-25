"""Verification of versioned, self-contained MIR exchange contracts."""
import json
import pytest

from error.Error import WysnaError
from mir.Exchange import (
    FORMAT,
    RUNTIME_ABI_VERSION,
    SCHEMA_VERSION,
    SEMANTIC_REVISION,
    export_module,
    import_module,
)
from mir.Nodes import Slot
from tests.unit.test_mir import lower


def test_mir_exchange_roundtrip_basic():
    source = """
    fn add(x: int, y: int) -> int {
        return x + y
    }
    let result = add(10, 20)
    print(result)
    """
    module, interner = lower(source)
    payload = export_module(module, interner)
    assert isinstance(payload, str)

    envelope = json.loads(payload)
    assert envelope["format"] == FORMAT
    assert envelope["schema_version"] == SCHEMA_VERSION
    assert envelope["semantic_revision"] == SEMANTIC_REVISION
    assert envelope["runtime_abi_version"] == RUNTIME_ABI_VERSION
    assert "type_arena" in envelope
    assert "module" in envelope

    restored_mod, restored_interner = import_module(payload)
    assert len(restored_mod.functions) == len(module.functions)
    assert restored_mod.arena_id == restored_interner.arena_id


def test_mir_exchange_roundtrip_complex_types():
    source = """
    struct Point {
        x: int
        y: int
    }
    enum State {
        Pending
        Done(int)
    }
    fn compute(p: Point) -> State ! str {
        if p.x > 0 {
            return State.Done(p.x + p.y)
        }
        return Err("negative x")
    }
    let pt = Point { x: 5, y: 15 }
    let res = compute(pt) else err { State.Pending }
    """
    module, interner = lower(source)
    payload = export_module(module, interner)
    restored_mod, restored_interner = import_module(payload)

    assert len(restored_mod.layouts) == len(module.layouts)
    assert len(restored_mod.functions) == len(module.functions)
    assert "Point" in restored_interner.struct_defs
    assert "State" in restored_interner.enum_defs


def test_mir_exchange_rejects_unsupported_format_and_versions():
    module, interner = lower("fn nop() -> unit {}")
    payload = export_module(module, interner)

    # Bad format
    bad_fmt = json.loads(payload)
    bad_fmt["format"] = "other.ir"
    with pytest.raises(WysnaError) as exc:
        import_module(json.dumps(bad_fmt))
    assert exc.value.stage == "mir-exchange"

    # Unsupported schema version
    bad_ver = json.loads(payload)
    bad_ver["schema_version"] = 999
    with pytest.raises(WysnaError) as exc:
        import_module(json.dumps(bad_ver))
    assert "schema version" in str(exc.value)

    # Unsupported ABI version
    bad_abi = json.loads(payload)
    bad_abi["runtime_abi_version"] = 999
    with pytest.raises(WysnaError) as exc:
        import_module(json.dumps(bad_abi))
    assert "runtime ABI version" in str(exc.value)


def test_mir_exchange_rejects_corrupted_type_arena():
    module, interner = lower("fn nop() -> unit {}")
    payload = export_module(module, interner)

    envelope = json.loads(payload)
    envelope["type_arena"]["entries"] = []  # Empty arena cannot validate TypeIds
    with pytest.raises(WysnaError) as exc:
        import_module(json.dumps(envelope))
    assert exc.value.stage == "mir-exchange"


def test_mir_exchange_rejects_unresolved_type_parameter():
    module, interner = lower("fn nop() -> unit {}")
    tp_id = interner.mk_type_param("T", owner="foo")

    # Add an unresolved type parameter to module globals
    module.globals.append(Slot(id=len(module.globals), type_id=tp_id, name="g_test", storage="value"))

    with pytest.raises(WysnaError) as exc:
        export_module(module, interner)
    assert "unresolved generic type parameter" in str(exc.value)

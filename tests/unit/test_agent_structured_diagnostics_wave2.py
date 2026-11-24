"""Validate structured checker facts for W2002, W2003, W2004, W3001, and W1002 across Seed, Selfhost, and MCP."""
import json
from pathlib import Path
import sys

import pytest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tests.unit.test_agent_protocol_schema import _assert_matches_protocol, _run_check_cli, _run_check_mcp

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DOC = json.loads((ROOT / 'schemas/wysna-tool-result.v1.schema.json').read_text(encoding='utf-8'))
SEED = [sys.executable, str(ROOT / 'main.py')]


@pytest.fixture(params=['seed', 'selfhost'])
def toolchain(request):
    if request.param == 'seed':
        return SEED
    return [ensure_selfhost_compiler_binary(str(ROOT))]


def _validate_data(diag: dict, def_name: str):
    try:
        import jsonschema
    except ImportError:
        return
    sub_schema = {
        '$schema': SCHEMA_DOC['$schema'],
        '$defs': SCHEMA_DOC['$defs'],
        '$ref': f'#/$defs/{def_name}',
    }
    jsonschema.Draft202012Validator(sub_schema).validate(diag['data'])


def test_w2002_invalid_assignment_target_facts(toolchain, tmp_path):
    target = tmp_path / 'w2002.wysna'
    target.write_text(
        'fn helper() -> int { return 1 }\n'
        'fn bad() {\n'
        '    helper() = 5\n'
        '}\n',
        encoding='utf-8',
    )
    payload = _run_check_cli(toolchain, target)
    _assert_matches_protocol(payload, SCHEMA_DOC)
    diag = payload['diagnostics'][0]
    assert diag['code'] == 'W2002'
    assert diag['data']['kind'] == 'invalid_assignment_target'
    assert diag['data']['target_kind']
    assert diag['data']['help']
    _validate_data(diag, 'InvalidAssignmentTargetData')


def test_w2003_non_exhaustive_match_closed_and_open_domain_facts(toolchain, tmp_path):
    closed = tmp_path / 'w2003_closed.wysna'
    closed.write_text(
        'enum Color { Red, Green, Blue }\n'
        'fn label(c: Color) -> int {\n'
        '    return match c {\n'
        '        Color.Red => 1,\n'
        '    }\n'
        '}\n',
        encoding='utf-8',
    )
    for runner in (lambda: _run_check_cli(toolchain, closed), lambda: _run_check_mcp(toolchain, tmp_path, 'w2003_closed.wysna')):
        payload = runner()
        _assert_matches_protocol(payload, SCHEMA_DOC)
        diag = payload['diagnostics'][0]
        assert diag['code'] == 'W2003'
        data = diag['data']
        assert data['kind'] == 'non_exhaustive_match'
        assert data['open_domain'] is False
        assert data['subject_type'] == 'Color'
        assert len(data['missing_variants']) >= 2
        assert data['uncovered_pattern_families'] == data['missing_variants']
        _validate_data(diag, 'NonExhaustiveMatchData')

    open_dom = tmp_path / 'w2003_open.wysna'
    open_dom.write_text(
        'fn score(x: int) -> int {\n'
        '    return match x {\n'
        '        1 => 10,\n'
        '    }\n'
        '}\n',
        encoding='utf-8',
    )
    payload_open = _run_check_cli(toolchain, open_dom)
    _assert_matches_protocol(payload_open, SCHEMA_DOC)
    diag_open = payload_open['diagnostics'][0]
    assert diag_open['code'] == 'W2003'
    assert diag_open['data']['kind'] == 'non_exhaustive_match'
    assert diag_open['data']['open_domain'] is True
    assert diag_open['data']['uncovered_pattern_families'] == ['_']
    _validate_data(diag_open, 'NonExhaustiveMatchData')


def test_w2004_return_mismatch_and_missing_return_include_related_decl(toolchain, tmp_path):
    mismatch = tmp_path / 'w2004_ret.wysna'
    mismatch.write_text(
        'fn compute(flag: bool) -> int {\n'
        '    return "bad"\n'
        '}\n',
        encoding='utf-8',
    )
    payload = _run_check_cli(toolchain, mismatch)
    _assert_matches_protocol(payload, SCHEMA_DOC)
    diag = payload['diagnostics'][0]
    assert diag['code'] == 'W2004'
    assert diag['data']['kind'] == 'return_type_mismatch'
    assert diag['data']['expected'] == 'int'
    assert diag['data']['found'] == 'str'
    assert diag['data']['function'] == 'compute'
    assert diag['related'] and diag['related'][0]['line'] == 1
    _validate_data(diag, 'ReturnMismatchData')

    missing = tmp_path / 'w2004_missing.wysna'
    missing.write_text(
        'fn compute(flag: bool) -> int {\n'
        '    let x = 1\n'
        '}\n',
        encoding='utf-8',
    )
    payload_miss = _run_check_cli(toolchain, missing)
    _assert_matches_protocol(payload_miss, SCHEMA_DOC)
    diag_miss = payload_miss['diagnostics'][0]
    assert diag_miss['code'] == 'W2004'
    assert diag_miss['data']['kind'] == 'missing_return'
    assert diag_miss['data']['expected'] == 'int'
    assert diag_miss['data']['function'] == 'compute'
    assert diag_miss['related'] and diag_miss['related'][0]['line'] == 1
    _validate_data(diag_miss, 'MissingReturnData')


def test_w3001_and_w1002_emit_structured_data_and_related_spans(toolchain, tmp_path):
    imm = tmp_path / 'w3001_imm.wysna'
    imm.write_text(
        'fn run() -> int {\n'
        '    let total = 1\n'
        '    total = 2\n'
        '    return total\n'
        '}\n',
        encoding='utf-8',
    )
    p_imm = _run_check_cli(toolchain, imm)
    _assert_matches_protocol(p_imm, SCHEMA_DOC)
    d_imm = p_imm['diagnostics'][0]
    assert d_imm['code'] == 'W3001'
    assert d_imm['data']['kind'] == 'immutable_mutation'
    assert d_imm['data']['binding'] == 'total'
    assert d_imm['related'] and d_imm['related'][0]['line'] == 2
    _validate_data(d_imm, 'ImmutableMutationData')

    cap = tmp_path / 'w3001_cap.wysna'
    cap.write_text(
        'fn run() {\n'
        '    let mut items = [1]\n'
        '    let f = |v: int| -> unit { items.append(v) }\n'
        '}\n',
        encoding='utf-8',
    )
    p_cap = _run_check_cli(toolchain, cap)
    _assert_matches_protocol(p_cap, SCHEMA_DOC)
    d_cap = p_cap['diagnostics'][0]
    assert d_cap['code'] == 'W3001'
    assert d_cap['data']['kind'] == 'captured_snapshot_mutation'
    assert d_cap['data']['help']
    _validate_data(d_cap, 'ImmutableMutationData')

    dup = tmp_path / 'w1002_dup.wysna'
    dup.write_text(
        'fn run() {\n'
        '    let alpha = 1\n'
        '    let alpha = 2\n'
        '}\n',
        encoding='utf-8',
    )
    p_dup = _run_check_cli(toolchain, dup)
    _assert_matches_protocol(p_dup, SCHEMA_DOC)
    d_dup = p_dup['diagnostics'][0]
    assert d_dup['code'] == 'W1002'
    assert d_dup['data']['kind'] == 'duplicate_declaration'
    assert d_dup['data']['name'] == 'alpha'
    assert d_dup['related'] and d_dup['related'][0]['line'] == 2
    _validate_data(d_dup, 'DuplicateDeclarationData')

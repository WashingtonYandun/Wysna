"""Validate wysna.tool-result/1 JSON Schema and structured W2001/W2005 facts across Seed, Selfhost, and MCP."""
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tests.unit.test_phase5_native_agent_autonomy import McpLineClient
from tooling.McpServer import McpServer

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / 'schemas/wysna-tool-result.v1.schema.json'
SEED = [sys.executable, str(ROOT / 'main.py')]


@pytest.fixture(scope='module')
def schema_doc():
    return json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


@pytest.fixture(params=['seed', 'selfhost'])
def toolchain(request):
    if request.param == 'seed':
        return SEED
    return [ensure_selfhost_compiler_binary(str(ROOT))]


def _assert_matches_protocol(payload: dict, schema_doc: dict):
    try:
        jsonschema = importlib.import_module('jsonschema')
        validator_cls = getattr(jsonschema, 'Draft202012Validator', None)
        if validator_cls is not None:
            validator_cls.check_schema(schema_doc)
            validator_cls(schema_doc).validate(payload)
    except ImportError:
        pass

    for key in schema_doc['required']:
        assert key in payload, f'missing envelope key {key}: {payload}'
    assert payload['schema_version'] == 'wysna.tool-result/1'
    assert isinstance(payload['diagnostics'], list)
    for diag in payload['diagnostics']:
        for key in schema_doc['$defs']['Diagnostic']['required']:
            assert key in diag, f'missing diagnostic key {key}: {diag}'
        assert re.match(r'^W[0-9]{4}$', diag['code'])
        assert diag['severity'] in ('error', 'warning')
        if diag['primary_span'] is not None:
            for skey in schema_doc['$defs']['SourceSpan']['required']:
                assert skey in diag['primary_span']
            assert re.match(r'^[0-9a-f]{64}$', diag['primary_span']['source_sha256'])
        assert isinstance(diag['related'], list)
        for rel in diag['related']:
            for rkey in schema_doc['$defs']['RelatedLocation']['required']:
                assert rkey in rel
        assert isinstance(diag['data'], dict)
        assert isinstance(diag['fixes'], list)


def _run_check_cli(cli, path: Path):
    res = subprocess.run(
        cli + ['check', str(path), '--output-format=json-v1'],
        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=90,
    )
    assert res.returncode == 1, (res.stdout, res.stderr)
    return json.loads(res.stdout)


def _run_check_mcp(cli, root: Path, filename: str):
    if cli == SEED:
        raw_text = McpServer(str(root)).handle({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'wysna_check', 'arguments': {'path': filename}},
        })['result']['content'][0]['text']
        return json.loads(raw_text)
    with subprocess.Popen(
        cli + ['mcp', '--root', str(root)],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as proc:
        try:
            client = McpLineClient(proc)
            assert 'serverInfo' in client.request('initialize', {})['result']
            _, payload = client.call_tool('wysna_check', {'path': filename})
            return payload
        finally:
            proc.kill()
            proc.wait(timeout=10)


def test_published_json_schema_is_valid(schema_doc):
    assert schema_doc['$schema'] == 'https://json-schema.org/draft/2020-12/schema'
    assert 'Diagnostic' in schema_doc['$defs']
    assert 'TypeMismatchData' in schema_doc['$defs']
    assert 'ArityMismatchData' in schema_doc['$defs']
    assert 'NotCallableData' in schema_doc['$defs']
    assert 'InspectResult' in schema_doc['$defs']
    assert 'WorkspacePatchResult' in schema_doc['$defs']
    try:
        jsonschema = importlib.import_module('jsonschema')
        jsonschema.Draft202012Validator.check_schema(schema_doc)
    except ImportError:
        pass


def test_w2001_call_argument_mismatch_exposes_checker_facts_and_related(toolchain, tmp_path, schema_doc):
    helper = tmp_path / 'math_ops.wysna'
    helper.write_text(
        'pub struct Label { text: str }\n'
        'pub fn scale(factor: int, tag: Label) -> int {\n'
        '    return factor\n'
        '}\n',
        encoding='utf-8',
    )
    entry = tmp_path / 'main.wysna'
    entry.write_text(
        'from "math_ops.wysna" use { Label, scale }\n'
        'fn run() -> int {\n'
        '    return scale("bad", Label { text: "ok" })\n'
        '}\n',
        encoding='utf-8',
    )
    for runner in (lambda: _run_check_cli(toolchain, entry), lambda: _run_check_mcp(toolchain, tmp_path, 'main.wysna')):
        payload = runner()
        _assert_matches_protocol(payload, schema_doc)
        assert payload['status'] == 'diagnostic_error'
        diag = payload['diagnostics'][0]
        assert diag['code'] == 'W2001'
        data = diag['data']
        assert data['kind'] == 'type_mismatch'
        assert data['context'] == 'argument'
        assert data['expected'] == 'int'
        assert data['found'] == 'str'
        assert data['argument_index'] == 1
        assert data['parameter_name'] == 'factor'
        assert data['callee'] == 'scale'
        assert '__wysna_module_' not in data['callee']
        assert diag['related'], f'expected declaration location in related: {diag}'
        rel = diag['related'][0]
        assert rel['line'] == 2
        assert rel['span'] is not None
        assert Path(rel['span']['source_id']).resolve() == helper.resolve()


def test_w2005_call_arity_and_not_callable_expose_structured_data(toolchain, tmp_path, schema_doc):
    arity_file = tmp_path / 'arity.wysna'
    arity_file.write_text(
        'fn combine(left: int, right: int) -> int {\n'
        '    return left + right\n'
        '}\n'
        'fn bad() -> int {\n'
        '    return combine(1)\n'
        '}\n',
        encoding='utf-8',
    )
    for runner in (lambda: _run_check_cli(toolchain, arity_file), lambda: _run_check_mcp(toolchain, tmp_path, 'arity.wysna')):
        payload = runner()
        _assert_matches_protocol(payload, schema_doc)
        diag = payload['diagnostics'][0]
        assert diag['code'] == 'W2005'
        data = diag['data']
        assert data['kind'] == 'arity_mismatch'
        assert data['expected_arity'] == 2
        assert data['found_arity'] == 1
        assert data['callee'] == 'combine'
        assert diag['related'] and diag['related'][0]['line'] == 1

    non_call = tmp_path / 'non_callable.wysna'
    non_call.write_text(
        'fn bad() -> int {\n'
        '    let count: int = 5\n'
        '    return count(1)\n'
        '}\n',
        encoding='utf-8',
    )
    for runner in (lambda: _run_check_cli(toolchain, non_call), lambda: _run_check_mcp(toolchain, tmp_path, 'non_callable.wysna')):
        payload = runner()
        _assert_matches_protocol(payload, schema_doc)
        diag = payload['diagnostics'][0]
        assert diag['code'] == 'W2005'
        assert diag['data']['kind'] == 'not_callable'
        assert diag['data']['found'] == 'int'


def test_imported_nominal_mismatch_has_no_mangled_leak(toolchain, tmp_path, schema_doc):
    mod = tmp_path / 'models.wysna'
    mod.write_text('pub struct Record { id: int }\n', encoding='utf-8')
    entry = tmp_path / 'entry.wysna'
    entry.write_text(
        'from "models.wysna" use Record\n'
        'fn consume(item: Record) -> int { return item.id }\n'
        'fn main() { print(consume("wrong")) }\n',
        encoding='utf-8',
    )
    payload = _run_check_cli(toolchain, entry)
    _assert_matches_protocol(payload, schema_doc)
    diag = payload['diagnostics'][0]
    assert diag['code'] == 'W2001'
    data = diag['data']
    assert data['kind'] == 'type_mismatch'
    assert data['argument_index'] == 1
    assert data['parameter_name'] == 'item'
    assert data['callee'] == 'consume'
    assert '__wysna_module_' not in data['expected']
    assert '__wysna_module_' not in data['found']
    assert 'Record' in data['expected']

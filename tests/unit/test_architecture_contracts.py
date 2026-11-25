"""Check architecture evidence and policy at the Seed and native agent entry points."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from tests.unit.test_agent_protocol_schema import _assert_matches_protocol, schema_doc
from tests.unit.test_workspace_patch import SEED, _call_mcp, _run_cli, toolchain


ROOT = Path(__file__).resolve().parents[2]
RULE = 'controller-no-repository'


@pytest.fixture
def architecture_project(tmp_path):
    root = tmp_path / 'architecture'
    shutil.copytree(ROOT / 'examples/architecture', root)
    return root


def _json(cli, *args):
    result = _run_cli(cli, *args, '--output-format=json-v1')
    assert result.stdout and not result.stderr, (result.stdout, result.stderr)
    return result.returncode, json.loads(result.stdout)


def _check(cli, root):
    engine = ['--engine=seed'] if cli == SEED else []
    return _json(cli, 'project', 'check', '--manifest', str(root / 'wysna.toml'), *engine)


def _source(root):
    return root / 'src/main.wysna'


def _direct_call(source):
    return source.replace('from .service use InvoiceService',
                          'from .repository use InvoiceRepository').replace('InvoiceService()', 'InvoiceRepository()')


def _match_call(source):
    return _direct_call(source).replace(
        'return service.total()', 'return match true { true => service.total(), false => 0 }')


def _direct_type(source):
    return ('from .repository use InvoiceRepository\n' + source).replace(
        '    pub fn total()', '    pub fn dependency(value: InvoiceRepository) -> int { return 0 }\n\n    pub fn total()')


def _indirect(source):
    return source.replace('        return service.total()',
                          '        let callback: fn() -> int = || -> int { 7 }\n        return callback()')


def test_graph_carries_imports_calls_and_alias_metadata(toolchain, architecture_project):
    root = architecture_project
    rc, result = _json(toolchain, 'graph', str(_source(root)))
    assert rc == 0, result
    graph = result['result']
    nodes = {n['id']: n for n in graph['nodes']}
    modules = {Path(m['file']).name: m for m in graph['modules']}
    assert modules['service.wysna']['id'] in modules['main.wysna']['dependencies']
    assert modules['repository.wysna']['id'] in modules['service.wysna']['dependencies']
    assert any(
        edge['kind'] == 'calls' and Path(edge['file']).name == 'service.wysna'
        and nodes[edge['from']]['qualified_name'].endswith('InvoiceService.total')
        and nodes[edge['to']]['qualified_name'].endswith('InvoiceRepository.total')
        for edge in graph['edges']
    )
    rc, inspected = _json(toolchain, 'inspect', str(_source(root)), '--symbol', 'InvoiceController')
    assert rc == 0, inspected
    attr, = inspected['result']['node']['attributes']
    assert attr['name'] == 'Layer'
    assert attr['arguments'] == {'role': 'Controller'}
    assert Path(attr['schema']['file']).name == 'architecture.wysna'
    assert attr['schema']['name'] == 'Architecture'
    rc, selected = _json(toolchain, 'graph', '--manifest', str(root / 'wysna.toml'), '--role', 'Repository')
    assert rc == 0, selected
    repository, = selected['result']['nodes']
    assert repository['name'] == 'InvoiceRepository' and repository['architecture_role'] == 'Repository'
    failed, mcp = _call_mcp(toolchain, root, 'wysna_graph', {'manifest': 'wysna.toml', 'role': 'Repository'})
    assert not failed and selected['result'] == mcp['result']


@pytest.mark.parametrize(('edit', 'code', 'status', 'relation', 'boundary'), [
    (None, None, 'verified', None, None),
    (_direct_call, 'W6101', 'violation', 'calls', None),
    (_match_call, 'W6101', 'violation', 'calls', None),
    (_direct_type, 'W6101', 'violation', 'uses_type', None),
    (_indirect, 'W6102', 'incomplete', 'calls', 'indirect_call'),
])
def test_project_check_and_mcp_agree(toolchain, architecture_project, schema_doc,
                                     edit, code, status, relation, boundary):
    root = architecture_project
    src = _source(root)
    if edit:
        src.write_text(edit(src.read_text(encoding='utf-8')), encoding='utf-8')
    rc, payload = _check(toolchain, root)
    _assert_matches_protocol(payload, schema_doc)
    report = payload['result']['architecture']
    assert report['status'] == status
    assert report['rules'] == [{'id': RULE, 'status': status}]
    assert rc == int(bool(code))
    failed, mcp = _call_mcp(toolchain, root, 'wysna_project_check', {'manifest': 'wysna.toml'})
    assert not failed and mcp['result'] == payload['result']
    assert mcp['diagnostics'] == payload['diagnostics']
    if code:
        diag, = payload['diagnostics']
        assert diag['code'] == code and diag['stage'] == 'architecture'
        assert diag['primary_span']['source_sha256'] == hashlib.sha256(src.read_bytes()).hexdigest()
        facts = diag['data']
        assert facts['rule_id'] == RULE and facts['relation'] == relation and facts['boundary'] == boundary
        assert facts['from_symbol'].startswith('src/main.wysna::InvoiceController.')
        assert facts['revision'] == report['revision'] and facts['graph_hash'] == report['graph_hash']
        if code == 'W6101':
            assert facts['to_symbol'].startswith('src/repository.wysna::InvoiceRepository')
            assert facts['to_role'] == 'Repository'
            assert diag['related'][0]['span'] is not None


def test_workspace_patch_uses_candidate_source_and_rejects_stale_manifest(toolchain, architecture_project):
    root = architecture_project
    src = _source(root)
    service = root / 'src/service.wysna'
    manifest = root / 'wysna.toml'
    originals = {file: file.read_bytes() for file in (src, service)}
    updated_service = service.read_text(encoding='utf-8').replace('repository.total()', 'repository.total() + 1')
    invalid = _direct_call(src.read_text(encoding='utf-8'))
    edits = [
        {'path': 'src/main.wysna', 'source': invalid},
        {'path': 'src/service.wysna', 'source': updated_service},
    ]
    request = root / 'edits.json'
    request.write_text(json.dumps(edits), encoding='utf-8')
    args = ['patch', '--manifest', str(manifest), '--edits', str(request)]
    rc, result = _json(toolchain, *args)
    assert rc == 1 and result['diagnostics'][0]['code'] == 'W6101'
    assert result['diagnostics'][0]['primary_span']['source_sha256'] == hashlib.sha256(invalid.encode()).hexdigest()
    failed, mcp = _call_mcp(toolchain, root, 'wysna_patch', {'manifest': 'wysna.toml', 'edits': edits})
    assert not failed and mcp['diagnostics'] == result['diagnostics']
    assert all(file.read_bytes() == before for file, before in originals.items())

    edits[0]['source'] = src.read_text(encoding='utf-8').replace('return service.total()', 'return service.total() + 1')
    request.write_text(json.dumps(edits), encoding='utf-8')
    rc, good = _json(toolchain, *args)
    assert rc == 0 and not good['result']['written'], good
    original_manifest = manifest.read_bytes()
    manifest.write_bytes(original_manifest + b'\n# Invalidate preview\n')
    rc, stale = _json(toolchain, *args, '--expect-plan-hash', good['result']['plan_hash'], '--apply')
    assert rc != 0 and stale['diagnostics'][0]['code'] == 'W5005'
    assert all(file.read_bytes() == before for file, before in originals.items())
    manifest.write_bytes(original_manifest)
    rc, applied = _json(toolchain, *args, '--expect-plan-hash', good['result']['plan_hash'], '--apply')
    assert rc == 0 and applied['result']['written'], applied
    assert _check(toolchain, root)[0] == 0


@pytest.mark.parametrize(('before', 'after'), [
    ('deny_roles = ["Repository"]', 'deny_roles = ["Unknown"]'),
    ('relations = ["calls", "uses_type"]', 'relations = ["transitive"]'),
    ('severity = "error"', 'severity = "warning"'),
])
def test_manifest_rejects_invalid_rule(toolchain, architecture_project, before, after):
    root = architecture_project
    manifest = root / 'wysna.toml'
    manifest.write_text(manifest.read_text(encoding='utf-8').replace(before, after), encoding='utf-8')
    rc, result = _check(toolchain, root)
    assert rc == 2 and result['diagnostics'][0]['code'] == 'W5002', result


@pytest.mark.parametrize(('from_text', 'to_text', 'boundary'), [
    ('Layer(role: "Controller")', 'Layer(role: "Unexpected")', 'unknown_role'),
    ('src/architecture.wysna::Architecture', 'src/architecture.wysna::Missing', 'missing_role_schema'),
])
def test_invalid_classification_is_incomplete(toolchain, architecture_project, from_text, to_text, boundary):
    root = architecture_project
    file = _source(root) if boundary == 'unknown_role' else root / 'wysna.toml'
    file.write_text(file.read_text(encoding='utf-8').replace(from_text, to_text), encoding='utf-8')
    rc, result = _check(toolchain, root)
    assert rc == 1, result
    assert result['result']['architecture']['status'] == 'incomplete'
    assert result['diagnostics'][0]['code'] == 'W6102'
    assert result['diagnostics'][0]['data']['boundary'] == boundary

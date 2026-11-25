"""Export the types a checked tool actually accepts and returns."""
import json
from pathlib import Path
import subprocess

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tooling.SchemaCLI import generate_schemas

ROOT = Path(__file__).resolve().parents[2]


def test_imported_tool_contract_uses_resolved_type_identity(tmp_path):
    (tmp_path / 'request.wysna').write_text('pub struct Batch { count: int }\n', encoding='utf-8')
    (tmp_path / 'response.wysna').write_text('pub struct Batch { code: str }\n', encoding='utf-8')
    (tmp_path / 'errors.wysna').write_text('pub enum Failure { Invalid }\n', encoding='utf-8')
    source = tmp_path / 'main.wysna'
    source.write_text('from .request use Batch as Request\n'
                      'from .response use Batch as Response\n'
                      'from .errors use Failure\n'
                      'pub fn summarize(batch: Request) -> Response ! Failure {\n'
                      '    if batch.count < 1 { return Err(Failure.Invalid) }\n'
                      '    return Ok(Response { code: "ok" })\n'
                      '}\n', encoding='utf-8')
    seed = generate_schemas(source)
    engine = ensure_selfhost_compiler_binary(str(ROOT))
    completed = subprocess.run([engine, 'schema', str(source), '--output-format=json-v1'],
                               capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    native = json.loads(completed.stdout)['result']
    assert seed['$defs'] == native['$defs']
    for field in ('input_schema', 'output_schema', 'error_schema'):
        assert seed['tools'][0][field] == native['tools'][0][field]
    tool = native['tools'][0]
    request = tool['input_schema']['properties']['batch']['$ref'].split('/')[-1]
    output = tool['output_schema']['$ref'].split('/')[-1]
    error = tool['error_schema']['$ref'].split('/')[-1]
    assert native['$defs'][request]['properties'] == {'count': {'type': 'integer'}}
    assert native['$defs'][output]['properties'] == {'code': {'type': 'string'}}
    assert native['$defs'][error]['enum'] == ['Invalid']

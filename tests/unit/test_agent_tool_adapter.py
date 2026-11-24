"""Exercise exported typed tool adapters with both execution engines."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from common.Platform import compiler_command, executable_name, native_stack_link_args
from compiler.Bootstrap import ensure_selfhost_compiler_binary

ROOT = Path(__file__).resolve().parents[2]
SEED = [sys.executable, str(ROOT / 'main.py')]


def invoke(command, *, timeout=120):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', timeout=timeout)


@pytest.fixture(params=['seed', 'selfhost'])
def toolchain(request):
    if request.param == 'seed':
        return SEED, False
    engine = ensure_selfhost_compiler_binary(str(ROOT))
    return [engine, '--stdlib-root', str(ROOT / 'stdlib')], True


def test_agent_tool_adapter_validates_input_and_serializes_typed_outcomes(toolchain, tmp_path):
    cli, native = toolchain
    (tmp_path / 'payload.wysna').write_text(
        'pub struct Batch { count: int }\npub enum Failure { Invalid }\n', encoding='utf-8')
    tool = tmp_path / 'tool.wysna'
    tool.write_text(
        'from .payload use {Batch, Failure}\n'
        'struct AgentTool { id: str, version: int }\n'
        '@AgentTool(id: "test.summarize", version: 1)\n'
        'pub fn summarize(batch: Batch) -> Batch ! Failure {\n'
        '    if batch.count < 1 { return Err(Failure.Invalid) }\n'
        '    Ok(Batch { count: batch.count + 1 })\n'
        '}\n', encoding='utf-8')
    adapter = tmp_path / 'adapter.wysna'
    export = cli + ['schema', str(tool), '--function', 'summarize', '--emit-tool',
                    '-o', str(adapter), '--output-format=json-v1']
    result = invoke(export)
    assert result.returncode == 0, result.stderr + result.stdout
    artifact = json.loads(result.stdout)['result']['agent_adapter']
    assert artifact == {'id': 'test.summarize', 'version': 1,
                        'source': adapter.read_text(encoding='utf-8')}
    assert invoke(export).returncode != 0
    assert adapter.read_text(encoding='utf-8') == artifact['source']

    if native:
        emitted = tmp_path / 'adapter.c'
        binary = tmp_path / executable_name('adapter')
        generated = invoke(cli + ['--emit-c-mir', str(adapter), str(emitted),
                                 '--sandbox', '--allow=arguments,stdio'])
        assert generated.returncode == 0, generated.stderr + generated.stdout
        cc = compiler_command()
        math_flags = [] if os.name == 'nt' else ['-lm']
        compiled = invoke(cc + ['-std=c99', '-O0', '-I' + str(ROOT), str(emitted),
                                *native_stack_link_args(cc), *math_flags, '-o', str(binary)])
        assert compiled.returncode == 0, compiled.stderr
        run = [str(binary)]
    else:
        run = cli + ['run', '--allow=arguments,stdio', str(adapter), '--']
    for data, status, expected in [
        ('{"batch":{"count":1}}', 'ok', {'count': 2}),
        ('{"batch":{"count":0}}', 'error', 'Invalid'),
        ('{"batch":{"count":1,"extra":5}}', 'invalid_input', None),
        ('{"batch":{"count":"1"}}', 'invalid_input', None),
        ('{"bad":1}', 'invalid_input', None),
        ('{"batch":', 'invalid_input', None),
    ]:
        executed = invoke(run + [data])
        assert executed.returncode == 0, executed.stderr + executed.stdout
        envelope = json.loads(executed.stdout)
        value = envelope.pop('value')
        assert envelope == {'schema_version': 'wysna.agent-tool-result/1',
                            'id': 'test.summarize', 'version': 1, 'status': status}
        if status == 'invalid_input':
            assert isinstance(value, str) and value
        else:
            assert value == expected


@pytest.mark.parametrize('initializer', [False, True], ids=['function', 'module'])
def test_agent_tool_adapter_refuses_effects(toolchain, tmp_path, initializer):
    cli, _ = toolchain
    tool = tmp_path / 'tool.wysna'
    body = 'return value' if initializer else 'print(value)\n    return value'
    trailing = 'print("module initializer")\n' if initializer else ''
    tool.write_text(
        'struct AgentTool { id: str, version: int }\n'
        '@AgentTool(id: "test.unsafe", version: 1)\n'
        f'pub fn unsafe(value: int) -> int {{\n    {body}\n}}\n' + trailing,
        encoding='utf-8')
    result = invoke(cli + ['schema', str(tool), '--function', 'unsafe', '--emit-tool',
                           '--output-format=json-v1'])
    assert result.returncode != 0
    assert json.loads(result.stdout)['diagnostics'][0]['code'] == ('W5006' if initializer else 'W2013')

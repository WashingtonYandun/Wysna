"""Keep machine-readable target support aligned with its implementation metadata."""

import json

from project.Targets import TARGET_SPECS
from tooling.ApiIndex import run_capabilities_command


def test_capabilities_report_complete_seed_target_contracts(capsys):
    assert run_capabilities_command(['--output-format=json-v1']) == 0
    output = capsys.readouterr()
    assert output.err == ''
    result = json.loads(output.out)

    assert result['schema_version'] == 'wysna.tool-result/1'
    assert result['command'] == 'capabilities'
    assert result['status'] == 'ok'
    assert result['result']['schema_version'] == 'wysna.capabilities/1'
    assert result['result']['runtimes'] == {
        'wasi-command': 'wasmtime-python==50.0.1',
        'browser-library': 'browser or Node.js',
    }
    profiles = {profile['profile']: profile for profile in result['result']['target_profiles']}
    assert set(profiles) == set(TARGET_SPECS)
    assert result['result']['capabilities'] == {
        name: list(spec.capabilities) for name, spec in TARGET_SPECS.items()
    }

    for name, spec in TARGET_SPECS.items():
        profile = profiles[name]
        assert profile == {
            'profile': spec.profile,
            'engine': spec.engine,
            'backend': spec.backend,
            'triple': spec.triple,
            'artifact_kind': spec.artifact_kind.value,
            'capabilities': list(spec.capabilities),
            'build_requirements': [
                {'name': requirement.name, 'version': requirement.version,
                 'provided_by': requirement.provided_by}
                for requirement in spec.build_requirements
            ],
            'run_requirements': [
                {'name': requirement.name, 'version': requirement.version,
                 'provided_by': requirement.provided_by}
                for requirement in spec.run_requirements
            ],
        }
        assert profile['engine'] == 'wysna-seed'
        assert profile['backend'] == 'c99'


def test_ai_guide_names_machine_contracts_and_native_sdk_limitations():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    guide = (root / 'docs/AI_AGENT_GUIDE.md').read_text(encoding='utf-8')
    assert 'wysna capabilities --output-format=json-v1' in guide
    assert 'wysna project check' in guide and 'wysna project build' in guide
    assert 'primary_span' in guide and 'start_byte' in guide and 'end_byte' in guide
    assert 'native-core-v1' in guide
    assert 'has not been measured' in guide.lower()

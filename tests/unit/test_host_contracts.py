"""The host ABI, effect catalog and borrowed path buffers must stay consistent."""

import inspect
from pathlib import Path
import re

import pytest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from compiler.CompilerService import CompilerService
from compiler.EffectAnalysis import analyze_effects
from compiler.EffectModel import HOST_EFFECTS
from error.Error import WysnaError
from runtime.HostAbi import FUNCTIONS
from tests.engine_harness import ENGINES, run_engine
from tooling.CapabilityPolicy import enforce_capability_policy

ROOT = Path(__file__).resolve().parents[2]


def test_every_host_binding_has_an_effect_contract_and_matching_c_arity():
    assert set(HOST_EFFECTS) == set(FUNCTIONS)
    headers = '\n'.join(path.read_text() for path in (ROOT / 'runtime').glob('wysna_host*.h'))
    for name, function in FUNCTIONS.items():
        definitions = re.findall(r'\b' + re.escape(name) + r'\s*\(([^)]*)\)\s*\{', headers)
        arities = {0 if arguments.strip() in ('', 'void') else len(arguments.split(','))
                   for arguments in definitions}
        parameters = [parameter for parameter in inspect.signature(function).parameters.values()
                      if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)]
        assert arities == {len(parameters)}, name


@pytest.mark.parametrize('name', ['wysna_host_active_handles', 'wysna_host_active_bytes'])
def test_telemetry_reads_host_state_without_external_capabilities(name):
    service = CompilerService()
    checked = service.check(f'extern "C" fn {name}() -> int\nprint({name}())\n')
    module = service.lower_mir(checked)
    trusted = {target: signature['native'] for target, signature in module.externs.items()}
    report = analyze_effects(module, checked._interner, service.source_texts(), trusted)
    assert report['program']['complete_within_model']
    assert report['program']['capabilities'] == ['stdio']
    assert 'resource.inspect' in report['program']['transitive_effects']
    assert 'host-state.read' in report['program']['transitive_effects']
    enforce_capability_policy(report, 'allow', {'stdio'}, '<host-test>')
    enforce_capability_policy(report, 'deny', {'filesystem.read'}, '<host-test>')
    with pytest.raises(WysnaError, match='stdio'):
        enforce_capability_policy(report, 'deny', {'stdio'}, '<host-test>')
    untrusted, bindings = service.explain_with_foreign_bindings(checked)
    assert bindings
    assert not untrusted['program']['complete_within_model']
    with pytest.raises(WysnaError, match='untrusted foreign'):
        enforce_capability_policy(untrusted, 'allow', {'stdio'}, '<host-test>',
                                  untrusted_bindings=bindings)


def test_canonical_path_preserves_filesystem_reads_and_untrusted_boundaries():
    source = '''extern "C" fn wysna_host_fs_canonical(path: opaque, output: opaque) -> int
fn resolve(path: opaque, output: opaque) -> int { wysna_host_fs_canonical(path, output) }
'''
    service = CompilerService()
    checked = service.check(source)
    module = service.lower_mir(checked)
    trusted = {target: signature['native'] for target, signature in module.externs.items()}
    report = analyze_effects(module, checked._interner, service.source_texts(), trusted)
    row = next(row for row in report['functions'] if row['name'] == 'resolve')
    assert 'filesystem.read' in row['transitive_effects']
    assert 'filesystem.write' not in row['transitive_effects']
    assert 'host-state.write' in row['transitive_effects']
    assert 'filesystem.read' in row['capabilities']
    assert any(witness['target'] == 'wysna_fn_wysna_host_fs_canonical'
               and 'filesystem.read' in witness['effects'] for witness in row['witnesses'])
    assert not any('no trusted effect contract' in witness.get('reason', '')
                   for witness in row['unknown_calls'])
    untrusted, bindings = service.explain_with_foreign_bindings(checked)
    assert bindings
    with pytest.raises(WysnaError, match='untrusted foreign'):
        enforce_capability_policy(untrusted, 'deny', {'filesystem.read'}, '<host-test>',
                                  untrusted_bindings=bindings)


@pytest.mark.parametrize('engine', ENGINES)
def test_canonical_path_borrows_both_buffers_and_releases_cleanly(engine, tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    source = (ROOT / 'tests/fixtures/language_combinations/borrowed_paths.wysna').read_text()
    result = run_engine(engine, source, 'host_borrowed_paths', compiler, tmp_path)
    assert result.exit_code == 0, result.stderr
    assert not result.infrastructure_error, result.infrastructure_error
    assert result.stdout == 'borrowed paths released\n'

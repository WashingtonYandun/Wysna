"""Validate conservative explanations against resolved source and hostile boundaries."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from compiler.CompilerService import CompilerService
from compiler.EffectAnalysis import analyze_effects
from compiler.EffectModel import OPERATION_EFFECTS, RUNTIME_EFFECTS
from error.Error import WysnaError
from mir.Nodes import EFFECTS
from mir.Verifier import RUNTIME
from tooling.ExplainCLI import run_explain_command
from project.Resolver import ProjectResolver


class EffectAnalysisTests(unittest.TestCase):
    def report(self, source):
        service = CompilerService()
        checked = service.check(source, '<effects>')
        return service.explain(checked)

    def test_recursion_and_transitive_effects_reach_a_fixed_point(self):
        report = self.report('fn first(n: int) { if n > 0 { second(n-1) } }\n'
                             'fn second(n: int) { print(n) first(n) }\nfirst(2)')
        self.assertEqual(report['program']['capabilities'], ['stdio'])
        self.assertTrue(report['program']['complete_within_model'])
        functions = {row['name']: row for row in report['functions']}
        for name in ('first', 'second'):
            self.assertIn('stdio.write', functions[name]['transitive_effects'])
        self.assertNotIn('stdio.write', functions['first']['direct_effects'])
        self.assertEqual(report['schema_version'], 'wysna.effect-report/1')

    def test_external_functions_are_never_loaded_or_certified_by_name(self):
        source = 'extern "C" fn wysna_host_file_read(file: opaque, data: opaque, count: int) -> int\n'
        source += 'fn foreign(file: opaque, data: opaque) -> int { return wysna_host_file_read(file, data, 1) }'
        with patch('ctypes.CDLL', side_effect=AssertionError('FFI must not load')), patch('subprocess.run', side_effect=AssertionError('No tool execution')):
            report = self.report(source)
        row = next(row for row in report['functions'] if row['name'] == 'foreign')
        self.assertFalse(row['complete_within_model'])
        self.assertEqual(row['capabilities'], ['native-ffi', 'unknown'])
        self.assertEqual(row['unknown_calls'][0]['trust'], 'unknown')

    def test_stdlib_host_contracts_have_explicit_trust_and_provenance(self):
        report = self.report('import "std/files" as files\nprint("not executed")')
        calls = [witness for row in report['functions'] for witness in row['witnesses']
                 if witness['trust'] == 'bundled-host-contract']
        self.assertTrue(calls)
        self.assertTrue(any('filesystem.read' in witness['effects'] for witness in calls))
        self.assertTrue(all(witness['source']['file'].endswith('.wysna') for witness in calls))
        # Importing unused file operations does not make them entry-point calls.
        self.assertEqual(report['program']['capabilities'], ['stdio'])
        self.assertTrue(any(path.endswith('files.wysna') for path in report['source_inputs']))

    def test_indirect_uncertainty_propagates_to_callers(self):
        report = self.report('fn apply(f: fn(int) -> int) -> int { return f(1) }\n'
                             'print(apply(|x: int| => x+1))')
        self.assertFalse(report['program']['complete_within_model'])
        self.assertIn('unknown', report['program']['capabilities'])
        self.assertTrue(report['program']['unresolved_functions'])
        self.assertTrue(any('indirect' in witness['reason'] for row in report['functions'] for witness in row['unknown_calls']))

    def test_missing_contract_is_unknown_and_new_operations_require_review(self):
        self.assertEqual(set(RUNTIME_EFFECTS), RUNTIME)
        self.assertEqual(set(OPERATION_EFFECTS) | {'runtime', 'call', 'virtual', 'indirect'}, set(EFFECTS))
        with patch('compiler.EffectAnalysis.RUNTIME_EFFECTS', {}):
            report = self.report('fn output() { print(1) }\noutput()')
        self.assertFalse(report['program']['complete_within_model'])
        self.assertIn('unknown', report['program']['transitive_effects'])

    def test_stale_results_and_invalid_mir_are_rejected(self):
        service = CompilerService()
        first = service.check('print(1)', '<first>')
        service.check('print(2)', '<second>')
        with self.assertRaises(WysnaError):
            service.explain(first)
        checked = service.check('print(3)', '<third>')
        module = service.lower_mir(checked)
        module.functions[-1].blocks[0].operations[0].result = 999
        with self.assertRaises(WysnaError):
            analyze_effects(module, checked._interner, service.source_texts())

    def test_json_cli_has_single_document_and_no_source_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.wysna'
            source.write_text('print("NEVER_EXECUTE_THIS")\n')
            output = io.StringIO()
            with redirect_stdout(output):
                code = run_explain_command([str(source), '--output-format=json-v1'])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result['command'], 'explain')
            self.assertEqual(result['status'], 'ok')
            self.assertNotIn('NEVER_EXECUTE_THIS', output.getvalue())
            source.write_bytes(b'\xff')
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(run_explain_command([str(source), '--output-format=json-v1']), 1)
            result = json.loads(output.getvalue())
            self.assertEqual(result['status'], 'diagnostic_error')
            self.assertEqual(result['diagnostics'][0]['code'], 'W1001')

    def test_resource_finalizers_do_not_receive_purity_or_completeness_claims(self):
        report = self.report('import "std/files"\nfn cleanup(value: files.File) -> unit { let owned = value }\n')
        cleanup = next(row for row in report['functions'] if row['name'] == 'cleanup')
        self.assertIn('resource.finalize', cleanup['transitive_effects'])
        self.assertFalse(cleanup['complete_within_model'])
        self.assertTrue(any('finalizer' in witness['reason'] for witness in cleanup['unknown_calls']))

    def test_function_selection_errors_and_filesystem_failures_are_structured(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.wysna'
            source.write_text('fn output() { print(1) }\noutput()\n')
            for arguments, status, code in (
                ([str(source),'--function','output'], 'ok',0),
                ([str(source),'--function','missing'], 'usage_error',2),
                ([str(source),'--frozen'], 'usage_error',2),
                ([str(source.parent/'missing.wysna')], 'toolchain_error',3),
            ):
                with self.subTest(arguments=arguments):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(run_explain_command(arguments+['--output-format=json-v1']),code)
                    result = json.loads(output.getvalue())
                    self.assertEqual(result['status'],status)
                    if code == 0:
                        self.assertTrue(result['result']['selected_function'].endswith('output'))

    def test_frozen_project_checks_lock_and_source_containment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root/'src').mkdir()
            source = root/'src/main.wysna'
            source.write_text('print(1)\n')
            manifest = root/'wysna.toml'
            manifest.write_text('manifest_version = 1\n[package]\nname = "effects-test"\nversion = "0.1.0"\n'
                'language = "0.5"\n[toolchain]\ncompiler = "0.5.0"\nstdlib = "bundled"\n'
                '[sources]\nroot = "src"\n[targets.app]\nentry = "src/main.wysna"\nprofile = "native-cli"\n')
            ProjectResolver.load(manifest).write_lock()
            output = io.StringIO()
            arguments = [str(source),'--manifest',str(manifest),'--frozen','--output-format=json-v1']
            with redirect_stdout(output):
                self.assertEqual(run_explain_command(arguments),0)
            self.assertIn('project',json.loads(output.getvalue())['result'])
            source.write_text('print(2)\n')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run_explain_command(arguments),0)
            manifest.write_text(manifest.read_text().replace('0.1.0', '0.1.1'))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run_explain_command(arguments),2)
            ProjectResolver.load(manifest).write_lock()
            outside = root/'outside.wysna'
            outside.write_text('print(3)\n')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run_explain_command([str(outside),*arguments[1:]]),2)


if __name__ == '__main__':
    unittest.main()

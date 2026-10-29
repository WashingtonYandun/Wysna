"""Compositional nominal/constructor, literal complexity and evidence contracts."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch

from main import ensure_selfhost_compiler_binary
from common.Platform import compiler_command, executable_name
from tests.engine_harness import run_engine, compare
from tools.ci_conformance import source_fingerprint, generate_markdown_matrix

ROOT = Path(__file__).resolve().parents[2]


def test_nominal_equality_and_undeclared_super_constructor_are_rejected():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    programs = [
        'struct A { x: int }\nstruct B { x: int }\nprint(A { x: 1 } == B { x: 1 })',
        'enum A { X }\nenum B { X }\nprint(A.X != B.X)',
        'struct A { x: int }\nstruct B { x: int }\nprint([A { x: 1 }] == [B { x: 1 }])',
        'enum A { X }\nenum B { X }\nprint((A.X, true) == (B.X, true))',
        'class A {}\nclass B extends A { fn init() { super.init() } }\nlet value = B()',
    ]
    with tempfile.TemporaryDirectory(prefix='wysna-nominal-contracts-') as temporary:
        directory = Path(temporary)
        module = directory / 'nominals.wysna'
        module.write_text('pub struct A { x: int }\npub enum E { X }\npub fn make() -> A { A { x: 1 } }\npub fn event() -> E { E.X }\n', encoding='utf-8')
        programs += [f'import {json.dumps(module.as_posix())}\nstruct B {{ x: int }}\nprint(nominals.make() == B {{ x: 1 }})',
                     f'import {json.dumps(module.as_posix())}\nenum E {{ X }}\nprint(nominals.event() == E.X)']
        for index, source in enumerate(programs):
            expected = 'W3002' if index == 4 else 'W2001'
            for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
                result = run_engine(engine, source, 'nominal', selfhost_bin, directory / str(index))
                assert result.exit_code == 1 and result.diag_code == expected, vars(result)
                assert not result.infrastructure_error, vars(result)


def test_literal_indexing_scales_linearly_by_work_not_wall_clock():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    metrics = {engine: [] for engine in ('ast', 'hir', 'selfhost')}
    with tempfile.TemporaryDirectory(prefix='wysna-string-scaling-') as temporary:
        directory = Path(temporary)
        for count in (128, 256, 512):
            text = 'abé\0' * count
            quoted = json.dumps(text, ensure_ascii=False).replace('\\u0000', '\\0')
            source = ('fn sum(text: str) -> int { let mut total = 0\n'
                      'for index in 0..len(text) { total = total + ord(text[index]) }\nreturn total }\n'
                      f'let literal = {quoted}\nlet owned = join_strings([literal], "")\n'
                      'print(sum(literal), sum(owned), len(literal), len(owned))\n')
            expected_sum = sum(ord(character) for character in text)
            expected = f'{expected_sum} {expected_sum} {len(text)} {len(text)}\n'
            source_path = directory / 'source.wysna'
            source_path.write_text(source, encoding='utf-8')
            interpreted = run_engine('interpreter', source, 'literal', selfhost_bin, directory / 'reference')
            assert interpreted.stdout == expected and interpreted.exit_code == 0, vars(interpreted)
            for engine in metrics:
                emitted = directory / (engine + '.c')
                if engine == 'selfhost':
                    command = [selfhost_bin, str(source_path), str(emitted)]
                elif engine == 'ast':
                    command = [sys.executable, str(ROOT / 'tests/emit_ast_oracle.py'),
                               str(source_path), str(emitted)]
                else:
                    command = [sys.executable, str(ROOT / 'main.py'), 'emit-c',
                               str(source_path), '-o', str(emitted)]
                built = subprocess.run(command, capture_output=True, text=True, timeout=120)
                assert built.returncode == 0, built.stderr
                code = emitted.read_text(encoding='utf-8').replace('    wysna_runtime_cleanup();',
                    '    fprintf(stderr, "scan=%" PRIu64 "\\n", wysna_g_string_scan_steps);\n    wysna_runtime_cleanup();')
                emitted.write_text(code, encoding='utf-8')
                binary = directory / executable_name(engine)
                flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if (os.environ.get('WYSNA_SANITIZE') == '1' and os.name != 'nt') else []
                subprocess.run(compiler_command() + ['-std=c99', '-O2', '-DW_STRING_METRICS', '-I' + str(ROOT),
                               str(emitted), '-o', str(binary)] + flags, check=True, capture_output=True, timeout=120)
                ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                    env=dict(os.environ, WYSNA_MEM_CHECK='1', ASAN_OPTIONS='detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0'), UBSAN_OPTIONS='halt_on_error=1'))
                assert ran.returncode == 0 and ran.stdout == expected, (engine, ran)
                match = re.search(r'scan=(\d+)', ran.stderr)
                assert match, ran.stderr
                steps = int(match.group(1))
                assert steps <= 12 * len(text) + 100, (engine, count, steps)
                metrics[engine].append(steps)
        for engine, steps in metrics.items():
            assert steps[1] <= steps[0] * 2 + 100 and steps[2] <= steps[1] * 2 + 100, (engine, steps)


def test_matrix_invalidates_changes_without_renaming_inputs():
    current = source_fingerprint()
    from tests.engine_harness import corpus_paths
    labels = [path.relative_to(ROOT).as_posix() for group in corpus_paths() for path in group]
    measured = {'provenance': current, 'corpus': labels,
                'coverage': {engine: {'passed': len(labels)} for engine in ('interpreter', 'ast', 'hir', 'selfhost')}}
    assert 'STALE' not in generate_markdown_matrix(measured)
    for altered in (dict(current, sha='changed-commit'),
                    dict(current, inputs={**current['inputs'], 'runtime/wysna_runtime.h': 'changed-bytes'})):
        with patch('tools.ci_conformance.source_fingerprint', return_value=altered):
            output = generate_markdown_matrix(measured)
            assert 'STALE' in output and '100.0%' not in output and '✅' not in output
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / 'stdlib').mkdir()
        path = root / 'stdlib/library.wysna'
        path.write_text('pub fn value() -> int { 1 }', encoding='utf-8')
        before = source_fingerprint(root)
        path.write_text('pub fn value() -> int { 2 }', encoding='utf-8')
        after = source_fingerprint(root)
        assert before['inputs'].keys() == after['inputs'].keys()
        assert before['digest'] != after['digest']
        (root / 'README.md').write_text('Public contract', encoding='utf-8')
        documented = source_fingerprint(root)
        assert 'README.md' in documented['inputs']
        (root / 'README.md').write_text('Changed contract', encoding='utf-8')
        assert documented['digest'] != source_fingerprint(root)['digest']


def test_lsp_uri_and_definition_on_native_platform():
    from tooling.LanguageServer import LanguageServer, source_path
    with tempfile.TemporaryDirectory(prefix='w lsp é ') as temporary:
        path = Path(temporary).resolve() / 'module.wysna'
        uri = path.as_uri()
        assert os.path.normcase(source_path(uri)) == os.path.normcase(str(path))
        messages = []
        server = LanguageServer(messages.append)
        server.update(uri, 'let value = "é"\nprint(value)', 1)
        location = server.definition(uri, {'line': 1, 'character': 7})
        assert location and location['uri'].lower() == uri.lower(), location
        assert location['range']['start'] == {'line': 0, 'character': 4}


def run_findings_tests():
    test_nominal_equality_and_undeclared_super_constructor_are_rejected()
    test_literal_indexing_scales_linearly_by_work_not_wall_clock()
    test_matrix_invalidates_changes_without_renaming_inputs()
    test_lsp_uri_and_definition_on_native_platform()
    return True


if __name__ == '__main__':
    run_findings_tests()

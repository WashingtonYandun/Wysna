"""Block 4C: pattern semantics and diagnostics agree across all four engines."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from compiler.CompilerService import CompilerService
from tests.engine_harness import ENGINES, run_engine

ROOT = Path(__file__).resolve().parents[2]


class MatchExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = Path(ensure_selfhost_compiler_binary(str(ROOT)))

    def check_engines(self, source, output=None, code=None):
        with tempfile.TemporaryDirectory(prefix='wysna-match-') as directory:
            for engine in ENGINES:
                with self.subTest(engine=engine):
                    result = run_engine(engine, source, 'match-extensions', self.compiler, Path(directory))
                    if code:
                        self.assertFalse(result.compile_ok, vars(result))
                        self.assertEqual(result.diag_code, code, vars(result))
                    else:
                        self.assertTrue(result.compile_ok and result.runtime_ok, vars(result))
                        self.assertEqual(result.exit_code, 0, vars(result))
                        self.assertEqual(result.stdout, output, vars(result))

    def test_or_patterns_preserve_bindings_and_nested_payloads(self):
        self.check_engines('''enum Value { Text(str), Other(str), Empty }
fn text(value: Value) -> str {
    match value { Value.Text(name) | Value.Other(name) => name, Value.Empty => "empty" }
}
fn nested(value: (int?, int)) -> int {
    match value { (Some(1 | 2), n) => n, (None, _) => -1, _ => 0 }
}
print(text(Value.Other("payload")), nested((Some(2), 9)), nested((None, 2)))
''', 'payload 9 -1\n')

    def test_missing_pattern_families_are_structured_for_agents(self):
        cases = [
            ('enum E { A, B(int), C }\nfn f(x:E)->int{match x{A=>0}}', ['B(_)', 'C']),
            ('fn f(x:bool)->int{match x{true=>1}}', ['false']),
            ('fn f(x:int?)->int{match x{Some(1 | 2)=>1}}', ['Some(_)', 'None']),
            ('fn f(x:bool)->int{match x{true if false=>1,false=>0}}', ['true']),
            ('fn f(x:(bool,bool))->int{match x{(true,_)=>1}}', ['(_, _)']),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.wysna'
            for source, families in cases:
                path.write_text(source + '\n')
                for command in ([sys.executable, str(ROOT / 'main.py')], [str(self.compiler)]):
                    result = subprocess.run([*command, 'check', str(path), '--output-format=json-v1'],
                        cwd=ROOT, capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    diagnostic = json.loads(result.stdout)['diagnostics'][0]
                    self.assertEqual(diagnostic['code'], 'W2003', diagnostic)
                    self.assertEqual(diagnostic['data']['uncovered_pattern_families'], sorted(families), diagnostic)

    def test_struct_patterns_support_rest_nested_fields_and_generics(self):
        self.check_engines('''struct Box[T] { value: T, label: str }
struct Point { x: int, y: int }
fn score(box: Box[Point]) -> int {
    match box {
        Box[Point] { value: Point { x: -2..=2, y }, .. } => y,
        _ => 0,
    }
}
print(score(Box[Point] { value: Point { x: 2, y: 42 }, label: "ok" }))
''', '42\n')

    def test_or_bindings_follow_the_selected_payload_positions(self):
        self.check_engines('enum Pair { A(int, int), B(int, int) }\n'
            'fn f(pair: Pair) -> int { match pair { Pair.A(x, y) | Pair.B(y, x) => x * 10 + y } }\n'
            'print(f(Pair.A(1, 2)), f(Pair.B(1, 2)))\n', '12 21\n')

    def test_imported_generic_struct_pattern_identity(self):
        from tests.fuzz.multifile_fuzzer import MultiFileCase, run_multifile_case
        case = MultiFileCase(seed=4103, files={
            'types.wysna': 'pub struct Point { x: int, y: int }\npub struct Box[T] { value: T }\n',
            'main.wysna': 'import "types.wysna" as t\n'
                'fn f(box: t.Box[t.Point]) -> int { match box { t.Box[t.Point] { value: t.Point { x: 1 | 2, y } } => y, _ => 0 } }\n'
                'print(f(t.Box[t.Point] { value: t.Point { x: 2, y: 42 } }))\n'}, entry='main.wysna')
        with tempfile.TemporaryDirectory() as directory:
            for result in run_multifile_case(case, self.compiler, Path(directory)):
                self.assertTrue(result.compile_ok and result.runtime_ok, vars(result))
                self.assertEqual(result.stdout, '42\n', vars(result))

    def test_generic_template_struct_pattern_is_qualified_in_dependency(self):
        from tests.fuzz.multifile_fuzzer import MultiFileCase, run_multifile_case
        case = MultiFileCase(seed=4903, files={
            'types.wysna': 'pub struct Box[T] { value:T }\n'
                'pub fn extract[T](box:Box[T])->T{match box{Box {value}=>value}}\n',
            'main.wysna': 'import "types.wysna" as t\nprint(t.extract(t.Box[int]{value:42}))\n'}, entry='main.wysna')
        with tempfile.TemporaryDirectory() as directory:
            for result in run_multifile_case(case, self.compiler, Path(directory)):
                self.assertTrue(result.compile_ok and result.runtime_ok, vars(result))
                self.assertEqual(result.stdout, '42\n', vars(result))

    def test_dependency_pattern_bindings_shadow_globals(self):
        from tests.fuzz.multifile_fuzzer import MultiFileCase, run_multifile_case
        case = MultiFileCase(seed=4908, files={
            'types.wysna': 'let value=99\npub struct Box {value:int}\n'
                'pub enum E {A(int),B(int)}\n'
                'pub fn extract(box:Box)->int{match box{Box {value}=>value}}\n'
                'pub fn alternative(x:E)->int{match x{E.A(value)|E.B(value)=>value}}\n',
            'main.wysna': 'import "types.wysna" as t\n'
                'print(t.extract(t.Box{value:42}),t.alternative(t.E.B(7)))\n'}, entry='main.wysna')
        with tempfile.TemporaryDirectory() as directory:
            for result in run_multifile_case(case, self.compiler, Path(directory)):
                self.assertTrue(result.compile_ok and result.runtime_ok, vars(result))
                self.assertEqual(result.stdout, '42 7\n', vars(result))

    def test_range_boundaries_and_complete_int64_coverage(self):
        self.check_engines('''fn classify(x: int) -> int {
    match x {
        -9223372036854775808..0 => 1,
        0..=9223372036854775807 => 2,
    }
}
print(classify(-9223372036854775808), classify(-1), classify(0), classify(9223372036854775807))
''', '1 1 2 2\n')

    def test_or_guard_and_subject_are_evaluated_once(self):
        self.check_engines('''class Counter {
    pub count: int
    fn init() { this.count = 0 }
    fn subject() -> int { this.count += 1; return 1 }
    fn guard() -> bool { this.count += 1; return false }
}
let counter = Counter()
let result = match counter.subject() { 1 | 1 if counter.guard() => 7, _ => 9 }
print(result, counter.count)
''', '9 2\n')

    def test_invalid_patterns_have_stable_diagnostics(self):
        cases = [
            ('fn f(x: int?) -> int { match x { Some(n) | None => 1, _ => 0 } }', 'W2001'),
            ('fn f(x: str) -> int { match x { 1..=2 => 1, _ => 0 } }', 'W2001'),
            ('fn f(x: int) -> int { match x { 3..3 => 1, _ => 0 } }', 'W2001'),
            ('fn f(x: int) -> int { match x { 3..=1 => 1, _ => 0 } }', 'W2001'),
            ('fn f(x: int) -> int { match x { 0..=9223372036854775808 => 1, _ => 0 } }', 'W0002'),
            ('struct P { x: int, y: int }\nfn f(p: P) -> int { match p { P { x } => x } }', 'W2001'),
            ('struct P { x: int }\nfn f(p: P) -> int { match p { P { missing, .. } => 1 } }', 'W2001'),
            ('struct P { x: int }\nfn f(p: P) -> int { match p { P { x, x } => 1 } }', 'W1002'),
            ('fn f(x: bool) -> int { match x { true | false if false => 1 } }', 'W2003'),
        ]
        for source, code in cases:
            with self.subTest(source=source):
                self.check_engines(source + '\n', code=code)

    def test_unguarded_struct_pattern_can_be_exhaustive(self):
        self.check_engines('struct P { x: int }\nfn f(p: P) -> int { match p { P { x } => x } }\nprint(f(P { x: 42 }))\n', '42\n')

    def test_unreachable_arm_lints_keep_guards_conservative(self):
        source = """fn f(x: int) -> int {
    match x { 0..=4 => 1, 5..=10 => 2, 2 | 7 => 3, _ => 4 }
}
fn g(x: int) -> int {
    match x { _ if false => 0, 2 => 1, _ => 2 }
}
"""
        service = CompilerService()
        checked = service.check(source, '<match-lints>')
        warnings = [warning for warning in service.lint_warnings(checked) if warning.code == 'W6003']
        self.assertEqual(len(warnings), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lint.wysna'
            path.write_text(source)
            for command in ([sys.executable, str(ROOT / 'main.py')], [str(self.compiler)]):
                result = subprocess.run([*command, 'check', str(path), '--output-format=json-v1'],
                                        cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                warnings = [item for item in payload['diagnostics'] if item['code'] == 'W6003']
                self.assertEqual(len(warnings), 1, payload)
                self.assertEqual(warnings[0]['severity'], 'warning')

    def test_formatter_preserves_new_pattern_syntax(self):
        from formatter.Formatter import Formatter
        from parser.Parser import Parser
        source = 'struct P { x: int, y: int }\nfn f(p: P) -> int { match p { P { x: 1 | 2, y: -3..=3 } => 1, _ => 0 } }\n'
        formatted = Formatter().format(Parser().parse(source))
        self.assertIn('1 | 2', formatted)
        self.assertIn('-3..=3', formatted)
        CompilerService().check(formatted, '<formatted>')


if __name__ == '__main__':
    unittest.main()

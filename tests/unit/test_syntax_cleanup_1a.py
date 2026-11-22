"""Block 4A (Syntax Cleanup 1A): canonical constructors, None, _ => wildcard, and parenthesized match tuples."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tests.engine_harness import ENGINES, compare, run_engine


def _selfhost_compiler() -> Path:
    return Path(ensure_selfhost_compiler_binary(str(ROOT)))


class TestSyntaxCleanup1A(unittest.TestCase):
    def _assert_all_4_engines(self, source: str, expected_stdout: str):
        sh = _selfhost_compiler()
        with tempfile.TemporaryDirectory(prefix="wysna-4a-") as td:
            results = [run_engine(eng, source, "block4a", sh, Path(td)) for eng in ENGINES]
            ok, reason = compare(results)
            self.assertTrue(ok, reason)
            for r in results:
                self.assertTrue(r.compile_ok and r.runtime_ok and r.exit_code == 0, str(r))
                self.assertEqual(r.stdout, expected_stdout, str(r))

    def _assert_rejected_seed_and_selfhost(self, source: str, expected_code: str, expected_hint: str):
        with tempfile.TemporaryDirectory(prefix="wysna-4a-neg-") as td:
            src = Path(td) / "bad.wysna"
            out_c = Path(td) / "bad.c"
            src.write_text(source, encoding="utf-8")
            seed = subprocess.run(
                [sys.executable, str(ROOT / "src/Main.py"), "check", str(src)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertNotEqual(seed.returncode, 0, seed.stdout + seed.stderr)
            self.assertIn(expected_code, seed.stderr)
            self.assertIn(expected_hint, seed.stderr.lower())

            sh = subprocess.run(
                [str(_selfhost_compiler()), str(src), str(out_c)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertNotEqual(sh.returncode, 0, sh.stdout + sh.stderr)
            self.assertIn(expected_code, sh.stderr)
            self.assertIn(expected_hint, sh.stderr.lower())

    def test_new_is_free_identifier_and_canonical_construction_works_across_4_engines(self):
        source = (
            "class Counter {\n"
            "    public value: int\n"
            "    fn init(v: int) { this.value = v }\n"
            "    fn new(delta: int) -> int { return this.value + delta }\n"
            "}\n"
            "fn new(x: int) -> int { return x * 2 }\n"
            "fn main() {\n"
            "    let new = 5\n"
            "    let c = Counter(10)\n"
            "    print(new, c.new(3))\n"
            "}\n"
            "main()\n"
        )
        self._assert_all_4_engines(source, "5 13\n")

    def test_canonical_none_wildcard_parenthesized_tuple_and_for_destructuring_across_4_engines(self):
        source = (
            "fn describe(opt: int?, pair: (int, int)) -> int {\n"
            "    let base = match opt {\n"
            "        Some(v) => v,\n"
            "        None => 10,\n"
            "    }\n"
            "    let bonus = match pair {\n"
            "        (1, y) => y,\n"
            "        _ => 0,\n"
            "    }\n"
            "    let counts = {\"a\": 2, \"b\": 3}\n"
            "    let mut total = base + bonus\n"
            "    for k, v in counts {\n"
            "        if len(k) > 0 { total += v }\n"
            "    }\n"
            "    return total\n"
            "}\n"
            "fn main() {\n"
            "    let empty: int? = None\n"
            "    print(describe(empty, (1, 7)), describe(Some(4), (2, 9)))\n"
            "}\n"
            "main()\n"
        )
        self._assert_all_4_engines(source, "22 9\n")

    def test_removed_new_keyword_rejected_with_migration_hint(self):
        source = (
            "class Box { public v: int\nfn init(v: int) { this.v = v } }\n"
            "fn main() { let b = new Box(1); print(b.v) }\n"
        )
        self._assert_rejected_seed_and_selfhost(source, "W0006", "removed")

    def test_removed_star_and_else_wildcards_rejected_with_migration_hint(self):
        star = "fn f(x: int) -> int { match x { 1 => 10, * => 0 } }\n"
        els = "fn f(x: int) -> int { match x { 1 => 10, else => 0 } }\n"
        self._assert_rejected_seed_and_selfhost(star, "W0001", "_ =>")
        self._assert_rejected_seed_and_selfhost(els, "W0001", "_ =>")

    def test_removed_lowercase_none_expr_and_pattern_rejected(self):
        expr = "fn main() { let x: int? = none }\n"
        pat = "fn f(x: int?) -> int { match x { Some(v) => v, none => 0 } }\n"
        self._assert_rejected_seed_and_selfhost(expr, "W1001", "none")
        self._assert_rejected_seed_and_selfhost(pat, "W0001", "none")

    def test_removed_bare_tuple_pattern_in_match_rejected(self):
        bare = "fn f(p: (int, int)) -> int { match p { a, b => a + b } }\n"
        self._assert_rejected_seed_and_selfhost(bare, "W0001", "(a, b)")


if __name__ == "__main__":
    unittest.main()

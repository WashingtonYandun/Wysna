"""End-to-end regression suite for v0.8.0 language ergonomics & Selfhost parity."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from compiler.CompilerService import CompilerService
from compiler.LlvmToolchain import LlvmToolchain
from compiler.NativeToolchain import NativeToolchain
from error.Error import WysnaError
from formatter.Formatter import format_source
from main import ensure_selfhost_compiler_binary
from tests.engine_harness import ENGINES, run_engine
from tests.formatter_contract import verify_formatting
from tooling.FixCLI import apply_fix_to_file, fix_source


ROOT = Path(__file__).resolve().parents[2]
HAS_LLVM = (
    importlib.util.find_spec("llvmlite") is not None
    or bool(os.environ.get("WYSNA_LLVM_SDK"))
)


def _run_llvm(source: str, workdir: Path, name: str = "prog.wysna") -> subprocess.CompletedProcess:
    service = CompilerService(project_root=ROOT)
    checked = service.check(source, file=str(workdir / name))
    emission = service.emit_llvm(checked)
    toolchain = LlvmToolchain()
    toolchain.verify(emission.source)
    build = toolchain.build(emission, workdir / "llvm_out")
    return subprocess.run([build.output_path], cwd=ROOT, capture_output=True, text=True, timeout=30)


def _run_selfhost_cli(
    selfhost_bin: str,
    src_path: Path,
    workdir: Path,
) -> subprocess.CompletedProcess:
    c_path = workdir / "out.c"
    bin_path = workdir / "out_bin"
    compiled = subprocess.run(
        [selfhost_bin, str(src_path), str(c_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if compiled.returncode != 0:
        return compiled
    NativeToolchain(ROOT).build(c_path.read_text(encoding="utf-8"), bin_path)
    return subprocess.run([str(bin_path)], cwd=workdir, capture_output=True, text=True, timeout=30)


class LanguageErgonomicsV08Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
        cls.service = CompilerService(project_root=ROOT)

    def _assert_all_engines(self, source: str, expected_stdout: str, stem: str,
                            *, status: int = 0, diagnostic: str = "") -> None:
        with tempfile.TemporaryDirectory(prefix=f"wysna-{stem}-") as tmpdir:
            root_tmp = Path(tmpdir)
            for engine in ENGINES:
                res = run_engine(engine, source, stem, selfhost_bin=self.selfhost_bin, directory=root_tmp / engine)
                self.assertEqual(res.exit_code, status, f"{engine} failed: {res.stderr}")
                self.assertEqual(res.stdout, expected_stdout, f"{engine} stdout mismatch")
                if diagnostic:
                    self.assertIn(diagnostic, res.stderr)
            if HAS_LLVM:
                llvm_res = _run_llvm(source, root_tmp / "llvm", name=f"{stem}.wysna")
                self.assertEqual(llvm_res.returncode, status, f"llvm failed: {llvm_res.stderr}")
                self.assertEqual(llvm_res.stdout, expected_stdout, "llvm stdout mismatch")
                if diagnostic:
                    self.assertIn(diagnostic, llvm_res.stderr)

    def test_process_entrypoint_status_and_diverging_handler(self):
        cases = [
            ("implicit", "fn main() -> int { 255 }", 255, "", ""),
            ("explicit", "fn main() -> int { 7 }\nmain()", 7, "", ""),
            ("ordinary-call", "fn main() -> int { 7 }\nprint(main())", 0, "7\n", ""),
            ("result", "fn main() -> int ! str { Ok(9) }", 9, "", ""),
            ("invalid", "fn main() -> int { 256 }", 1, "", "W4003"),
            ("negative", "fn main() -> int { -1 }", 1, "", "W4003"),
            ("exiting", """
fn main() -> int {
    let absent: int? = none
    let value = absent else { exit(42) }
    return value
}
""", 42, "", ""),
        ]
        for name, source, status, stdout, diagnostic in cases:
            with self.subTest(name=name):
                self._assert_all_engines(source + "\n", stdout, name, status=status, diagnostic=diagnostic)

    def test_qualified_and_selectively_aliased_enum_patterns(self):
        with tempfile.TemporaryDirectory(prefix="wysna-pattern-owners-") as temporary:
            root = Path(temporary).resolve()
            entry = root / "main.wysna"
            module = '''pub enum State { Ready(int), Idle }
pub enum Choice[T] { Chosen(T), Absent }
fn helper(value: int) -> int { value + 1 }
pub fn apply[T](value: T, transform: fn(T) -> int) -> int {
    return helper(transform(value))
}
'''
            (root / "status.wysna").write_text(module, encoding="utf-8")
            source = '''
import "status.wysna" as status
from "status.wysna" use State as Other
from "status.wysna" use apply as transform
fn classify(state: status.State) -> int {
    return match state {
        status.State.Ready(number) => number,
        Other.Idle => 0,
    }
}
fn double(value: int) -> int { value * 2 }
print(classify(status.State.Ready(12)), classify(status.State.Idle))
print(transform(5, double), status.apply[int](6, double))
let chosen = status.Choice.Chosen(6)
let absent: status.Choice[int] = status.Choice.Absent
let absent_again: status.Choice[int] = status.Choice.Absent
print(chosen == status.Choice.Chosen(6), absent == absent_again)
'''
            entry.write_text(source, encoding="utf-8")
            for engine in ENGINES:
                target = root / engine
                target.mkdir()
                (target / "status.wysna").write_text(module, encoding="utf-8")
                result = run_engine(engine, source, "qualified", self.selfhost_bin, root)
                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertEqual(result.stdout, "12 0\n11 13\ntrue true\n")
            invalid = source.replace("Other.Idle =>", "status.Unknown.Idle =>")
            entry.write_text(invalid, encoding="utf-8")
            with self.assertRaises(WysnaError):
                self.service.check_file(entry)
            rejected = _run_selfhost_cli(self.selfhost_bin, entry, root)
            self.assertNotEqual(rejected.returncode, 0)

    def test_map_error_preserves_success_and_calls_converter_for_failure(self):
        source = '''
import "std/result" as result_ops
from "std/result" use map_error
fn input(valid: bool) -> int ! str {
    if valid { return Ok(7) }
    return Err("bad")
}
fn convert(error: str) -> int {
    print("converting")
    return len(error)
}
let success = map_error(input(true), convert) else error { error }
let failure = result_ops.map_error(input(false), convert) else error { error }
print(success, failure)
'''
        self._assert_all_engines(source, "converting\n7 3\n", "map-error")

    def test_statement_match_discards_values_but_executes_effects(self):
        source = '''
enum State { Idle, Ready(int) }
fn process(state: State) -> int {
    let mut count = 0
    match state {
        State.Ready(value) => { count += value },
        State.Idle => {},
    }
    match count {
        7 => print("matched"),
        8 => count + 99,
    }
    (match count { _ => count }) + 1
    match count { _ => count } + 1
    return count
}
print(process(State.Ready(7)), process(State.Idle), process(State.Ready(8)))
fn fail() -> unit ! str { Err("tail error") }
fn tail(flag: bool) -> unit ! str {
    match flag { true => fail(), false => Ok(print()) }
}
tail(true) else error { print(error) }
fn recover() -> int {
    let failed: int ! str = Err("recover")
    failed else error { (match (if true { 0 } else { 3 }) { 0 => 6, _ => 7 }) }
}
print(recover())
'''
        self._assert_all_engines(source, "matched\n7 0 8\ntail error\n6\n", "discard-match")
        invalid = "let n = match true { true => 1, false => \"no\" }\n"
        with self.assertRaises(WysnaError):
            self.service.check(invalid, "<discard-match>")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.wysna"
            path.write_text(invalid, encoding="utf-8")
            rejected = subprocess.run([self.selfhost_bin, "--check", str(path)],
                                      capture_output=True, text=True, timeout=60)
            self.assertIn("W2001", rejected.stderr)

    def test_json_access_errors_include_field_and_actual_type(self):
        source = '''
import "std/json"
let value = json.object({"status_code": json.from_str("201"), "dot.key": json.from_int(7)})
match json.get_int(value, "status_code") { Err(issue) => print(issue.message), _ => {} }
match json.get_optional_bool(value, "dot.key") { Err(issue) => print(issue.message), _ => {} }
match json.get_str(value, "missing") { Err(issue) => print(issue.message), _ => {} }
match json.to_int(json.Value.Number("9223372036854775808")) { Err(issue) => print(issue.message), _ => {} }
'''
        expected = (
            'status_code: expected JSON integer, found JSON string\n'
            '["dot.key"]: expected JSON boolean, found JSON number\n'
            'missing: missing required JSON field\n'
            'integer overflow\n'
        )
        self._assert_all_engines(source, expected, "json-field-errors")

    def test_b1_bidirectional_empty_collection_inference(self):
        source = '''
struct Bag {
    items: list[int],
    tags: dict[str, int],
}

class Repo {
    public ids: dict[str, int]
    fn init() {
        this.ids = {}
    }
    public fn set_empty() -> unit {
        this.ids = {}
    }
}

fn maybe_nums(ok: bool) -> list[int]? {
    if ok {
        return Some([7])
    }
    return none
}

fn pick_branch(mode: int) -> list[int] {
    if mode == 1 {
        return [1]
    }
    let chosen = if mode == 2 {
        [10]
    } elif mode == 3 {
        [20]
    } else {
        []
    }
    return chosen
}

fn match_list(code: int) -> list[int] {
    let from_expected: list[int] = match code {
        1 => [],
        _ => [99],
    }
    let from_arm = match code {
        1 => [42],
        _ => [],
    }
    return [len(from_expected), len(from_arm)]
}

fn main() -> unit {
    let r = new Repo()
    r.ids["k"] = 5
    r.set_empty()

    let mut matrix: list[list[int]] = [[1, 2], [3]]
    matrix[0] = []

    let bag = Bag { items: [], tags: {} }
    let pair: tuple[list[int], dict[str, int]] = ([], {})
    let p_sum = match pair {
        (p_list, p_dict) => len(p_list) + len(p_dict),
    }
    let recovered = maybe_nums(false) else { [] }
    let b = pick_branch(4)
    let m = match_list(1)
    let from_outer_if: list[int] = if false { [3] } else { [] }

    print(
        len(r.ids),
        len(matrix[0]),
        len(bag.items),
        len(bag.tags),
        p_sum,
        len(recovered),
        len(b),
        m[0],
        m[1],
        len(from_outer_if),
    )
}
main()
'''
        self._assert_all_engines(source, "0 0 0 0 0 0 0 0 1 0\n", "b1_empty_infer")

    def test_b2_compound_assignments_and_w3001_autofix(self):
        source = '''
struct Stat {
    score: int,
}

class Meter {
    public value: int
    fn init(start: int) {
        this.value = start
    }
    public fn bump(delta: int) -> unit {
        this.value += delta
        this.value -= 1
    }
}

fn main() -> unit {
    let mut n = 10
    n += 5
    n -= 3
    n *= 2

    let mut s = "wy"
    s += "sna"

    let mut xs = [1, 2, 3]
    xs[1] += 10
    xs[2] *= 4

    let mut d = {"hits": 3}
    d["hits"] += 4

    let mut st = Stat { score: 5 }
    st.score += 7
    st.score -= 2

    let m = new Meter(20)
    m.bump(6)

    print(n, s, xs[1], xs[2], d["hits"], st.score, m.value)
}
main()
'''
        self._assert_all_engines(source, "24 wysna 12 12 7 10 25\n", "b2_compound")

        bad = "let x = 1\nx += 2\nprint(x)\n"
        with tempfile.TemporaryDirectory(prefix="wysna-b2-fix-") as tmpdir:
            tmp = Path(tmpdir)
            seed_file = tmp / "seed_fix.wysna"
            seed_file.write_text(bad, encoding="utf-8")
            result, _ = apply_fix_to_file(str(seed_file), apply=True)
            self.assertTrue(result["changed"])
            self.assertEqual(seed_file.read_text(encoding="utf-8"), "let mut x = 1\nx += 2\nprint(x)\n")

            sh_file = tmp / "selfhost_fix.wysna"
            sh_file.write_text(bad, encoding="utf-8")
            sh_fix = subprocess.run(
                [self.selfhost_bin, "fix", "--apply", "--output-format=json-v1", str(sh_file)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(sh_fix.returncode, 0, sh_fix.stdout + sh_fix.stderr)
            self.assertEqual(sh_file.read_text(encoding="utf-8"), "let mut x = 1\nx += 2\nprint(x)\n")

    def test_b3_canonical_wildcard_formatting_and_w2001_str_autofix(self):
        unformatted_else = "fn f(x: int) -> int {\n    return match x {\n        1 => 10,\n        else => 0,\n    }\n}\n"
        formatted_else = format_source(unformatted_else, "match_else.wysna")
        self.assertIn("_ => 0", formatted_else)
        self.assertNotIn("else =>", formatted_else)
        self.assertEqual(format_source(formatted_else, "match_else.wysna"), formatted_else)

        canonical_under = "fn g(x: int) -> int {\n    return match x {\n        1 => 10,\n        _ => 0,\n    }\n}\n"
        self.assertEqual(format_source(canonical_under, "match_under.wysna"), canonical_under)

        bad_concat = '''
fn main() -> unit {
    let port = 8080
    let ok = true
    let s1 = "port=" + port
    let s2 = port + ":ok"
    let s3 = "ok=" + ok
    print(s1, s2, s3)
}
main()
'''
        with tempfile.TemporaryDirectory(prefix="wysna-b3-w2001-") as tmpdir:
            tmp = Path(tmpdir)
            seed_file = tmp / "seed_concat.wysna"
            seed_file.write_text(bad_concat, encoding="utf-8")
            result, _ = apply_fix_to_file(str(seed_file), apply=True)
            self.assertTrue(result["changed"])
            seed_fixed = seed_file.read_text(encoding="utf-8")
            self.assertIn('"port=" + str(port)', seed_fixed)
            self.assertIn('str(port) + ":ok"', seed_fixed)
            self.assertIn('"ok=" + str(ok)', seed_fixed)
            self.service.check(seed_fixed, file=str(seed_file))

            sh_file = tmp / "sh_concat.wysna"
            sh_file.write_text(bad_concat, encoding="utf-8")
            sh_fix = subprocess.run(
                [self.selfhost_bin, "fix", "--apply", "--output-format=json-v1", str(sh_file)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(sh_fix.returncode, 0, sh_fix.stdout + sh_fix.stderr)
            sh_fixed = sh_file.read_text(encoding="utf-8")
            self.assertIn('"port=" + str(port)', sh_fixed)
            self.assertIn('str(port) + ":ok"', sh_fixed)
            self.assertIn('"ok=" + str(ok)', sh_fixed)

    def test_c1_c2_hex_binary_multiline_and_fstrings(self):
        source = '''
fn add(a: int, b: int) -> int {
    return a + b
}

fn main() -> unit {
    let hx = 0xFF
    let hx2 = 0x10_2A
    let bn = 0b1010_0101
    let min_i = -0x8000000000000000
    let ml = """line1
"quoted"
line3"""
    let name = "Wysna"
    let plain = f"hello {{world}}"
    let interp = f"{name}:{add(hx, bn)}"
    let fml = f"""v={hx2}
neg={min_i < 0}
esc={{x}}"""
    print(hx, hx2, bn, min_i < 0, len(ml) > 10, plain, interp, fml)
}
main()
'''
        expected = "255 4138 165 true true hello {world} Wysna:420 v=4138\nneg=true\nesc={x}\n"
        self._assert_all_engines(source, expected, "c1_c2_literals")

        for bad_num in ("let a = 0x\n", "let b = 0xGG\n", "let c = 0b102\n", "let d = 123abc\n"):
            with self.assertRaises(WysnaError) as ctx:
                self.service.check(bad_num, file="bad_num.wysna")
            self.assertEqual(ctx.exception.code, "W0002")

            with tempfile.TemporaryDirectory(prefix="wysna-badnum-") as tmpdir:
                res = run_engine("selfhost", bad_num, "bad_num", selfhost_bin=self.selfhost_bin, directory=Path(tmpdir))
                self.assertNotEqual(res.exit_code, 0)
                self.assertIn("W0002", res.stderr)

    def test_c3_functional_struct_update_spread(self):
        fmt_src = "let next_user = User { ..base, active: false }\n"
        self.assertEqual(verify_formatting(fmt_src), fmt_src)

        source = '''
struct User {
    id: int,
    name: str,
    active: bool,
}

struct Box[T] {
    value: T,
    label: str,
}

fn main() -> unit {
    let base = User { id: 1, name: "washo", active: true }
    let updated = User { ..base, active: false }
    let b1 = Box[int] { value: 42, label: "v1" }
    let b2 = Box { ..b1, label: "v2" }
    print(base.active, updated.id, updated.name, updated.active, b2.value, b2.label)
}
main()
'''
        self._assert_all_engines(source, "true 1 washo false 42 v2\n", "c3_struct_spread")

        single_eval_src = '''
struct Pair3 {
    a: int,
    b: int,
    c: int,
}

let mut calls = 0

fn make_pair() -> Pair3 {
    calls += 1
    return Pair3 { a: 10, b: 20, c: 30 }
}

fn main() -> unit {
    let p = Pair3 { ..make_pair(), c: 99 }
    print(calls, p.a, p.b, p.c)
}
main()
'''
        with tempfile.TemporaryDirectory(prefix="wysna-spread-once-") as tmpdir:
            res = run_engine("selfhost", single_eval_src, "spread_once", selfhost_bin=self.selfhost_bin, directory=Path(tmpdir))
            self.assertEqual(res.exit_code, 0, res.stderr)
            self.assertEqual(res.stdout, "1 10 20 99\n")

    def test_c4_dict_items_and_direct_for_kv(self):
        source = '''
fn main() -> unit {
    let counts = {"a": 10, "b": 20}
    let pairs = counts.items()
    let mut sum_v = 0
    let mut joined = ""
    for k, v in counts {
        joined += k
        sum_v += v
    }
    let mut pair_sum = 0
    for pk, pv in pairs {
        if pk == "a" or pk == "b" {
            pair_sum += pv
        }
    }
    let first_pair = match pairs[0] {
        (k0, v0) => f"{k0}={v0}",
    }
    print(len(pairs), joined, sum_v, pair_sum, first_pair)
}
main()
'''
        self._assert_all_engines(source, "2 ab 30 30 a=10\n", "c4_dict_items")

    def test_c5_selfhost_from_use_dot_relative_and_submodule_parity(self):
        with tempfile.TemporaryDirectory(prefix="wysna-sh-use-") as tmpdir:
            root = Path(tmpdir)
            (root / "helper.wysna").write_text(
                "pub fn triple(x: int) -> int { return x * 3 }\n",
                encoding="utf-8",
            )
            domain_dir = root / "app" / "domain"
            domain_dir.mkdir(parents=True)
            (domain_dir / "user.wysna").write_text(
                "pub struct User { id: int }\npub fn get_id(u: User) -> int { return u.id }\npub fn calc(n: int) -> int { return n + 5 }\n",
                encoding="utf-8",
            )
            (domain_dir / "auth.wysna").write_text(
                "pub fn role_level() -> int { return 9 }\n",
                encoding="utf-8",
            )

            main_src = '''
from std.text use { contains, to_lower }
from .helper use triple
from app.domain use { user.User, user.get_id, auth.role_level }
from app.domain use user as user_ns

fn main() -> unit {
    let from = 2
    let use = 3
    let u = User { id: triple(4) }
    let low = to_lower("WYSOFIA")
    let has = contains(low, "sofia")
    print(has, get_id(u), role_level(), user_ns.calc(from + use))
}
main()
'''
            main_path = root / "main.wysna"
            main_path.write_text(main_src, encoding="utf-8")
            run_res = _run_selfhost_cli(self.selfhost_bin, main_path, root)
            self.assertEqual(run_res.returncode, 0, run_res.stderr)
            self.assertEqual(run_res.stdout, "true 12 9 10\n")

            # Also test single unbraced stdlib import
            single_dir = root / "s_out"
            single_dir.mkdir(exist_ok=True)
            single_path = root / "single_std.wysna"
            single_path.write_text(
                'from std.text use to_lower\nprint(to_lower("WYSNA"))\n',
                encoding="utf-8",
            )
            single_res = _run_selfhost_cli(self.selfhost_bin, single_path, single_dir)
            self.assertEqual(single_res.returncode, 0, single_res.stderr)
            self.assertEqual(single_res.stdout, "wysna\n")

    def test_w2_phase1_tuple_shorthand_indexing_and_destructuring(self):
        fmt_src = "fn split_pair(p: (int, str)) -> (int, (str, bool)) {\n    let (a, b) = p\n    return (a, (b, true))\n}\n"
        formatted = verify_formatting(fmt_src)
        self.assertIn("tuple[int, str]", formatted)
        self.assertIn("let (a, b) = p", formatted)

        source = '''
fn make_nested(n: int) -> (int, (str, bool)) {
    return (n + 1, ("wysna", true))
}

fn empty_tuple_len(u: ()) -> int {
    return len(u)
}

let (g_id, (g_tag, _)) = make_nested(9)

fn main() -> unit {
    let pair: (int, str) = (7, "ok")
    let nested: (int, (str, bool)) = make_nested(40)
    let pairs = [(1, "a"), (2, "b")]
    let mut (x, y): (int, int) = (10, 20)
    x += 5
    y *= 2
    let (a, (lbl, flag)) = nested
    print(
        g_id,
        g_tag,
        pair.0,
        pair.1,
        nested.1.0,
        nested.1.1,
        make_nested(3).0,
        pairs[1].1,
        x,
        y,
        a,
        lbl,
        flag,
        empty_tuple_len(()),
    )
}
main()
'''
        self._assert_all_engines(
            source,
            "10 wysna 7 ok wysna true 4 b 15 40 41 wysna true 0\n",
            "w2_tuple_ergonomics",
        )

        # Negative diagnostics in both Seed and Selfhost
        neg_cases = [
            ("let p = (1, 2)\nprint(p.2)\n", "W3002"),
            ("let (dup, dup) = (1, 2)\n", "W1002"),
            ("let (a, b, c) = (1, 2)\n", "W2001"),
        ]
        for bad_src, expected_code in neg_cases:
            with self.assertRaises(WysnaError) as ctx:
                self.service.check(bad_src, file="bad_tup.wysna")
            self.assertEqual(ctx.exception.code, expected_code)
            with tempfile.TemporaryDirectory(prefix="wysna-badtup-") as tmpdir:
                res = run_engine("selfhost", bad_src, "bad_tup", selfhost_bin=self.selfhost_bin, directory=Path(tmpdir))
                self.assertNotEqual(res.exit_code, 0)
                self.assertIn(expected_code, res.stderr)

    def test_w2_phase2_single_eval_struct_spread_all_engines(self):
        source = '''
struct Pair3 {
    a: int,
    b: int,
    c: int,
}

let mut calls = 0

fn make_pair() -> Pair3 {
    calls += 1
    return Pair3 { a: 10, b: 20, c: 30 }
}

fn main() -> unit {
    let p1 = Pair3 { ..make_pair(), c: 99 }
    let p2 = Pair3 { ..make_pair(), b: 55 }
    print(calls, p1.a, p1.b, p1.c, p2.a, p2.b, p2.c)
}
main()
'''
        self._assert_all_engines(source, "2 10 20 99 10 55 30\n", "w2_spread_single_eval")

    def test_w2_phase3_star_wildcard_match_parity(self):
        star_fmt = "fn classify(n: int) -> str {\n    return match n {\n        1 => \"one\",\n        * => \"other\",\n    }\n}\n"
        formatted = format_source(star_fmt, "star_wild.wysna")
        self.assertIn("_ => \"other\"", formatted)
        self.assertNotIn("* =>", formatted)
        self.assertEqual(format_source(formatted, "star_wild.wysna"), formatted)

        source = '''
fn classify(n: int) -> str {
    return match n {
        1 => "one",
        * => "other",
    }
}
fn main() -> unit {
    print(classify(1), classify(99))
}
main()
'''
        self._assert_all_engines(source, "one other\n", "w2_star_wildcard")

    def test_w2_phase4_contains_is_empty_and_in_operator(self):
        fmt_src = "let ok = x in xs and not (y in xs)\n"
        self.assertEqual(verify_formatting(fmt_src), fmt_src)

        source = '''
fn main() -> unit {
    let xs = [10, 20, 30]
    let empty_l: list[int] = []
    let names = ["ada", "grace"]
    let d = {"host": 8080, "tls": 443}
    let empty_d: dict[str, int] = {}
    let s = "hello wysna"
    let empty_s = ""

    print(
        xs.contains(20),
        20 in xs,
        not (99 in xs),
        xs.is_empty(),
        empty_l.is_empty(),
        "grace" in names,
        d.contains("host"),
        "tls" in d,
        "missing" in d,
        d.is_empty(),
        empty_d.is_empty(),
        s.contains("wysna"),
        "hello" in s,
        "rust" in s,
        "" in s,
        s.is_empty(),
        empty_s.is_empty(),
    )
}
main()
'''
        self._assert_all_engines(
            source,
            "true true true false true true true true false false true true true false true false true\n",
            "w2_contains_in",
        )

        for bad_in in ('let a = 42 in ["x"]\n', 'let b = 123 in "abc"\n'):
            with self.assertRaises(WysnaError) as ctx:
                self.service.check(bad_in, file="bad_in.wysna")
            self.assertEqual(ctx.exception.code, "W2001")
            with tempfile.TemporaryDirectory(prefix="wysna-badin-") as tmpdir:
                res = run_engine("selfhost", bad_in, "bad_in", selfhost_bin=self.selfhost_bin, directory=Path(tmpdir))
                self.assertNotEqual(res.exit_code, 0)
                self.assertIn("W2001", res.stderr)

    def test_w2_phase5_constructor_w3004_diagnostic_and_autofix(self):
        bad_cls = '''
class BankAccount {
    public owner: str

    fn BankAccount(owner: str) {
        this.owner = owner
    }
}

fn main() -> unit {
    let acc = BankAccount("Washo")
    print(acc.owner)
}
main()
'''
        with self.assertRaises(WysnaError) as ctx:
            self.service.check(bad_cls, file="bank.wysna")
        self.assertEqual(ctx.exception.code, "W3004")

        with tempfile.TemporaryDirectory(prefix="wysna-w3004-") as tmpdir:
            tmp = Path(tmpdir)
            seed_file = tmp / "seed_bank.wysna"
            seed_file.write_text(bad_cls, encoding="utf-8")
            result, _ = apply_fix_to_file(str(seed_file), apply=True)
            self.assertTrue(result["changed"])
            seed_fixed = seed_file.read_text(encoding="utf-8")
            self.assertIn("fn init(owner: str)", seed_fixed)
            self.service.check(seed_fixed, file=str(seed_file))

            sh_file = tmp / "sh_bank.wysna"
            sh_file.write_text(bad_cls, encoding="utf-8")
            sh_check = run_engine("selfhost", bad_cls, "sh_bank_bad", selfhost_bin=self.selfhost_bin, directory=tmp / "chk")
            self.assertNotEqual(sh_check.exit_code, 0)
            self.assertIn("W3004", sh_check.stderr)

            sh_fix = subprocess.run(
                [self.selfhost_bin, "fix", "--apply", "--output-format=json-v1", str(sh_file)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(sh_fix.returncode, 0, sh_fix.stdout + sh_fix.stderr)
            sh_fixed = sh_file.read_text(encoding="utf-8")
            self.assertIn("fn init(owner: str)", sh_fixed)
            sh_run_dir = tmp / "sh_run"
            sh_run_dir.mkdir(exist_ok=True)
            sh_run = _run_selfhost_cli(self.selfhost_bin, sh_file, sh_run_dir)
            self.assertEqual(sh_run.returncode, 0, sh_run.stderr)
            self.assertEqual(sh_run.stdout, "Washo\n")

    def test_w2_phase6_cross_module_pub_let_tuple_destructuring(self):
        module_src = '''
pub let (base_x, (base_y, _)) = (10, (32, 99))
let (secret_a, secret_b) = (1, 2)
'''
        entry_src = '''
from .pair_mod use { base_x, base_y }
import "pair_mod" as pm

fn main() -> unit {
    print(base_x, base_y, pm.base_x, pm.base_y, base_x + base_y)
}
main()
'''
        private_entry = '''
from .pair_mod use secret_a

fn main() -> unit {
    print(secret_a)
}
main()
'''
        expected = "10 32 10 32 42\n"
        with tempfile.TemporaryDirectory(prefix="wysna-pub-tuple-") as tmpdir:
            root_tmp = Path(tmpdir).resolve()
            for engine in ENGINES:
                eng_dir = root_tmp / engine
                eng_dir.mkdir(parents=True, exist_ok=True)
                (eng_dir / "pair_mod.wysna").write_text(module_src, encoding="utf-8")
                res = run_engine(engine, entry_src, "pub_tuple", selfhost_bin=self.selfhost_bin, directory=root_tmp)
                self.assertEqual(res.exit_code, 0, f"{engine} failed: {res.stderr}")
                self.assertEqual(res.stdout, expected, f"{engine} stdout mismatch")

            if HAS_LLVM:
                llvm_dir = root_tmp / "llvm"
                llvm_dir.mkdir(parents=True, exist_ok=True)
                (llvm_dir / "pair_mod.wysna").write_text(module_src, encoding="utf-8")
                llvm_res = _run_llvm(entry_src, llvm_dir, name="main.wysna")
                self.assertEqual(llvm_res.returncode, 0, f"llvm failed: {llvm_res.stderr}")
                self.assertEqual(llvm_res.stdout, expected, "llvm stdout mismatch")

            neg_dir = root_tmp / "neg"
            neg_dir.mkdir(parents=True, exist_ok=True)
            (neg_dir / "pair_mod.wysna").write_text(module_src, encoding="utf-8")
            with self.assertRaises(WysnaError):
                CompilerService(project_root=ROOT).check(private_entry, file=str(neg_dir / "bad.wysna"))
            bad_res = run_engine("selfhost", private_entry, "pub_tuple_bad", selfhost_bin=self.selfhost_bin, directory=root_tmp / "neg_sh")
            self.assertNotEqual(bad_res.exit_code, 0)

    def test_w2_phase6_w2001_unwrap_maybe_autofix(self):
        broken_src = '''
fn maybe_num() -> int? {
    return 40
}

fn fallible_delta() -> int ! str {
    return Ok(2)
}

fn compute() -> int? {
    let a: int = maybe_num()
    return a + 2
}

fn compute_res() -> int ! str {
    let b: int = fallible_delta()
    return Ok(b + 40)
}
'''
        with tempfile.TemporaryDirectory(prefix="wysna-w2001-unwrap-") as tmpdir:
            tmp = Path(tmpdir).resolve()
            seed_path = tmp / "seed_unwrap.wysna"
            seed_path.write_text(broken_src, encoding="utf-8")

            seed_default = fix_source(str(seed_path), broken_src, include_maybe=False)
            self.assertFalse(seed_default["changed"])
            self.assertEqual(len(seed_default["remaining_diagnostics"]), 2)

            seed_maybe = fix_source(str(seed_path), broken_src, include_maybe=True)
            self.assertTrue(seed_maybe["changed"])
            self.assertEqual(seed_maybe["remaining_diagnostics"], [])
            self.assertIn("let a: int = maybe_num()?", seed_maybe["fixed_source"])
            self.assertIn("let b: int = fallible_delta()?", seed_maybe["fixed_source"])

            sh_file = tmp / "sh_unwrap.wysna"
            sh_file.write_text(broken_src, encoding="utf-8")

            sh_default = subprocess.run(
                [self.selfhost_bin, "fix", "--apply", "--output-format=json-v1", str(sh_file)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(sh_default.returncode, 0)
            self.assertEqual(sh_file.read_text(encoding="utf-8"), broken_src)

            sh_maybe = subprocess.run(
                [self.selfhost_bin, "fix", "--apply", "--include-maybe", "--output-format=json-v1", str(sh_file)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(sh_maybe.returncode, 0, sh_maybe.stdout + sh_maybe.stderr)
            sh_payload = json.loads(sh_maybe.stdout)
            self.assertEqual(sh_payload["status"], "ok")
            self.assertEqual(sh_payload["result"]["remaining_diagnostics"], [])
            fixed_text = sh_file.read_text(encoding="utf-8")
            self.assertIn("let a: int = maybe_num()?", fixed_text)
            self.assertIn("let b: int = fallible_delta()?", fixed_text)


if __name__ == "__main__":
    unittest.main()

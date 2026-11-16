"""Permanent regression suite for Frentes 1-3 (nested block comments, generic class methods, extended error unions, and tuple dict keys)."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from compiler.CompilerService import CompilerService
from compiler.LlvmToolchain import LlvmToolchain
from error.Error import WysnaError
from formatter.Formatter import format_source
from main import ensure_selfhost_compiler_binary
from tests.engine_harness import ENGINES, run_engine
from tooling.LanguageServer import LanguageServer


ROOT = Path(__file__).resolve().parents[2]


def _run_llvm(source: str, workdir: Path, name: str = "prog.wysna") -> subprocess.CompletedProcess:
    service = CompilerService(project_root=ROOT)
    checked = service.check(source, file=str(workdir / name))
    emission = service.emit_llvm(checked)
    toolchain = LlvmToolchain()
    toolchain.verify(emission.source)
    build = toolchain.build(emission, workdir / "llvm_out")
    return subprocess.run([build.output_path], cwd=ROOT, capture_output=True, text=True, timeout=30)


class CloseGapsFrente1To3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
        cls.service = CompilerService(project_root=ROOT)

    def test_frente1_nested_block_comments_all_engines_and_llvm(self):
        source = '''/* outer comment
   /* inner nested /* deepest */ back to middle */
   still inside outer comment */
fn main() -> unit {
    let a /* inline /* nested */ comment */ = 10
    let b = 20 /* trailing /* nested */ */
    let c = 1 /* inline /* deep */ */ + 2
    let d = 3 /* multiline nested
    /* inner block */
    acts as statement boundary */
    print("OK", a, b, c, d)
}
main()
'''
        with tempfile.TemporaryDirectory(prefix="wysna-f1-comments-") as tmpdir:
            root_tmp = Path(tmpdir)
            for engine in ENGINES:
                res = run_engine(engine, source, "nested_comments", selfhost_bin=self.selfhost_bin,
                                 directory=root_tmp / engine)
                self.assertEqual(res.exit_code, 0, f"{engine}: {res.stderr}")
                self.assertEqual(res.stdout, "OK 10 20 3 3\n", engine)

            llvm_res = _run_llvm(source, root_tmp / "llvm")
            self.assertEqual(llvm_res.returncode, 0, llvm_res.stderr)
            self.assertEqual(llvm_res.stdout, "OK 10 20 3 3\n")

    def test_frente1_unterminated_nested_block_comment_rejected(self):
        bad = "/* outer /* inner */ still unclosed\nlet x = 1\n"
        with self.assertRaises(WysnaError) as ctx:
            self.service.check(bad, file="bad_comment.wysna")
        self.assertEqual(ctx.exception.code, "W0002")

        with tempfile.TemporaryDirectory(prefix="wysna-f1-bad-") as tmpdir:
            res = run_engine("selfhost", bad, "bad_comment", selfhost_bin=self.selfhost_bin,
                             directory=Path(tmpdir))
            self.assertNotEqual(res.exit_code, 0)
            self.assertIn("W0002", res.stderr)

    def test_frente1_formatter_and_lsp_semantic_tokens_block_comments(self):
        source = '''/* header /* nested */ */
fn add(x: int) -> int {
    /* inside body /* deeper */ */
    return x + 1 /* trailing */
}
'''
        formatted_once = format_source(source, "fmt_comments.wysna")
        formatted_twice = format_source(formatted_once, "fmt_comments.wysna")
        self.assertEqual(formatted_once, formatted_twice)
        self.assertIn("/* header /* nested */ */", formatted_once)
        self.assertIn("/* inside body /* deeper */ */", formatted_once)

        messages = []
        server = LanguageServer(messages.append)
        uri = (ROOT / "fmt_comments.wysna").as_uri()
        server.dispatch({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
        legend = next(m["result"] for m in messages if m.get("id") == 0)["capabilities"]["semanticTokensProvider"]["legend"]["tokenTypes"]
        server.dispatch({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                         "params": {"textDocument": {"uri": uri, "languageId": "wysna", "version": 1, "text": source}}})
        server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "textDocument/semanticTokens/full",
                         "params": {"textDocument": {"uri": uri}}})
        data = next(m["result"] for m in messages if m.get("id") == 1)["data"]
        lines = source.split("\n")
        decoded, line, column = [], 0, 0
        for index in range(0, len(data), 5):
            delta_line, delta_char, length, kind, modifiers = data[index:index + 5]
            line += delta_line
            column = delta_char if delta_line else column + delta_char
            decoded.append((legend[kind], lines[line][column:column + length]))
        self.assertIn(("comment", "/* header /* nested */ */"), decoded)
        self.assertIn(("comment", "/* inside body /* deeper */ */"), decoded)
        self.assertIn(("comment", "/* trailing */"), decoded)

    def test_frente2_generic_methods_on_classes_and_llvm_and_cross_module(self):
        with tempfile.TemporaryDirectory(prefix="wysna-f2-generics-") as tmpdir:
            dir_path = Path(tmpdir)
            mod_path = dir_path / "box_mod.wysna"
            mod_path.write_text('''pub interface Printable {
    fn label() -> str
}

pub class Tag implements Printable {
    name: str
    fn init(name: str) { this.name = name }
    public fn label() -> str { return this.name }
}

pub class Box[T] {
    value: T
    fn init(value: T) { this.value = value }

    public fn transform[R](f: fn(T) -> R) -> Box[R] {
        return new Box[R](f(this.value))
    }

    public fn pair_with[U](other: U) -> tuple[T, U] {
        return (this.value, other)
    }
}

pub class ChildBox[T] extends Box[T] {
    fn init(value: T) { super.init(value) }
}
''', encoding="utf-8")

            main_path = dir_path / "main_generics.wysna"
            main_source = f'''from "{mod_path}" use {{ Printable, Tag, Box, ChildBox }}

class Util {{
    public fn render[X: Printable](item: X) -> str {{
        return "[" + item.label() + "]"
    }}
}}

fn main() -> unit {{
    let b: ChildBox[int] = new ChildBox[int](21)
    let mapped = b.transform[str](|n: int| => str(n * 2))
    let inferred = b.transform(|n: int| => n + 9)
    let pair = b.pair_with[bool](true)
    match pair {{
        (p0, p1) => {{
            let u = new Util()
            print(mapped.value, inferred.value, p0, p1, u.render[Tag](new Tag("v07")))
        }},
    }}
}}

main()
'''
            main_path.write_text(main_source, encoding="utf-8")
            for engine in ("interpreter", "ast", "hir"):
                res = run_engine(engine, main_source, str(main_path), selfhost_bin=self.selfhost_bin,
                                 directory=dir_path / engine)
                self.assertEqual(res.exit_code, 0, f"{engine}: {res.stderr}")
                self.assertEqual(res.stdout, "42 30 21 true [v07]\n", engine)

            checked = self.service.check(main_source, file=str(main_path))
            emission = self.service.emit_llvm(checked)
            tc = LlvmToolchain()
            tc.verify(emission.source)
            built = tc.build(emission, dir_path / "llvm_out")
            llvm_run = subprocess.run([built.output_path], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(llvm_run.returncode, 0, llvm_run.stderr)
            self.assertEqual(llvm_run.stdout, "42 30 21 true [v07]\n")

    def test_frente2_generic_method_error_diagnostics(self):
        bad_init = '''class A { fn init[T](x: T) {} }'''
        with self.assertRaises(WysnaError) as ctx:
            self.service.check(bad_init, file="bad_init.wysna")
        self.assertEqual(ctx.exception.code, "W2001")

        bad_type_arity = '''class A { public fn f[T, U](x: T) -> T { x } }
fn main() -> unit { let a = new A(); print(a.f[int](1)) }
'''
        with self.assertRaises(WysnaError) as ctx2:
            self.service.check(bad_type_arity, file="bad_type_arity.wysna")
        self.assertIn(ctx2.exception.code, ("W2001", "W2004"))

        bad_val_arity = '''class A { public fn f[T](x: T) -> T { x } }
fn main() -> unit { let a = new A(); print(a.f[int](1, 2)) }
'''
        with self.assertRaises(WysnaError) as ctx2b:
            self.service.check(bad_val_arity, file="bad_val_arity.wysna")
        self.assertEqual(ctx2b.exception.code, "W2005")

        bad_bound = '''interface Named { fn name() -> str }
class A { public fn show[T: Named](x: T) -> str { x.name() } }
fn main() -> unit { let a = new A(); print(a.show[int](1)) }
'''
        with self.assertRaises(WysnaError) as ctx3:
            self.service.check(bad_bound, file="bad_bound.wysna")
        self.assertEqual(ctx3.exception.code, "W2001")

        uncalled = '''class A { public fn id[T](x: T) -> T { x } }
fn main() -> unit { let a = new A(); let _ = a.id }
'''
        with self.assertRaises(WysnaError) as ctx4:
            self.service.check(uncalled, file="uncalled.wysna")
        self.assertEqual(ctx4.exception.code, "W2001")

    def test_frente3_class_error_unions_across_all_engines_including_selfhost_and_llvm(self):
        source = '''class NetworkErr {
    code: int
    msg: str
    fn init(code: int, msg: str) {
        this.code = code
        this.msg = msg
    }
}

struct ParseErr {
    line: int
}

fn step(mode: int) -> int ! NetworkErr | ParseErr {
    if mode == 0 { return Ok(99) }
    if mode == 1 { return Err(new NetworkErr(503, "down")) }
    return Err(ParseErr { line: 12 })
}

fn main() -> unit {
    for mode in 0..3 {
        let out = step(mode) else err {
            match err {
                NetworkErr(e) => { print("NET", e.code, e.msg) },
                ParseErr(p) => { print("PARSE", p.line) },
            }
            continue
        }
        print("OK", out)
    }
}

main()
'''
        with tempfile.TemporaryDirectory(prefix="wysna-f3-class-err-") as tmpdir:
            tmp = Path(tmpdir)
            for engine in ENGINES:
                res = run_engine(engine, source, "class_err", selfhost_bin=self.selfhost_bin,
                                 directory=tmp / engine)
                self.assertEqual(res.exit_code, 0, f"{engine}: {res.stderr}")
                self.assertEqual(res.stdout, "OK 99\nNET 503 down\nPARSE 12\n", engine)

            llvm_res = _run_llvm(source, tmp / "llvm")
            self.assertEqual(llvm_res.returncode, 0, llvm_res.stderr)
            self.assertEqual(llvm_res.stdout, "OK 99\nNET 503 down\nPARSE 12\n")

    def test_frente3_generic_struct_enum_error_unions_and_llvm(self):
        source = '''struct BoxErr[T] {
    detail: T
}

enum GenErr[T] {
    Coded(T),
    Empty,
}

fn work(kind: int) -> int ! BoxErr[int] | GenErr[str] {
    if kind == 0 { return Ok(7) }
    if kind == 1 { return Err(BoxErr[int] { detail: 404 }) }
    return Err(GenErr.Coded("bad-token"))
}

fn main() -> unit {
    for k in 0..3 {
        let v = work(k) else err {
            match err {
                BoxErr[int](b) => { print("BOX", b.detail) },
                GenErr[str](g) => {
                    match g {
                        GenErr.Coded(s) => { print("GEN", s) },
                        GenErr.Empty => { print("GEN_EMPTY") },
                    }
                },
            }
            continue
        }
        print("VAL", v)
    }
}

main()
'''
        with tempfile.TemporaryDirectory(prefix="wysna-f3-gen-err-") as tmpdir:
            tmp = Path(tmpdir)
            for engine in ("interpreter", "ast", "hir"):
                res = run_engine(engine, source, "gen_err", selfhost_bin=self.selfhost_bin,
                                 directory=tmp / engine)
                self.assertEqual(res.exit_code, 0, f"{engine}: {res.stderr}")
                self.assertEqual(res.stdout, "VAL 7\nBOX 404\nGEN bad-token\n", engine)

            llvm_res = _run_llvm(source, tmp / "llvm")
            self.assertEqual(llvm_res.returncode, 0, llvm_res.stderr)
            self.assertEqual(llvm_res.stdout, "VAL 7\nBOX 404\nGEN bad-token\n")

    def test_frente3_tuple_dictionary_keys_all_engines_and_invalid_tuple_rejected(self):
        source = '''fn main() -> unit {
    let mut table: dict[tuple[int, str], int] = {}
    table[(1, "alpha")] = 100
    table[(2, "beta")] = 200
    table[(1, "alpha")] = 150
    let mut nested: dict[tuple[int, tuple[str, bool]], str] = {
        (7, ("ok", true)): "yes",
        (8, ("no", false)): "no",
    }
    print(len(table), table[(1, "alpha")], table[(2, "beta")], nested[(7, ("ok", true))])
}

main()
'''
        with tempfile.TemporaryDirectory(prefix="wysna-f3-tuple-key-") as tmpdir:
            tmp = Path(tmpdir)
            for engine in ENGINES:
                res = run_engine(engine, source, "tuple_key", selfhost_bin=self.selfhost_bin,
                                 directory=tmp / engine)
                self.assertEqual(res.exit_code, 0, f"{engine}: {res.stderr}")
                self.assertEqual(res.stdout, "2 150 200 yes\n", engine)

            llvm_res = _run_llvm(source, tmp / "llvm")
            self.assertEqual(llvm_res.returncode, 0, llvm_res.stderr)
            self.assertEqual(llvm_res.stdout, "2 150 200 yes\n")

        bad_tuple = "let d: dict[tuple[int, float], int] = {}\n"
        with self.assertRaises(WysnaError) as ctx:
            self.service.check(bad_tuple, file="bad_tuple.wysna")
        self.assertEqual(ctx.exception.code, "W2010")

        with tempfile.TemporaryDirectory(prefix="wysna-f3-bad-tuple-") as tmpdir:
            res = run_engine("selfhost", bad_tuple, "bad_tuple", selfhost_bin=self.selfhost_bin,
                             directory=Path(tmpdir))
            self.assertNotEqual(res.exit_code, 0)
            self.assertIn("W2010", res.stderr)


if __name__ == "__main__":
    unittest.main()

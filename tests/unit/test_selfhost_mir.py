"""Independent Compiler MIR production, interchange rejection, and execution parity."""
from copy import deepcopy
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from checker.TypeInterner import TypeInterner
from common.Platform import compiler_command, executable_name
from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from mir.CCodeGen import CCodeGen
from mir.Exchange import import_module
from tooling.MirCLI import emit_mir, run_mir_command

ROOT = Path(__file__).resolve().parents[2]


class CompilerMirTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import ensure_selfhost_compiler_binary
        cls.compiler = ensure_selfhost_compiler_binary(str(ROOT))

    def compile_ir(self, source, output, executable=None):
        completed = subprocess.run(
            [executable or self.compiler, "--emit-mir", str(source)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if completed.returncode == 0:
            output.write_text(completed.stdout, encoding="utf-8")
        return completed

    def seed_ir(self, source):
        service = CompilerService()
        checked = service.check_file(source)
        return service.export_mir(checked)

    def execute_ir(self, payload, directory, name, *, sanitize=False):
        module, interner = import_module(payload)
        c_path = directory / (name + ".c")
        binary = directory / executable_name(name)
        c_path.write_text(CCodeGen(interner).generate(module), encoding="utf-8")
        flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if sanitize else []
        built = subprocess.run(
            compiler_command() + ["-std=c99", "-O1"] + flags + [f"-I{ROOT}", str(c_path), "-lm", "-o", str(binary)],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        environment = dict(os.environ, WYSNA_MEM_CHECK="1")
        if sanitize:
            environment["UBSAN_OPTIONS"] = "halt_on_error=1"
            environment["ASAN_OPTIONS"] = "detect_leaks=" + ("1" if sys.platform.startswith("linux") else "0")
        return subprocess.run([str(binary)], env=environment, capture_output=True, text=True, timeout=10)

    def test_scalar_execution_matches_seed(self):
        cases = [
            ("fn add(x: int, y: int) -> int { x + y }\nprint(add(10, 20))", "30\n"),
            ("fn outer(x: int) -> int { inner(x) }\nfn inner(x: int) -> int { x * 2 }\nprint(outer(21))", "42\n"),
            ("fn main() -> int { print(7) return 12 }", "7\n"),
            ("fn main() { print(7) }\nprint(9)", "9\n"),
            ("fn main() { print(7) }\nmain()", "7\n"),
            ("fn main() -> int { print(7) return 12 }\n(main())", "7\n"),
            ("fn main() -> int { print(7) return 12 }\nmain() + 1", "7\n"),
            ("fn unit_id(x: unit) -> unit { return x }\nunit_id(print())", "\n"),
            ("fn identity(x: bool) -> bool { x }\nprint(identity(not false), 1 < 2, 2 >= 3, true != false)", "true true false true\n"),
            ("let number: float = -0.0\nprint(number, 0.1 + 0.2, int(4.75), float(true))", "-0.0 0.30000000000000004 4 1.0\n"),
            ("let x: int = 00012\nprint(-x * 2 + 30 / 3, 17 % 4)", "-14 1\n"),
            ("let maximum = 9223372036854775807\nlet minimum = -9223372036854775808\nprint(maximum + 1, minimum / -1, minimum % -1, -minimum)", "-9223372036854775808 -9223372036854775808 0 -9223372036854775808\n"),
            ("fn first() -> int { print(1) return 10 }\nfn second() -> int { print(2) return 20 }\nfn combine(x: int, y: int) -> int { x + y }\nprint(combine(first(), second()))", "1\n2\n30\n"),
            ("pub fn twice(x: float) -> float { x * 2.0 }\neprint(twice(1.25))\nprint()", "\n"),
        ]
        with tempfile.TemporaryDirectory(prefix="wysna-mir-parity-") as temporary:
            root = Path(temporary).resolve()
            source = root / "program.wysna"
            output = root / "compiler.mir.json"
            for code, expected in cases:
                with self.subTest(source=code):
                    source.write_text(code + "\n", encoding="utf-8")
                    compiled = subprocess.run(
                        [sys.executable, str(ROOT / "main.py"), "emit-mir", "--implementation=compiler", str(source), "-o", str(output)],
                        cwd=ROOT, capture_output=True, text=True, timeout=120,
                    )
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    payload = output.read_text(encoding="utf-8")
                    self.assertEqual(json.loads(payload)["type_arena"], TypeInterner().to_snapshot())
                    seed_payload = self.seed_ir(source)
                    compiler_module, _ = import_module(payload)
                    seed_module, _ = import_module(seed_payload)
                    self.assertEqual(compiler_module.functions[-1].return_type, seed_module.functions[-1].return_type)
                    compiler_result = self.execute_ir(payload, root, "compiler-program")
                    seed_result = self.execute_ir(seed_payload, root, "seed-program")
                    self.assertEqual((compiler_result.returncode, compiler_result.stdout, compiler_result.stderr),
                                     (seed_result.returncode, seed_result.stdout, seed_result.stderr))
                    self.assertEqual(compiler_result.stdout, expected)

    def test_control_flow_execution_matches_seed(self):
        cases = [
            ("fn choose(x: int) -> int { if x < 0 { 1 } elif x == 0 { 2 } else { 3 } }\nprint(choose(-1), choose(0), choose(1))", "1 2 3\n"),
            ("fn choose(x: bool) -> int { if x { return 7 } else { return 9 } }\nprint(choose(true), choose(false))", "7 9\n"),
            ("fn choose(x: bool) -> int { if x { return 7 }\n10 }\nprint(choose(true), choose(false))", "7 10\n"),
            ("let mut x = 1\nlet mut y = 0\nprint(x + (x = 2), x, y = x = 3, x, y)", "3 2 3 3 3\n"),
            ("fn pair(a: int, b: int) { print(a, b) }\nlet mut x = 1\npair(x, x = 2)\nprint(x)", "1 2\n2\n"),
            ("let mut x = 0\nwhile x < 5 { x = x + 1\nif x == 2 { continue }\nif x == 4 { break }\nprint(x) }\nprint(x)", "1\n3\n4\n"),
            ("let mut i = 0\nlet mut total = 0\nwhile i < 3 { let mut j = 0\nwhile j < 4 { j = j + 1\nif j == 2 { continue }\nif j == 4 { break }\ntotal = total + 1 }\ni = i + 1 }\nprint(total)", "6\n"),
            ("fn search() -> int { let mut i = 0\nwhile i < 5 { if i == 3 { return i }\ni = i + 1 }\nreturn -1 }\nprint(search())", "3\n"),
            ("let mut b = false\nlet mut x = 1.0\nwhile not b { x = x + 1.5\nb = true }\nprint(b, x)", "true 2.5\n"),
            ("fn yes() -> bool { print(1) true }\nprint(false and yes(), true or yes(), true and yes(), false or yes())", "1\n1\nfalse true true true\n"),
            ("let mut x = 0\nprint(false and ((x = 1) == 1), true or ((x = 2) == 2), x)\nprint(true and ((x = 3) == 3), x)", "false true 0\ntrue 3\n"),
            ("print(false and (1 / 0 == 0), true or (1 / 0 == 0))", "false true\n"),
            ("let x = 10\nif true { let x = 20\nprint(x) }\nprint(x)\nwhile false { print(99) }", "20\n10\n"),
            ("fn work() { let value = print(1)\nlet mut i = 0\nwhile i < 3 { let local = print(i)\ni = i + 1\nif i == 1 { continue }\nif i == 3 { break } }\nvalue }\nwork()", "1\n0\n1\n2\n"),
            ("fn check(x: bool) -> bool { let value = print(1)\nx }\nlet mut i = 0\nwhile check(i < 2) { i = i + 1 }\nprint(i)", "1\n1\n1\n2\n"),
            ("fn choose(a: bool, b: bool) -> int { if a { if b { 1 } else { 2 } } else { 3 } }\nprint(choose(true, false), choose(false, true))", "2 3\n"),
        ]
        with tempfile.TemporaryDirectory(prefix="wysna-cfg-parity-") as temporary:
            root = Path(temporary).resolve()
            source = root / "program.wysna"
            output = root / "module.json"
            for code, expected in cases:
                with self.subTest(source=code):
                    source.write_text(code + "\n", encoding="utf-8")
                    compiled = self.compile_ir(source, output)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    compiler_result = self.execute_ir(output.read_text(), root, "compiler")
                    seed_result = self.execute_ir(self.seed_ir(source), root, "seed")
                    self.assertEqual((compiler_result.returncode, compiler_result.stdout, compiler_result.stderr),
                                     (seed_result.returncode, seed_result.stdout, seed_result.stderr))
                    self.assertEqual(compiler_result.stdout, expected)

    def test_native_cfg_verifier_rejects_corruption(self):
        from tools.bundle_selfhost import MODULE_ORDER
        driver = r'''
fn test_module(source: str, file: str) -> MirModule { return mir_build(check_mir_source(source, file)) }
let cli = args()
let mut module = test_module("fn choose(flag: bool) -> int { let output = print(8)\nlet chosen = if flag { 2 } else { 3 }\nchosen }\nprint(choose(true))", "verifier.wysna")
let fault = cli[1]
if fault == "target" { module.functions[0].blocks[0].terminator.targets[0] = 999 }
if fault == "condition" {
    for i in 0..len(module.functions[0].slots) {
        if module.functions[0].slots[i].type_id == 1 { module.functions[0].blocks[0].terminator.value = i break }
    }
}
if fault == "parameter" { module.functions[0].params.append(0) }
if fault == "opcode" { module.functions[0].blocks[0].operations[0].opcode = "invalid" }
if fault == "terminator" { module.functions[0].blocks[0].terminator.kind = "" }
if fault == "join" {
    # Each arm ends by moving its escaped value to the common result slot.
    module.functions[0].blocks[1].operations.pop()
}
if fault == "leak" {
    for i in 0..len(module.functions[0].blocks) {
        let mut operations: list[MirOperation] = []
        for operation in module.functions[0].blocks[i].operations {
            if operation.opcode != "release" { operations.append(operation) }
        }
        module.functions[0].blocks[i].operations = operations
    }
}
if fault == "consumed" {
    for i in 0..len(module.functions[0].blocks) {
        if module.functions[0].blocks[i].terminator.kind == "return" {
            let block = module.functions[0].blocks[i]
            let mut moved = -1
            for operation in block.operations { if operation.opcode == "store" { moved = operation.args[0] } }
            let returned = block.terminator.value
            module.functions[0].blocks[i].operations.append(MirOperation { opcode: "clone", result: returned, args: [moved], name: "", value: mir_literal_unit(), source: block.terminator.source })
        }
    }
}
if fault == "backedge" or fault == "live_backedge" {
    module = test_module("let mut i = 0\nwhile i < 2 { i = i + 1 }\nprint(i)", "loop.wysna")
    let function = module.functions[0]
    let source = function.source
    let slot = len(function.slots)
    if fault == "backedge" {
        module.functions[0].slots.append(MirSlot { type_id: 3, name: "", parameter: false, origin: -1, predicate: false })
        let binding = function.blocks[0].operations[1].result
        module.functions[0].blocks[2].operations.append(MirOperation { opcode: "store", result: slot, args: [binding], name: "", value: mir_literal_unit(), source: source })
    } else {
        module.functions[0].slots.append(MirSlot { type_id: 1, name: "", parameter: false, origin: -1, predicate: false })
        let operands: list[int] = []
        module.functions[0].blocks[2].operations.append(MirOperation { opcode: "const", result: slot, args: operands, name: "", value: mir_literal_unit(), source: source })
    }
}
if fault == "string_leak" or fault == "string_borrow" or fault == "string_move" or fault == "string_helper" or fault == "string_literal" {
    module = test_module("fn copy(value: str) -> str { let temp = value + \"!\"\ntemp }\nprint(copy(\"a\"))", "string.wysna")
    for i in 0..len(module.functions[0].blocks) {
        let mut operations: list[MirOperation] = []
        for operation in module.functions[0].blocks[i].operations {
            let mut candidate = operation
            if fault == "string_move" and candidate.opcode == "store" { candidate.args = [0] }
            if fault == "string_helper" and candidate.opcode == "runtime" { candidate.name = "wysna_mul" }
            if fault == "string_literal" and candidate.opcode == "const" { candidate.value.type_id = 3 }
            if fault != "string_leak" or candidate.opcode != "release" { operations.append(candidate) }
        }
        module.functions[0].blocks[i].operations = operations
        if fault == "string_borrow" and module.functions[0].blocks[i].terminator.kind == "return" { module.functions[0].blocks[i].terminator.value = 0 }
    }
}
if len(fault) >= 7 and slice(fault, 0, 7) == "result_" {
    module = test_module("fn value(flag: bool) -> str ! str { if flag { return Ok(\"ok\") }\nErr(\"bad\") }\nfn forward(flag: bool) -> str ! str { let local = \"held-\" + str(1)\nlet result = value(flag)?\nresult }\nprint(forward(false) else error { error })", "result.wysna")
    if fault == "result_arena" { module.result_types[0].error = 3 }
    if fault == "result_duplicate" { module.result_types.append(module.result_types[0]) }
    for i in 0..len(module.functions[1].blocks) {
        let block = module.functions[1].blocks[i]
        let mut operations: list[MirOperation] = []
        for operation in block.operations {
            if fault == "result_guard" and operation.opcode == "test" {
                let term = block.terminator
                module.functions[1].blocks[i].terminator.targets = [term.targets[1], term.targets[0]]
            }
            if fault == "result_reuse" and operation.opcode == "test" { operations.append(operation) }
            if fault == "result_stale" and operation.name == "wysna_unwrap_err" {
                let value = operation.args[0]
                let copy = len(module.functions[1].slots)
                module.functions[1].slots.append(MirSlot { type_id: module.functions[1].slots[value].type_id, name: "", parameter: false, origin: -1, predicate: false })
                operations.append(MirOperation { opcode: "clone", result: copy, args: [value], name: "", value: mir_literal_unit(), source: operation.source })
                operations.append(MirOperation { opcode: "store", result: value, args: [copy], name: "", value: mir_literal_unit(), source: operation.source })
            }
            if fault != "result_leak" or operation.opcode != "release" { operations.append(operation) }
        }
        module.functions[1].blocks[i].operations = operations
    }
    if fault == "result_move" {
        module = test_module("fn copy(payload: str) -> str ! str { Ok(payload) }\nprint(copy(\"a\") else { \"bad\" })", "borrow.wysna")
        for i in 0..len(module.functions[0].blocks) {
            for j in 0..len(module.functions[0].blocks[i].operations) {
                if module.functions[0].blocks[i].operations[j].name == "wysna_ok" { module.functions[0].blocks[i].operations[j].args = [0] }
            }
        }
    }
    if fault == "result_const" {
        let slot = len(module.functions[1].slots)
        module.functions[1].slots.append(MirSlot { type_id: 7, name: "", parameter: false, origin: -1, predicate: false })
        let mut literal = mir_literal_unit()
        literal.type_id = 7
        let operands: list[int] = []
        module.functions[1].blocks[0].operations.append(MirOperation { opcode: "const", result: slot, args: operands, name: "", value: literal, source: module.functions[1].source })
    }
    if fault == "result_facts" {
        let before = mir_module_json(module)
        let normal = check_source("print(1)", "normal.wysna")
        if normal.has_nodes or len(normal.nodes) != 0 { report_error_at("W9999", module.source, "unexpected MIR nodes in metadata check") }
        if mir_module_json(module) != before { report_error_at("W9999", module.source, "MIR module changed across check sessions") }
    }
}
mir_verify(module)
print("verified")
'''
        modules = [(ROOT / "selfhost/src" / name).read_text() for name in MODULE_ORDER if name != "09_main.wysna"]
        service = CompilerService()
        checked = service.check("".join(modules) + driver, "mir-verifier-test.wysna")
        generated = service.emit_c(checked, backend="ast").source
        with tempfile.TemporaryDirectory(prefix="wysna-cfg-verifier-") as temporary:
            root = Path(temporary).resolve()
            c_path = root / "verifier.c"
            binary = root / executable_name("verifier")
            c_path.write_text(generated, encoding="utf-8")
            built = subprocess.run(compiler_command() + ["-std=c99", "-O0", f"-I{ROOT}", str(c_path), "-lm", "-o", str(binary)],
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stderr)
            for fault in ("valid", "target", "condition", "parameter", "opcode", "terminator", "join", "leak", "consumed", "backedge", "live_backedge", "string_leak", "string_borrow", "string_move", "string_helper", "string_literal", "result_valid", "result_facts", "result_arena", "result_duplicate", "result_guard", "result_reuse", "result_stale", "result_leak", "result_move", "result_const"):
                with self.subTest(fault=fault):
                    result = subprocess.run([str(binary), fault], capture_output=True, text=True, timeout=15)
                    if fault in ("valid", "result_valid", "result_facts"):
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, "verified\n")
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("[W9999]", result.stderr)

    def test_managed_strings_match_seed_with_sanitizers(self):
        cases = [
            ('print("", "x", "hello", "árbol 😀", "a\\0b", "a\\n\\t\\\"\\\\")', ' x hello árbol 😀 a\0b a\n\t"\\\n'),
            ('fn identity(value: str) -> str { value }\nlet value = "prefix-" + str(42)\nprint(identity(value), value)', "prefix-42 prefix-42\n"),
            ('fn choose(flag: bool, value: str) -> str { if flag { return value + "!" }\nvalue + "?" }\nprint(choose(true, "ok"), choose(false, "no"))', "ok! no?\n"),
            ('fn choose(flag: bool) -> str { if flag { "yes-" + str(1) } elif not flag { "no-" + str(2) } else { "none" } }\nprint(choose(true), choose(false))', "yes-1 no-2\n"),
            ('let mut value: str = "old"\nlet alias = value\nprint(value, value = "new", value, alias)\nvalue = value\nprint(value)', "old new new old\nnew\n"),
            ('let mut value = "ab"\nprint(value[len(value = "changed") - 7], value)', "a changed\n"),
            ('fn combine(a: str, b: str) -> str { a + b }\nlet mut x = "a"\nprint(combine(x, x = "b"), x)', "ab b\n"),
            ('print("abc" == "a" + "bc", "x" != "y", len("a😀é"), ord("😀"), chr(128512), str(true), int("42"), float("1.25"))', "true true 3 128512 😀 true 42 1.25\n"),
            ('let value = "a😀éz"\nprint(value[1], value[3], slice(value, 1, 3), slice(value, -2, 99), slice(value, 3, 1))', "😀 z 😀é a😀éz \n"),
            ('let mut i = 0\nlet mut value = ""\nwhile i < 4 { let local = str(i)\ni = i + 1\nif i == 2 { continue }\nvalue = value + local\nif i == 3 { break } }\nprint(value)', "02\n"),
            ('fn work() -> str { let outer = "prefix-" + str(1)\nlet mut i = 0\nwhile i < 4 { let local = outer + str(i)\nif i == 2 { return local }\ni = i + 1 }\nouter }\nprint(work())', "prefix-12\n"),
            ('fn condition(value: str, flag: bool) -> bool { let local = value + "!"\nlen(local) > 0 and flag }\nlet mut i = 0\nwhile condition(str(i), i < 3) { i = i + 1 }\nprint(i)', "3\n"),
            ('let mut i = 0\nwhile i < 3 { if i == 1 { "discard-" + str(i) }\ni = i + 1 }\nprint(i)', "3\n"),
            ('let outer = "same" + str(1)\nif true { let outer = "inner" + str(2)\nprint(outer) }\nprint(outer)', "inner2\nsame1\n"),
            ('fn side() -> bool { let value = "skip-" + str(1)\nprint(value)\ntrue }\nprint(false and side(), true or side())', "false true\n"),
            ('fn recurse(n: int, value: str) -> str { if n == 0 { return value }\nreturn recurse(n - 1, value + str(n)) }\nprint(recurse(4, "r"))', "r4321\n"),
            ('fn main() -> int { let value = "main-" + str(7)\nprint(value)\nreturn 12 }', "main-7\n"),
            ('let mut i = 0\nlet mut value = "initial"\nwhile i < 500 { let local = str(i) + "-suffix"\nvalue = slice(local, 0, 3)\ni = i + 1 }\nprint(value)', "499\n"),
        ]
        with tempfile.TemporaryDirectory(prefix="wysna-managed-mir-") as temporary:
            root = Path(temporary).resolve()
            source = root / "strings.wysna"
            output = root / "module.json"
            for code, expected in cases:
                with self.subTest(source=code):
                    source.write_text(code + "\n", encoding="utf-8")
                    compiled = self.compile_ir(source, output)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    compiler_result = self.execute_ir(output.read_text(), root, "compiler", sanitize=True)
                    seed_result = self.execute_ir(self.seed_ir(source), root, "seed", sanitize=True)
                    self.assertEqual((compiler_result.returncode, compiler_result.stdout, compiler_result.stderr),
                                     (seed_result.returncode, seed_result.stdout, seed_result.stderr))
                    self.assertEqual(compiler_result.returncode, 0)
                    self.assertEqual(compiler_result.stdout, expected)
                    self.assertEqual(compiler_result.stderr, "")

    def test_result_channels_match_seed_with_sanitizers(self):
        cases = [
            ('fn bad() -> int ! str { Err("bad") }\nfn work(flag: bool) -> unit ! str { (if flag { (if true { bad() } else { 0 }) } else { 1 }) }\nwork(true) else error { print(error) }', 'bad\n'),
            ('fn bad() -> int ! str { Err("bad") }\nfn work(flag: bool) -> unit ! str { if flag { bad() }\nprint("after") }\nwork(true) else error { print(error) }', 'after\n'),
            ('fn bad() -> int ! str { Err("bad") }\nfn work(flag: bool) -> unit ! str { if flag { bad() } }\nwork(true) else error { print(error) }\nwork(false) else error { print(error) }', 'bad\n'),
            ('fn bad() -> int ! str { Err("bad") }\nfn work(flag: bool) -> unit ! str { if not flag { 0 } elif flag { bad() } else { 1 } }\nwork(true) else error { print(error) }', 'bad\n'),
            ('fn work(flag: bool) -> unit ! str { if flag { Err("bad") } else { Ok(7) } }\nwork(true) else error { print(error) }\nwork(false) else error { print(error) }', 'bad\n'),
            ('fn bad() -> int ! str { Err("bad") }\nfn good() -> str ! str { "ok" }\nfn work(flag: bool) -> unit ! str { if flag { bad() } else { good() } }\nwork(true) else error { print(error) }\nwork(false) else error { print(error) }', 'bad\n'),
            ('fn bad() -> str ! str { Err("bad") }\nfn work() -> str ! str { bad() else error { return Err(error + "!") } }\nprint(work() else error { error })', 'bad!\n'),
            ('fn value(flag: bool) -> int ! str { if flag { return Ok(7) }\nreturn Err("bad") }\nprint(value(true) else { 0 }, value(false) else error { len(error) })', '7 3\n'),
            ('fn value(flag: bool) -> str ! str { if flag { return "ok-" + str(1) }\nreturn Err("bad-" + str(2)) }\nprint(value(true) else { "fallback" }, value(false) else error { error + "!" })', 'ok-1 bad-2!\n'),
            ('fn source(flag: bool) -> int ! str { if flag { return 10 }\nreturn Err("failed") }\nfn forward(flag: bool) -> int ! str { let local = "alive-" + str(1)\nlet value = source(flag)?\nvalue + 2 }\nprint(forward(true) else { 0 }, forward(false) else error { len(error) })', '12 6\n'),
            ('fn source(flag: bool) -> int ! str { if flag { return 3 }\nreturn Err("failed") }\nfn discard(flag: bool) -> unit ! str { source(flag) }\ndiscard(false) else error { print(error) }\ndiscard(true) else error { print(error) }', 'failed\n'),
            ('fn done() -> unit ! str { let local = "live" + str(1)\nreturn }\ndone() else error { print(error) }\nprint("done")', 'done\n'),
            ('fn done() -> unit ! str { Ok("discarded" + str(1)) }\ndone() else error { print(error) }\nprint("done")', 'done\n'),
            ('fn read(value: int ! str) -> int { value else error { len(error) } }\nprint(read(4), read(Ok(5)), read(Err("error")))', '4 5 5\n'),
            ('fn identity(value: str ! str) -> str ! str { value }\nlet value: str ! str = "owned-" + str(1)\nlet saved = identity(value)\nprint(saved else { "bad" }, value else { "bad" })', 'owned-1 owned-1\n'),
            ('let mut value: int ! str = 1\nvalue = Err("bad")\nprint(value else error { len(error) })\nvalue = Ok(3)\nprint(value else { 0 })', '3\n3\n'),
            ('fn bad() -> str ! str { Err("bad-" + str(2)) }\nfn pair(a: str, b: str) -> str { a + b }\nfn work() -> str ! str { let before = "before-" + str(1)\nreturn pair(before, bad()?) }\nprint(work() else error { error })', 'bad-2\n'),
            ('fn bad() -> str ! str { Err("bad") }\nfn work() -> str ! str { let mut value = "old-" + str(1)\nvalue = bad()?\nvalue }\nprint(work() else error { error })', 'bad\n'),
            ('fn bad() -> str ! str { Err("bad") }\nfn work() -> str { let value = bad() else error { return error + "!" }\nvalue }\nprint(work())', 'bad!\n'),
            ('fn value(i: int) -> int ! str { if i < 2 { return Err(str(i)) }\nreturn Ok(i) }\nlet mut i = 0\nwhile i < 4 { i = i + 1\nlet local = "keep-" + str(i)\nlet result = value(i) else error { continue }\nprint(result) }', '2\n3\n4\n'),
            ('fn value(i: int) -> int ! str { if i == 3 { return Err(str(i)) }\ni }\nlet mut i = 0\nwhile i < 5 { i = i + 1\nlet result = value(i) else error { break }\nprint(result) }', '1\n2\n'),
            ('fn bad() -> str ! str { Err("e\\0x") }\nprint(bad() else error { str(len(error)) })', '3\n'),
            ('fn bad() -> str ! str { Err("inner") }\nlet error = "outer"\nprint(bad() else error { error }, error)', 'inner outer\n'),
            ('fn boolean() -> bool ! str { true }\nfn number() -> float ! str { 1.25 }\nprint(boolean() else { false }, number() else { 0.0 })', 'true 1.25\n'),
            ('fn value() -> Result[int, str] { Ok(42) }\nlet result = value()\nprint(result else { 0 })', '42\n'),
            ('fn value(flag: bool) -> str ! str { if flag { return Ok("yes") } else { return Err("no") } }\nlet result = value(false)\nprint(result else error { error }, result else { "again" })', 'no again\n'),
            ('fn bad() -> int ! str { Err("bad") }\nprint(bad()\nelse error { len(error) })', '3\n'),
            ('fn value(i: int) -> str ! str { if i % 2 == 0 { return Err("e" + str(i)) }\n"ok" + str(i) }\nlet mut i = 0\nlet mut last = ""\nwhile i < 200 { last = value(i) else error { error }\ni = i + 1 }\nprint(last)', 'ok199\n'),
        ]
        with tempfile.TemporaryDirectory(prefix="wysna-result-mir-") as temporary:
            root = Path(temporary).resolve()
            source = root / "results.wysna"
            output = root / "module.json"
            for code, expected in cases:
                with self.subTest(source=code):
                    source.write_text(code + "\n", encoding="utf-8")
                    compiled = self.compile_ir(source, output)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    payload = output.read_text()
                    entries = json.loads(payload)["type_arena"]["entries"]
                    result_types = [tuple(entry["data"]) for entry in entries if entry["tag"] == "result"]
                    self.assertTrue(result_types)
                    self.assertEqual(len(result_types), len(set(result_types)))
                    self.assertTrue(all(1 <= ok <= 5 and error == 5 for ok, error in result_types))
                    compiler_result = self.execute_ir(payload, root, "compiler", sanitize=True)
                    seed_result = self.execute_ir(self.seed_ir(source), root, "seed", sanitize=True)
                    self.assertEqual((compiler_result.returncode, compiler_result.stdout, compiler_result.stderr),
                                     (seed_result.returncode, seed_result.stdout, seed_result.stderr))
                    self.assertEqual(compiler_result.returncode, 0, compiler_result.stderr)
                    if expected is not None:
                        self.assertEqual(compiler_result.stdout, expected)
                    self.assertEqual(compiler_result.stderr, "")

    def test_string_index_runtime_errors_keep_source_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'índice "quoted".wysna'
            output = root / "module.json"
            for index in ("1", "-2"):
                source.write_text('fn read(value: str) -> str {\nreturn value[' + index + ']\n}\nprint(read("x"))\n')
                compiled = self.compile_ir(source, output)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                for name, payload in (("compiler", output.read_text()), ("seed", self.seed_ir(source))):
                    result = self.execute_ir(payload, root, name)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("[W4002]", result.stderr)
                    self.assertIn(f"{source}:2:", result.stderr)

    def test_runtime_checks_keep_source_locations(self):
        with tempfile.TemporaryDirectory(prefix="wysna-mir-runtime-") as temporary:
            root = Path(temporary).resolve()
            source = root / 'árbol "quoted".wysna'
            output = root / "module.json"
            for operator in ("/", "%"):
                source.write_text(f"fn divide(x: int) -> int {{\n return x {operator} 0\n}}\nprint(divide(1))\n", encoding="utf-8")
                compiled = self.compile_ir(source, output)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                payload = output.read_text(encoding="utf-8")
                self.assertEqual(json.loads(payload)["module"]["file"], str(source))
                for name, candidate in (("compiler", payload), ("seed", self.seed_ir(source))):
                    result = self.execute_ir(candidate, root, name)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("[W4001]", result.stderr)
                    self.assertIn(f"{source}:2:", result.stderr)

    def test_semantic_rejections_match_seed_and_preserve_output(self):
        cases = [
            ("fn f(x: int) -> int { x }\nprint(f(true))", "W2001"),
            ("fn f() -> int { return true }", "W2004"),
            ("print(missing)", "W1001"),
            ('(if true { 1 } else { "wrong" }, 2)', "W2001"),
            ('fn bad() -> int ! str { Err("bad") }\nfn wrong() -> int { bad()? }', "W2006"),
            ('fn bad() -> int ! str { Err("bad") }\nprint(bad() else { "wrong" })', "W2001"),
            ('let mut value: int ! str = 1\nvalue = Err(2)', "W2001"),
            ('print(len(1))', "W2001"),
            ('print("x"[true])', "W2001"),
            ('print("x" + 1)', "W2001"),
            ('fn copy(value: str) -> str { return true }', "W2004"),
            ("fn f(x: int) -> int { x }\nprint(f())", "W2005"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "invalid.wysna"
            output = Path(temporary).resolve() / "module.json"
            for code, expected in cases:
                with self.subTest(source=code):
                    source.write_text(code + "\n", encoding="utf-8")
                    output.write_text("keep existing artifact", encoding="utf-8")
                    compiled = subprocess.run(
                        [sys.executable, str(ROOT / "main.py"), "emit-mir", "--implementation=compiler", str(source), "-o", str(output)],
                        cwd=ROOT, capture_output=True, text=True, timeout=120,
                    )
                    self.assertNotEqual(compiled.returncode, 0)
                    self.assertIn(f"[{expected}]", compiled.stderr)
                    self.assertEqual(output.read_text(), "keep existing artifact")
                    with self.assertRaises(WysnaError) as caught:
                        CompilerService().check_file(source)
                    self.assertEqual(caught.exception.code, expected)

    def test_unsupported_features_are_explicit(self):
        cases = [
            "for i in 0..3 { print(i) }",
            "fn identity[T](x: T) -> T { x }\nprint(identity(2))",
            "struct Box { value: int }\nlet box = Box { value: 1 }\nprint(box.value)",
            "let global = 3\nfn read() -> int { global }\nprint(read())",
            'fn fail() -> int ! int { return Err(1) }',
            'let result = Ok(1)\nprint(result)',
            "let values = [1, 2]\nprint(len(values))",
            "let increment = |x: int| { x + 1 }\nprint(increment(2))",
            'extern "C" fn native(x: int) -> int\nprint(1)',
        ]
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "unsupported.wysna"
            output = Path(temporary).resolve() / "module.json"
            for code in cases:
                with self.subTest(source=code):
                    source.write_text(code + "\n", encoding="utf-8")
                    CompilerService().check_file(source)
                    compiled = self.compile_ir(source, output)
                    self.assertNotEqual(compiled.returncode, 0)
                    self.assertIn("[W2013]", compiled.stderr)
                    self.assertIn("result-cfg-v1", compiled.stderr)
                    self.assertFalse(output.exists())
            source.write_text('import "missing.wysna"\n', encoding="utf-8")
            imported = self.compile_ir(source, output)
            self.assertIn("[W2013]", imported.stderr)
            self.assertIn("imports", imported.stderr)

    def test_exchange_verifier_rejects_tampered_compiler_ir(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "program.wysna"
            output = Path(temporary).resolve() / "module.json"
            source.write_text("fn add(x: int, y: int) -> int { x + y }\nprint(add(1, 2))\n")
            result = self.compile_ir(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = json.loads(output.read_text())
            mutations = []
            bad = deepcopy(original)
            bad["module"]["functions"][0]["slots"][0]["type_id"] = 999
            mutations.append(bad)
            bad = deepcopy(original)
            bad["module"]["functions"][0]["blocks"][0]["operations"][0]["args"] = {"$tuple": [999]}
            mutations.append(bad)
            bad = deepcopy(original)
            bad["module"]["functions"][0]["return_type"] = 2
            mutations.append(bad)
            bad = deepcopy(original)
            op = next(op for fn in bad["module"]["functions"] for op in fn["blocks"][0]["operations"] if op["opcode"] == "call")
            op["data"]["name"] = "missing"
            mutations.append(bad)
            bad = deepcopy(original)
            op = next(op for op in bad["module"]["functions"][0]["blocks"][0]["operations"] if op["opcode"] == "runtime")
            op["data"]["name"] = "system"
            mutations.append(bad)
            for payload in mutations:
                with self.assertRaises(WysnaError):
                    import_module(json.dumps(payload))

    def test_exchange_rejects_corrupted_control_flow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "branch.wysna"
            output = root / "module.json"
            source.write_text("fn choose(flag: bool) -> int { if flag { 2 } else { 3 } }\nprint(choose(true))\n")
            result = self.compile_ir(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = json.loads(output.read_text())
            for fault in ("target", "condition", "terminator", "join"):
                with self.subTest(fault=fault):
                    bad = deepcopy(original)
                    function = bad["module"]["functions"][0]
                    branch = function["blocks"][0]["terminator"]
                    if fault == "target":
                        branch["targets"] = {"$tuple": [999, 2]}
                    elif fault == "condition":
                        branch["value"] = next(slot["id"] for slot in function["slots"] if slot["type_id"] == 3)
                    elif fault == "terminator":
                        branch["kind"] = ""
                    else:
                        function["blocks"][1]["operations"].pop()
                    with self.assertRaises(WysnaError):
                        import_module(json.dumps(bad))

    def test_exchange_rejects_invalid_string_lifetimes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "strings.wysna"
            output = root / "module.json"
            source.write_text('fn copy(value: str) -> str { value + "!" }\nprint(copy("a"))\n')
            result = self.compile_ir(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = json.loads(output.read_text())
            for fault in ("literal", "borrow", "move", "leak", "helper"):
                with self.subTest(fault=fault):
                    bad = deepcopy(original)
                    function = bad["module"]["functions"][0]
                    block = function["blocks"][0]
                    if fault == "literal":
                        constant = next(op for op in block["operations"] if op["opcode"] == "const" and isinstance(op["data"]["value"], str))
                        constant["data"]["value"] = 123
                    elif fault == "borrow":
                        block["terminator"]["value"] = function["params"][0]
                    elif fault == "move":
                        moved = next(op for op in block["operations"] if op["opcode"] == "store")
                        moved["args"] = {"$tuple": [function["params"][0]]}
                    elif fault == "leak":
                        block["operations"] = [op for op in block["operations"] if op["opcode"] != "release"]
                    else:
                        operation = next(op for op in block["operations"] if op["opcode"] == "runtime")
                        operation["data"]["name"] = "wysna_mul"
                    with self.assertRaises(WysnaError):
                        import_module(json.dumps(bad))

    def test_exchange_rejects_invalid_result_contracts(self):
        source_code = '''fn value(flag: bool, text: str) -> str ! str {
            if flag { return Ok(text) }
            Err(text)
        }
        fn read() -> str { value(false, "bad") else error { error } }
        print(read())'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "results.wysna"
            output = root / "module.json"
            source.write_text(source_code)
            result = self.compile_ir(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = json.loads(output.read_text())
            for fault in ("arena", "moves", "borrow", "guard", "reused", "stale"):
                with self.subTest(fault=fault):
                    bad = deepcopy(original)
                    if fault == "arena":
                        entry = next(entry for entry in bad["type_arena"]["entries"] if entry["tag"] == "result")
                        entry["data"][1] = 3
                    elif fault in ("moves", "borrow"):
                        function = bad["module"]["functions"][0]
                        operation = next(op for block in function["blocks"] for op in block["operations"] if op["data"].get("name") == "wysna_ok")
                        if fault == "moves":
                            operation["moves"] = {"$tuple": []}
                        else:
                            operation["args"] = {"$tuple": [function["params"][1]]}
                    else:
                        function = bad["module"]["functions"][1]
                        block = next(block for block in function["blocks"] if any(op["opcode"] == "test" for op in block["operations"]))
                        guard = next(op for op in block["operations"] if op["opcode"] == "test")
                        if fault == "guard":
                            block["terminator"]["targets"]["$tuple"].reverse()
                        elif fault == "reused":
                            block["operations"].append(deepcopy(guard))
                        else:
                            arm = next(block for block in function["blocks"] if any(op["data"].get("name") == "wysna_unwrap_err" for op in block["operations"]))
                            value = guard["args"]["$tuple"][0]
                            copy = len(function["slots"])
                            slot = deepcopy(function["slots"][value])
                            slot["id"] = copy
                            function["slots"].append(slot)
                            clone = deepcopy(guard)
                            clone.update(opcode="clone", result=copy, data={}, effect="retain")
                            store = deepcopy(clone)
                            store.update(opcode="store", result=value, args={"$tuple": [copy]}, moves={"$tuple": [0]}, effect="move")
                            arm["operations"][:0] = [clone, store]
                    with self.assertRaises(WysnaError):
                        import_module(json.dumps(bad))

    def test_bootstrap_stages_emit_identical_ir(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "same.wysna"
            source.write_text('fn value(flag: bool) -> str ! str { if flag { return "ok" }\nErr("bad") }\nfn forward(flag: bool) -> str ! str { value(flag)? }\nprint(forward(false) else error { error })\n')
            outputs = []
            for stage in ("stage1", "stage2"):
                output = Path(temporary).resolve() / (stage + ".json")
                binary = ROOT / ".wysna_cache" / executable_name(stage)
                result = self.compile_ir(source, output, str(binary))
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs.append(output.read_bytes())
            self.assertEqual(outputs[0], outputs[1])

    def test_cli_producers_and_source_protection(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "program.wysna"
            source.write_text("print(42)\n")
            for implementation in ("seed", "compiler"):
                output = Path(temporary).resolve() / (implementation + ".json")
                result = subprocess.run(
                    [sys.executable, str(ROOT / "main.py"), "emit-mir", f"--implementation={implementation}", str(source), "-o", str(output)],
                    cwd=ROOT, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                import_module(output.read_text())
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(run_mir_command([f"--implementation={implementation}", str(source), "-o", str(source)]), 2)
                self.assertEqual(source.read_text(), "print(42)\n")
            # A prebuilt Compiler checks fallible code without invoking Seed's frontend.
            independent = Path(temporary).resolve() / "independent.wysna"
            independent.write_text('fn value() -> int ! str { Err("bad") }\nprint(value() else error { len(error) })\n')
            with patch("compiler.Bootstrap.ensure_selfhost_compiler_binary", return_value=self.compiler), patch.object(CompilerService, "check", side_effect=AssertionError("Seed fallback")):
                payload, _ = emit_mir(independent, "compiler")
                import_module(payload)

            alias = Path(temporary).resolve() / "source-alias.json"
            os.link(source, alias)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(run_mir_command([str(source), "-o", str(alias)]), 2)
            dependency = Path(temporary).resolve() / "dependency.wysna"
            dependency.write_text("pub fn number() -> int { 42 }\n")
            source.write_text('import "dependency.wysna"\nprint(dependency.number())\n')
            before = dependency.read_bytes()
            with redirect_stderr(io.StringIO()):
                self.assertEqual(run_mir_command([str(source), "-o", str(dependency)]), 2)
            self.assertEqual(dependency.read_bytes(), before)

    def test_service_export_rejects_foreign_checked_programs(self):
        owner = CompilerService()
        checked = owner.check("print(42)")
        import_module(owner.export_mir(checked))
        with self.assertRaises(WysnaError) as caught:
            CompilerService().export_mir(checked)
        self.assertEqual(caught.exception.stage, "semantic-boundary")

    def test_cli_usage_and_missing_input(self):
        for args, code in (([], "W5002"), (["--implementation=other", "file.wysna"], "W5002"),
                           (["/nonexistent-wysna-mir-source.wysna"], "W5003")):
            with self.subTest(args=args), redirect_stderr(io.StringIO()) as stderr:
                self.assertNotEqual(run_mir_command(args), 0)
                self.assertIn(f"[{code}]", stderr.getvalue())

    def test_numeric_literal_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "literal.wysna"
            output = Path(temporary).resolve() / "module.json"
            for literal in ("9223372036854775808", "-9223372036854775809", "999999999999999999999999999999", "9" * 400 + ".0"):
                source.write_text(f"print({literal})\n")
                result = self.compile_ir(source, output)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[W0002]", result.stderr)
                self.assertFalse(output.exists())
                with self.assertRaises(WysnaError) as caught:
                    CompilerService().check_file(source)
                self.assertEqual(caught.exception.code, "W0002")


if __name__ == "__main__":
    unittest.main()

"""Native phase-boundary rejection and isolation between compiler invocations."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from compiler.CompilerService import CompilerService
from tools.bundle_selfhost import MODULE_ORDER

ROOT = Path(__file__).resolve().parents[2]
DRIVER = r'''
let cli = args()
let mode = cli[1]
let first = check_source("fn choose(flag: bool) -> str ! str { let value = if flag { \"yes\" } else { \"no\" }\nOk(value) }\nprint(choose(true) else error { error })", "first.wysna")
let expected = mir_module_json(mir_build(first))
if mode == "isolated" {
    let second = check_source("fn add(a: int, b: int) -> int { a + b }\nprint(add(1, 2))", "second.wysna")
    let second_module = mir_build(second)
    let second_before = mir_module_json(second_module)
    let normal = check_source("class Box { value: int\nfn init(value: int) { this.value = value } }\nlet box = new Box(7)\nprint(box.value)", "normal.wysna")
    let c_before = compile_source(normal)
    let other = check_source("fn message() -> str { \"hello\" }\nprint(message())", "other.wysna")
    let ignored = compile_source(other)
    if compile_source(normal) != c_before { report_error_at("W9999", first.source, "C emitter state leaked") }
    if mir_module_json(mir_build(first)) != expected { report_error_at("W9999", first.source, "checked result changed across sessions") }
    if mir_module_json(second_module) != second_before { report_error_at("W9999", first.source, "MIR module changed across sessions") }
}
if mode == "detached_source" {
    let detached = check_source("print(7)", "unavailable-source.wysna")
    let generated = compile_source(detached)
    if len(generated) == 0 { report_error_at("W9999", first.source, "consumer requires source contents") }
}
if mode == "copy" {
    let mut changed = first
    changed.nodes[len(changed.nodes) - 1].kind = "invalid"
    if mir_module_json(mir_build(first)) != expected { report_error_at("W9999", first.source, "checked consumer mutated another consumer") }
}
if mode == "builders" {
    let a = new MirBuilder(first)
    let b = new MirBuilder(first)
    mir_begin(a, first.source, 5)
    let value = mir_constant(a, mir_literal_str("kept"), 5, first.source)
    mir_begin(b, first.source, 3)
    let other = mir_constant(b, mir_literal_int(8), 3, first.source)
    mir_return(a, value, first.source)
    mir_return(b, other, first.source)
    mir_finish(a, "wysna_program", first.source)
    mir_finish(b, "wysna_program", first.source)
    let module_a = MirModule { file: "a", source: first.source, functions: a.functions, globals: a.globals, records: a.records, virtual_slots: a.virtual_slots, externs: a.externs, types: a.types }
    let module_b = MirModule { file: "b", source: first.source, functions: b.functions, globals: b.globals, records: b.records, virtual_slots: b.virtual_slots, externs: b.externs, types: b.types }
    let before = mir_module_json(module_a)
    let ignored = mir_module_json(module_b)
    if mir_module_json(module_a) != before { report_error_at("W9999", first.source, "interleaved MIR builders share storage") }
}
if mode == "imports" {
    let source_a = read_file(cli[2]) else { "" }
    let source_b = read_file(cli[3]) else { "" }
    let checked_a = check_source(source_a, cli[2])
    let checked_b = check_source(source_b, cli[3])
    let before = compile_source(checked_a)
    let ignored = compile_source(checked_b)
    let again = check_source(source_a, cli[2])
    if compile_source(again) != before { report_error_at("W9999", first.source, "module graph leaked between compilations") }
}
if mode == "declarations" {
    let normal = check_source("struct Hidden { value: int }\nlet hidden = Hidden { value: 7 }", "declaration.wysna")
    let invalid = check_source("let value = Hidden { value: 3 }", "unknown.wysna")
}
if mode == "diagnostics" {
    let normal = check_source("print(1)", "previous.wysna")
    let invalid = check_source("let value: bool = 3", "diagnostic.wysna")
}
if mode == "whitespace" {
    let a = check_source("fn f() -> int { let x = 3\nx }\nprint(f())", "a.wysna")
    let b = check_source("# comment\n\nfn f() -> int {\n# body\nlet x = 3\nx\n}\n\nprint(f())", "b.wysna")
    if len(a.bindings) != len(b.bindings) or len(a.nodes) != len(b.nodes) { report_error_at("W9999", first.source, "node identities depend on token offsets") }
    for i in 0..len(a.nodes) {
        if a.nodes[i].kind != b.nodes[i].kind or a.nodes[i].binding != b.nodes[i].binding or a.nodes[i].children != b.nodes[i].children or a.nodes[i].target != b.nodes[i].target { report_error_at("W9999", first.source, "checked identity changed with trivia") }
    }
}
if mode == "shadowing" {
    let scoped = check_source("let x = 1\nif true { let x = 2\nprint(x) }\nprint(x)", "scope.wysna")
    let mut ids: list[int] = []
    for binding in scoped.bindings { if binding.name == "x" { ids.append(len(ids)) } }
    if len(ids) != 2 { report_error_at("W9999", first.source, "missing shadowed declarations") }
    let mut uses: list[int] = []
    for node in scoped.nodes { if node.kind == "binding" { uses.append(node.binding) } }
    if len(uses) != 2 or uses[0] == uses[1] { report_error_at("W9999", first.source, "shadowed names share binding identity") }
    let ignored = mir_module_json(mir_build(scoped))
}
if len(mode) > 4 and slice(mode, 0, 4) == "bad_" {
    let mut broken = first
    let last = len(broken.nodes) - 1
    if mode == "bad_guard_type" {
        broken = check_source("print(match true { true if true => 1, _ => 2 })", "guard.wysna")
        for arm in broken.arms { if arm.guard >= 0 { broken.nodes[arm.guard].type_id = 99999 break } }
    }
    if mode == "bad_unary_type" {
        broken = check_source("let values = [1]\nprint(values)", "list.wysna")
        for i in 0..len(broken.types) { if broken.types[i].kind == "list" { let empty: list[int] = [] broken.types[i].args = empty break } }
    }
    if mode == "bad_child" { broken.nodes[last].children = [last] }
    if mode == "bad_type" { broken.nodes[last].type_id = 99999 }
    if mode == "bad_kind" { broken.nodes[last].kind = "unknown" }
    if mode == "bad_binding" { for i in 0..len(broken.nodes) { if broken.nodes[i].kind == "binding" { broken.nodes[i].binding = 99999 break } } }
    if mode == "bad_target" { for i in 0..len(broken.nodes) { if broken.nodes[i].kind == "call" { broken.nodes[i].target = 99999 break } } }
    if mode == "bad_body" { broken.functions[0].body = 99999 }
    if mode == "bad_parameter" { broken.functions[0].params = [99999] }
    if mode == "bad_signature" { broken.signatures[0].type_id = 99999 }
    if mode == "bad_entry" { broken.entry = [99999] }
    if mode == "bad_literal" { for i in 0..len(broken.nodes) { if broken.nodes[i].kind == "string" { broken.nodes[i].type_id = broken.unit_type break } } }
    if mode == "bad_completed" { broken.has_nodes = false }
    if mode == "bad_arena" { broken.types[len(broken.types) - 1].args = [99999] }
    if mode == "bad_cycle" { broken.types[len(broken.types) - 1].args = [len(broken.types) - 1] }
    if mode == "bad_result" { for i in 0..len(broken.types) { if broken.types[i].kind == "result" { broken.types[i].args = [0] break } } }
    if mode == "bad_immutable" {
        broken = check_source("let mut x = 0\nx = 1", "immutable.wysna")
        broken.bindings[0].mutable = false
    }
    if mode == "bad_return_type" { for i in 0..len(broken.nodes) { if broken.nodes[i].kind == "call" and broken.nodes[i].target >= 0 { broken.nodes[i].type_id = broken.unit_type break } } }
    if mode == "bad_unit" { broken.unit_type = 99999 }
    if mode == "bad_arity" { for i in 0..len(broken.nodes) { if broken.nodes[i].kind == "call" and broken.nodes[i].text == "Ok" { let children: list[int] = []
broken.nodes[i].children = children break } } }
    if mode == "bad_name" { for i in 0..len(broken.nodes) { if broken.nodes[i].kind == "call" and broken.nodes[i].target >= 0 { broken.nodes[i].text = "wrong" break } } }
    let ignored = mir_module_json(mir_build(broken))
}
if mode == "unknown_mir_opcode" {
    let mut module = mir_build(first)
    module.functions[0].blocks[0].operations[0].opcode = "invalid"
    let ignored = mir_emit_c(module)
}
if mode == "missing_method_target" {
    let mut checked = check_source("class Base { fn value() -> int { 1 } }\nclass Derived extends Base { fn value() -> int { super.value() } }\nprint(new Derived().value())", "missing-method.wysna")
    for i in 0..len(checked.records) {
        if checked.types[checked.records[i].type_id].name == "Derived" { checked.records[i].methods[0].target = 99999 }
    }
    let ignored = compile_source(checked)
}
print("verified")
'''


class CompilationContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="wysna-context-")
        cls.directory = Path(cls.temporary.name).resolve()
        source = "".join((ROOT / "selfhost/src" / name).read_text() for name in MODULE_ORDER if name != "09_main.wysna")
        service = CompilerService()
        checked = service.check(source + DRIVER, "compilation-context-test.wysna")
        c_path = cls.directory / "contexts.c"
        c_path.write_text(service.emit_c(checked, backend="ast").source)
        cls.binary = cls.directory / executable_name("contexts")
        flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if os.name != "nt" else []
        built = subprocess.run(compiler_command() + ["-std=c99", "-O0"] + flags + [f"-I{ROOT}", str(c_path), "-lm", "-o", str(cls.binary)],
                               capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_mode(self, mode, *arguments):
        environment = dict(os.environ, WYSNA_MEM_CHECK="1", UBSAN_OPTIONS="halt_on_error=1",
                           ASAN_OPTIONS="detect_leaks=" + ("1" if sys.platform.startswith("linux") else "0"))
        return subprocess.run([str(self.binary), mode, *map(str, arguments)], capture_output=True,
                              text=True, encoding="utf-8", env=environment, timeout=30)

    def test_independent_consumers_and_interleaved_builders(self):
        for mode in ("isolated", "detached_source", "copy", "builders", "whitespace", "shadowing"):
            with self.subTest(mode=mode):
                result = self.run_mode(mode)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "verified\n", ""))

    def test_malformed_checked_programs_are_rejected(self):
        for suffix in ("guard_type", "unary_type", "child", "type", "kind", "binding", "target", "body", "parameter", "signature", "entry", "literal", "completed", "name", "arena", "cycle", "result", "immutable", "return_type", "unit", "arity"):
            with self.subTest(fault=suffix):
                result = self.run_mode("bad_" + suffix)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("[W9999]", result.stderr)
                self.assertNotIn("AddressSanitizer", result.stderr)
                self.assertNotIn("runtime error:", result.stderr)

    def test_declarations_and_diagnostics_are_session_local(self):
        for mode, code, file in (("declarations", "W1001", "unknown.wysna"), ("diagnostics", "W2001", "diagnostic.wysna")):
            with self.subTest(mode=mode):
                result = self.run_mode(mode)
                self.assertEqual(result.returncode, 1)
                self.assertIn("[" + code + "] " + file + ":", result.stderr)
                self.assertNotIn("previous.wysna", result.stderr)

    def test_module_graphs_are_owned_by_each_invocation(self):
        paths = []
        for name, number in (("first", 1), ("second", 2)):
            directory = self.directory / name
            directory.mkdir()
            (directory / "library.wysna").write_text(f"pub fn value() -> int {{ {number} }}\n")
            path = directory / "main.wysna"
            path.write_text('import "library.wysna" as lib\nprint(lib.value())\n')
            paths.append(path)
        result = self.run_mode("imports", *paths)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "verified\n", ""))

    def test_c_emission_rejects_corrupt_checked_inputs(self):
        for mode, message in (("unknown_mir_opcode", "invalid typed MIR operation"),
                              ("missing_method_target", "invalid checked method metadata")):
            with self.subTest(mode=mode):
                result = self.run_mode(mode)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("[W9999]", result.stderr)
                self.assertIn(message, result.stderr)
                self.assertNotIn("AddressSanitizer", result.stderr)
                self.assertNotIn("runtime error:", result.stderr)

    def test_phase_dependencies_do_not_regress(self):
        for path in (ROOT / "selfhost/src").glob("*.wysna"):
            content = path.read_text()
            self.assertNotIn("parse_import_decl", content)
            self.assertNotIn("is_module_name", content)
            self.assertNotRegex(content, r"(?m)^let mut ", str(path))
        for name in ("08_mir.wysna", "08_mir_channels.wysna", "08_mir_lowering.wysna"):
            content = (ROOT / "selfhost/src" / name).read_text()
            self.assertNotRegex(content, r"\b(?:g_pos|g_tokens|sem_mir_\w*|mir_checked_fact|sem_function|sem_kind|advance_tok|at_tok)\b")
        lowering = (ROOT / "selfhost/src/08_mir_lowering.wysna").read_text()
        build_body = lowering[lowering.index("fn mir_build("):lowering.index("fn mir_module_json(")]
        self.assertNotIn(".tokens", build_body)
        model = (ROOT / "selfhost/src/08_checked.wysna").read_text()
        program = model[model.index("struct CheckedProgram"):model.index("struct SemanticScope")]
        self.assertNotIn("tokens:", program)
        self.assertNotIn("CEmissionMetadata", model)
        self.assertNotIn("CEmitter", "".join(path.read_text() for path in (ROOT / "selfhost/src").glob("*.wysna")))


if __name__ == "__main__":
    unittest.main()

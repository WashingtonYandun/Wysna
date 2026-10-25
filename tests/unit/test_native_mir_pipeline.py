"""Native MIR-to-C99 parity, ownership and rejected phase boundaries."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from compiler.CompilerService import CompilerService
from mir.CCodeGen import CCodeGen
from mir.Exchange import import_module

ROOT = Path(__file__).resolve().parents[2]
CASES = [('global',
  'let mut count = 3\n'
  'fn read() -> int { count }\n'
  'fn change() { count = count + 1 }\n'
  'change()\n'
  'print(read())'),
 ('list', 'let values: list[int] = [1, 2, 3]\nprint(values, values[1], len(values))'),
 ('tuple', 'let value = ("hi", 2)\nprint(value, len(value))'),
 ('dict',
  'let values: dict[str, int] = {"a": 1, "b": 2}\n'
  'print(values["b"], values.keys(), values.values())'),
 ('empty', 'let values: list[int] = []\nlet pairs: dict[str, str] = {}\nprint(values, pairs)'),
 ('for', 'let values = ["a", "b", "c"]\nfor value in values { print(value) }'),
 ('range',
  'for value in 1..=4 { if value == 2 { continue } if value == 4 { break } print(value) }'),
 ('nested',
  'let values: list[list[str]] = [["a"], ["b", "c"]]\nfor value in values { print(value[0]) }'),
 ('optional',
  'fn get(value: bool) -> str? { if value { Some("yes") } else { none } }\n'
  'print(get(true) ?? "no", get(false) ?? "no")'),
 ('optional_propagation',
  'fn get(value: str?) -> str? { Some(value?) }\n'
  'print(get(Some("yes")) ?? "no", get(none) ?? "no")'),
 ('destructure', 'for i, value in enumerate(["a", "b"]) { print(i, value) }'),
 ('result',
  'fn get() -> list[int] ! str { Ok([1, 2]) }\nlet values = get() else { [0] }\nprint(values)'),
 ('cow',
  'let mut values = [1, 2]\n'
  'let snapshot = values\n'
  'values.append(3)\n'
  'values[0] = 9\n'
  'print(values, snapshot, values.pop())\n'
  'values.clear()\n'
  'print(values)'),
 ('dict_mutation',
  'let mut values: dict[str, int] = {"a": 1}\n'
  'let snapshot = values\n'
  'values["b"] = 2\n'
  'print(values, snapshot, values.get("missing") ?? 0)'),
 ('nested_mutation',
  'let mut values: list[list[int]] = [[1], [2]]\n'
  'let snapshot = values\n'
  'values[0].append(9)\n'
  'values[1][0] = 3\n'
  'print(values, snapshot)'),
 ('scalar_assignment',
  'fn work() -> int { let mut value = 1\n'
  'while value < 4 { value = value + 1 }\n'
  'value }\n'
  'print(work())'),
 ('struct_order',
  'struct Pair { first: int, second: int }\n'
  'fn mark(value: int) -> int { print(value) value }\n'
  'let pair = Pair { second: mark(2), first: mark(1) }\n'
  'print(pair.first, pair.second)'),
 ('struct_cow',
  'struct Box { values: list[int] }\n'
  'let mut box = Box { values: [1, 2] }\n'
  'let snapshot = box\n'
  'box.values.append(3)\n'
  'box.values[0] = 8\n'
  'print(box.values, snapshot.values)'),
 ('enum',
  'enum Event { Ready, Value(int), Pair(str, int) }\n'
  'print(Event.Ready, Event.Value(7), Event.Pair("ok", 2))'),
 ('nominal_result',
  'struct Error { code: int }\n'
  'fn fail() -> int ! Error { Err(Error { code: 7 }) }\n'
  'print(fail() else error { error.code })'),
 ('range_boundaries',
  'for value in 9223372036854775805..=9223372036854775806 { print(value) }\n'
  'for value in 2..1 { print(value) }')]


CASES.extend([('class',
  'class Counter { value: int\n'
  'fn init(value: int) { this.value = value }\n'
  'fn add(amount: int) -> int { this.value = this.value + amount\n'
  'this.value } }\n'
  'let counter = new Counter(3)\n'
  'print(counter.add(4), counter.value)'),
 ('inheritance',
  'class Base { value: int\n'
  'fn init(value: int) { this.value = value }\n'
  'fn read() -> int { this.value } }\n'
  'class Derived extends Base { extra: int\n'
  'fn init(value: int) { super.init(value)\n'
  'this.extra = 10 }\n'
  'fn read() -> int { super.read() + this.extra } }\n'
  'fn read(value: Base) -> int { value.read() }\n'
  'let child = new Derived(3)\n'
  'print(read(child), child.read(), child.value)'),
 ('interface',
  'interface Reader { fn read() -> int }\n'
  'class Counter implements Reader { value: int\n'
  'fn init(value: int) { this.value = value }\n'
  'fn read() -> int { this.value } }\n'
  'fn read(value: Reader) -> int { value.read() }\n'
  'print(read(new Counter(9)))'),
 ('class_snapshot',
  'class Box { value: int\n'
  'fn init(value: int) { this.value = value } }\n'
  'let mut first = new Box(1)\n'
  'let original = first\n'
  'let second = new Box(2)\n'
  'first.value = (first = second).value\n'
  'print(original.value, first.value)')])


CASES.extend([('foreign_buffer',
  'import "std/buffer"\n'
  'let value = buffer.from_text("hé")\n'
  'print(value.size(), value.get(0) else { 0 }, value.close())'),
 ('integer_error', 'fn fail() -> int ! int { Err(7) }\nprint(fail() else error { error + 1 })')])


CASES.extend([
    ("default_collections", 'class Item { values: list[int], pairs: dict[str, int] }\nlet item = new Item()\nfor value in item.values { print(value) }\nprint(item.values, item.pairs)'),
    ("nested_for", 'for (number, (text, flag)) in [(1, ("yes", true)), (2, ("no", false))] { print(number, text, flag) }'),
    ("empty_nested", 'let values = [[]]\nlet pairs = [{}]\nprint(values, pairs)'),
    ("weak_lifetime", 'class Item { value: int\nfn init(value: int) { this.value = value } }\nfn handle() -> Weak[Item] { weak(new Item(3)) }\nlet item = new Item(7)\nlet reference = weak(item)\nprint((upgrade(reference) ?? new Item(0)).value, upgrade(handle()))'),
    ("lambda_block", 'let work = |value: int| { value + 1 }\nlet answer = || { return 42 }\nprint(work(4), answer())'),
    ("nested_result", 'fn value() -> (int ! str) ! bool { Ok(Err("inner")) }\nfn optional() -> (int ! str)? { Some(Ok(3)) }\nprint(value(), optional())'),
    ("generic_identity", 'fn identity[T](value: T) -> T { value }\nprint(identity(4), identity("hello"), identity[int](7))'),
    ("generic_operator", 'fn twice[T](value: T) -> T { value + value }\nprint(twice(4), twice("hello"))'),
    ("generic_recursion", 'fn keep[T](value: T, count: int) -> T { if count == 0 { value } else { keep(value, count - 1) } }\nprint(keep("yes", 3))'),
    ("generic_forward", 'fn first[T](value: T) -> T { second(value) }\nfn second[T](value: T) -> T { value }\nprint(first(9))'),
    ("unused_generic", 'fn add[T](value: T) -> T { value + value }\nprint("unused")'),
    ("function_ref", 'fn twice(value: int) -> int { value * 2 }\nlet work = twice\nprint(work(3))'),
    ("lambda", 'let work = |value: int| => value * 3\nprint(work(4))'),
    ("closure_snapshot", 'let mut text = "before"\nlet work = || => text\ntext = "after"\nprint(work(), text)'),
    ("nested_closure", 'fn factory(text: str) -> fn() -> fn() -> str { || => || => text }\nlet outer = factory("hello")\nlet inner = outer()\nprint(inner())'),
    ("higher_order", 'fn apply(work: fn(int) -> int, value: int) -> int { work(value) }\nprint(apply(|value| => value + 1, 8))'),
    ("closure_result", 'let work: fn(int) -> int ! str = |value| -> int ! str { if value > 0 { Ok(value) } else { Err("bad") } }\nprint(work(4) else { 0 }, work(0) else { 0 })'),
    ("match_bool", 'fn label(value: bool) -> str { match value { true => "yes", false => "no" } }\nprint(label(true), label(false))'),
    ("match_string", 'fn label(value: str) -> int { match value { "yes" => 1, _ => 2 } }\nprint(label("yes"), label("other"))'),
    ("match_negative", 'let value = -15\nprint(match value { -15 => "yes", _ => "no" })'),
    ("match_optional", 'fn label(value: str?) -> str { match value { Some(text) => text, None => "no" } }\nprint(label(Some("yes")), label(none))'),
    ("match_result", 'fn label(value: int ! str) -> str { match value { Ok(number) => str(number), Err(error) => error } }\nprint(label(Ok(7)), label(Err("bad")))'),
    ("match_enum", 'enum Event { None, Value(int), Pair(str, int) }\nfn label(event: Event) -> str { match event { Event.None => "none", Event.Value(number) => str(number), Event.Pair(text, number) => text + str(number) } }\nprint(label(Event.None), label(Event.Value(3)), label(Event.Pair("v", 4)))'),
    ("match_tuple_guard", 'let value = (("hello", 3), true)\nprint(match value { ((text, number), true) if number > 5 => text, ((text, number), true) => text + str(number), _ => "no" })'),
    ("match_error_union", 'fn fail() -> str ! int | str { Err(7) }\nprint(fail() else error { match error { int(code) => str(code), str(text) => text } })'),
])


CASES.extend([
    ('nested_generic_initializer_call', 'struct Box[T] { value: T }\nfn twice[T](value: T) -> T { value + value }\nlet offset = 4\nlet work = |value: int| => twice(value) + offset\nlet value: Box[int] = Box { value: work(3) }\nprint(value.value)'),
    ('nested_generic_initializer_literal', 'struct Pair[T] { first: T, second: T }\nstruct Box[T] { value: T }\nlet value = Pair { first: Box { value: 1 }, second: Box { value: 2 } }\nprint(value.first.value, value.second.value)'),
    ('generic_nominal_display', 'struct Box[T] { value: T }\nlet value: Box[int] = Box { value: 3 }\nprint(value)'),
    ('generic_nominal_name_collision', 'struct Box[T] { value: T }\nstruct Box__0 { text: str }\nlet value: Box[int] = Box { value: 3 }\nlet ordinary = Box__0 { text: "distinct" }\nprint(value.value, ordinary.text)'),
])


class NativeMirPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = ensure_selfhost_compiler_binary(str(ROOT))

    def invoke(self, command, source):
        result = subprocess.run([self.compiler, command, str(source)], capture_output=True,
                                text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def execute(self, source, directory, engine):
        c_path = directory / (engine + ".c")
        executable = directory / executable_name(engine)
        c_path.write_text(source, encoding="utf-8")
        command = compiler_command()
        flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if os.name != "nt" else []
        built = subprocess.run(command + ["-std=c99", "-O1"] + flags +
                               ["-I" + str(ROOT), str(c_path), "-lm", "-o", str(executable)],
                               capture_output=True, text=True, timeout=120)
        self.assertEqual(built.returncode, 0, built.stderr)
        environment = dict(os.environ, WYSNA_MEM_CHECK="1", UBSAN_OPTIONS="halt_on_error=1")
        completed = subprocess.run([str(executable)], capture_output=True, env=environment, timeout=20)
        self.assertNotIn(b"runtime error:", completed.stderr)
        self.assertNotIn(b"AddressSanitizer", completed.stderr)
        return completed.returncode, completed.stdout, completed.stderr

    def test_native_emitter_matches_independent_consumers(self):
        with tempfile.TemporaryDirectory(prefix="wysna-native-mir-") as temporary:
            directory = Path(temporary).resolve()
            source = directory / "source ü.wysna"
            for name, code in CASES:
                with self.subTest(case=name):
                    source.write_text(code + "\n", encoding="utf-8")
                    self.assertEqual(self.invoke("--check-mir", source), "")
                    payload = self.invoke("--emit-mir", source)
                    module, arena = import_module(payload)
                    native = self.invoke("--emit-c-mir", source)
                    service = CompilerService()
                    checked = service.check_file(source)
                    seed = service.emit_c(checked).source
                    expected = self.execute(seed, directory, "seed")
                    self.assertEqual(expected[0], 0, expected[2])
                    self.assertEqual(self.execute(CCodeGen(arena).generate(module), directory, "exchange"), expected)
                    self.assertEqual(self.execute(native, directory, "native"), expected)

    def test_runtime_errors_match_independent_consumers(self):
        cases = [
            ("for value in 9223372036854775806..=9223372036854775807 { print(value) }", "W4003"),
            ("let values = [1]\nprint(values[2])", "W4002"),
            ("fn divide(value: int) -> int { value / 0 }\nprint(divide(1))", "W4001"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            source = directory / "panic.wysna"
            for code, diagnostic in cases:
                with self.subTest(source=code):
                    source.write_text(code, encoding="utf-8")
                    module, arena = import_module(self.invoke("--emit-mir", source))
                    service = CompilerService()
                    checked = service.check_file(source)
                    expected = self.execute(service.emit_c(checked).source, directory, "seed")
                    self.assertNotEqual(expected[0], 0)
                    self.assertIn(("[" + diagnostic + "]").encode(), expected[2])
                    self.assertEqual(self.execute(CCodeGen(arena).generate(module), directory, "exchange"), expected)
                    self.assertEqual(self.execute(self.invoke("--emit-c-mir", source), directory, "native"), expected)

    def test_default_collection_fields_fail_safely(self):
        cases = [
            "class Item { values: tuple[int, int] }\nlet item = new Item()\nfor a, b in [item.values] { print(a, b) }",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            source = directory / "uninitialized.wysna"
            for code in cases:
                with self.subTest(source=code):
                    source.write_text(code, encoding="utf-8")
                    service = CompilerService()
                    checked = service.check_file(source)
                    module, arena = import_module(self.invoke("--emit-mir", source))
                    emissions = {
                        "hir": service.emit_c(checked).source,
                        "ast": service.emit_c(checked, backend="ast").source,
                        "exchange": CCodeGen(arena).generate(module),
                        "native": self.invoke("--emit-c-mir", source),
                    }
                    for engine, emission in emissions.items():
                        with self.subTest(engine=engine):
                            status, output, error = self.execute(emission, directory, engine)
                            self.assertNotEqual(status, 0)
                            self.assertEqual(output, b"")
                            self.assertIn(b"[W4003]", error)
                            self.assertIn(str(source).encode("utf-8"), error)

    def test_native_phase_rejects_invalid_source(self):
        cases = [
            ('class Box[T] { value: T }', 'W2013'),
            ('interface Action[T] { fn apply(value: T) -> T }', 'W2013'),
            ('enum Maybe[T] { None, Value(T) }\nprint(Maybe.None)', 'W2013'),
            ('let values = [1, "bad"]', "W2001"),
            ('let values = []', "W2001"),
            ('fn bad[T](value: T) -> T { missing }', "W1001"),
            ('fn bad[T](value: T) -> T { true + false }', "W2001"),
            ('fn twice[T](value: T) -> T { value + value }\nprint(twice(true))', "W2001"),
            ('let value = 1\nlet callback = || { value = 2 }', "W3001"),
            ('for (value, (value, other)) in [(1, (2, 3))] { print(value) }', "W1002"),
            ('let callback = || { if true { return 1 } "bad" }', "W2004"),
            ('let values: list[int] = []\nvalues.append(1)', "W3001"),
            ('fn value() -> str? { none }\nprint(value() else error { "bad" })', "W2006"),
            ('fn value() -> list[int] ! str { Ok([1]) }\nprint(value() else { [] })', "W2001"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "invalid.wysna"
            for code, diagnostic in cases:
                with self.subTest(source=code):
                    source.write_text(code, encoding="utf-8")
                    for command in ("--check-mir", "--emit-mir", "--emit-c-mir"):
                        result = subprocess.run([self.compiler, command, str(source)], capture_output=True,
                                                text=True, timeout=120)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("[" + diagnostic + "]", result.stderr)
                        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()

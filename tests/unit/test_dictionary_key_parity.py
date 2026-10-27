"""Dictionary key semantics and MIR metadata agree across compiler engines."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from compiler.CompilerService import CompilerService
from mir.Exchange import import_module
from tests.engine_harness import verify_case

ROOT = Path(__file__).resolve().parents[2]


class DictionaryKeyParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import ensure_selfhost_compiler_binary

        cls.compiler = ensure_selfhost_compiler_binary(str(ROOT))

    def test_valid_enum_keys_preserve_equality_and_hash(self):
        cases = [
            ("""struct Holder { value: dict[Outer, int] }
enum Outer { Wrapped(Inner), Empty }
enum Inner { Entry(int, str, bool) }
let mut values: dict[Outer, int] = {}
values[Outer.Wrapped(Inner.Entry(7, "x", true))] = 1
values[Outer.Wrapped(Inner.Entry(7, "x", true))] = 9
print(values[Outer.Wrapped(Inner.Entry(7, "x", true))], len(values))
""", "nested_forward"),
            ("""enum Key[T] { Item(T) }
let mut values: dict[Key[int], int] = {}
values[Key.Item(7)] = 1
values[Key.Item(7)] = 9
print(values[Key.Item(7)], len(values))
""", "generic"),
        ]
        with tempfile.TemporaryDirectory(prefix="wysna-dict-key-valid-") as temporary:
            for source, name in cases:
                with self.subTest(case=name):
                    ok, detail, report = verify_case(
                        source, name + ".wysna", self.compiler, temporary,
                        expected="9 1\n", strict=True,
                    )
                    self.assertTrue(ok, detail)
                    self.assertEqual(len(report["results"]), 4)

    def test_invalid_key_types_are_rejected_by_all_frontends(self):
        cases = {
            "forward": "struct Holder { value: dict[Key, int] }\nenum Key { Item(list[int]) }",
            "declared_first": "enum Key { Item(list[int]) }\nstruct Holder { value: dict[Key, int] }",
            "nested": "enum Outer { Item(Inner) }\nstruct Holder { value: dict[Outer, int] }\nenum Inner { Item(float) }",
            "optional": "let values: dict[Option[int], int] = {}",
            "optional_payload": "enum Key { Item(Option[int]) }\nlet x: Option[int] = none\nlet values = {Key.Item(x): 1}",
            "generic": "enum Key[T] { Item(T) }\nstruct Holder { value: dict[Key[list[int]], int] }",
        }
        with tempfile.TemporaryDirectory(prefix="wysna-dict-key-invalid-") as temporary:
            for name, source in cases.items():
                with self.subTest(case=name):
                    ok, detail, report = verify_case(
                        source, name + ".wysna", self.compiler, temporary,
                        strict=True, expect_rejection=True,
                    )
                    self.assertTrue(ok, detail)
                    self.assertEqual(len(report["results"]), 4)
                    for result in report["results"]:
                        self.assertEqual(result["diag_code"], "W2010", result)
                        self.assertFalse(result["infrastructure_error"], result)

    def test_seed_and_selfhost_mir_export_correct_key_capabilities(self):
        source = """enum Valid { Item(int), Empty }
enum Invalid { Item(list[int]) }
enum Nested { Item(Invalid) }
print(1)
"""
        expected = {"Valid": True, "Invalid": False, "Nested": False}
        with tempfile.TemporaryDirectory(prefix="wysna-dict-key-mir-") as temporary:
            path = Path(temporary) / "keys.wysna"
            path.write_text(source, encoding="utf-8")
            service = CompilerService()
            seed = service.export_mir(service.check_file(path))
            compiled = subprocess.run(
                [self.compiler, "--emit-mir", str(path)], cwd=ROOT,
                capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            for producer, payload in (("seed", seed), ("selfhost", compiled.stdout)):
                with self.subTest(producer=producer):
                    envelope = json.loads(payload)
                    arena = envelope["type_arena"]
                    ids = {entry["data"]: index for index, entry in enumerate(arena["entries"])
                           if entry["tag"] == "enum" and entry["data"] in expected}
                    self.assertEqual(set(ids), set(expected))
                    for name, valid in expected.items():
                        self.assertEqual(arena["capabilities"][ids[name]]["is_valid_dict_key"], valid)
                    import_module(payload)

                    # Version 1 snapshots produced before key validation may
                    # contain stale bits; import must derive them from payloads.
                    for name in expected:
                        arena["capabilities"][ids[name]]["is_valid_dict_key"] = not expected[name]
                    _, restored = import_module(json.dumps(envelope))
                    for name, valid in expected.items():
                        self.assertEqual(restored.get_capabilities(ids[name]).is_valid_dict_key, valid)


if __name__ == "__main__":
    unittest.main()

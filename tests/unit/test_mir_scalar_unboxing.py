"""Verify that C99 emission from verified MIR avoids redundant scalar boxing/unboxing."""
import unittest
from compiler.CompilerService import CompilerService


class MirScalarUnboxingTests(unittest.TestCase):
    def setUp(self):
        self.service = CompilerService()

    def test_scalar_constant_and_return_emit_unboxed_c(self):
        source = (
            "fn compute(x: int) -> int {\n"
            "    let a = 42;\n"
            "    let b = x;\n"
            "    return a;\n"
            "}\n"
        )
        checked = self.service.check(source)
        emitted = self.service.emit_c(checked).source

        # Must not contain boxing wrapper around integer literal
        self.assertNotIn("wysna_as_int(wysna_int(42LL))", emitted)
        self.assertIn("42LL;", emitted)

        # Must directly return slot without boxing roundtrip
        self.assertNotIn("return wysna_int(", emitted)
        self.assertRegex(emitted, r"return _m\d+;")

    def test_scalar_boolean_emits_unboxed_c(self):
        source = (
            "fn is_valid(flag: bool) -> bool {\n"
            "    let constant_true = true;\n"
            "    let copied = flag;\n"
            "    return constant_true;\n"
            "}\n"
        )
        checked = self.service.check(source)
        emitted = self.service.emit_c(checked).source

        self.assertNotIn("wysna_as_bool(wysna_bool(true))", emitted)
        self.assertIn("true;", emitted)
        self.assertNotIn("return wysna_bool(", emitted)
        self.assertRegex(emitted, r"return _m\d+;")

    def test_scalar_float_emits_unboxed_c(self):
        source = (
            "fn get_ratio() -> float {\n"
            "    let pi = 3.14159;\n"
            "    return pi;\n"
            "}\n"
        )
        checked = self.service.check(source)
        emitted = self.service.emit_c(checked).source

        self.assertNotIn("wysna_as_float(wysna_float(3.14159))", emitted)
        self.assertIn("3.14159;", emitted)
        self.assertNotIn("return (wysna_float(", emitted)
        self.assertRegex(emitted, r"return _m\d+;")


if __name__ == "__main__":
    unittest.main()

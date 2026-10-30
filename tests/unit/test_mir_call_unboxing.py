"""Verify that user function calls with scalar arguments pass direct unboxed C identifiers."""
import unittest
from compiler.CompilerService import CompilerService


class MirCallUnboxingTests(unittest.TestCase):
    def setUp(self):
        self.service = CompilerService()

    def test_direct_call_passes_unboxed_scalar_arguments(self):
        source = (
            "fn add(a: int, b: int) -> int {\n"
            "    return a + b;\n"
            "}\n"
            "fn caller() -> int {\n"
            "    let x = 10;\n"
            "    let y = 20;\n"
            "    return add(x, y);\n"
            "}\n"
        )
        checked = self.service.check(source)
        emitted = self.service.emit_c(checked).source

        # Must not box arguments before function call
        self.assertNotIn("wysna_fn_add((wysna_int(", emitted)
        self.assertRegex(emitted, r"wysna_fn_add\(_m\d+, _m\d+\);")

    def test_direct_call_passes_float_and_bool_unboxed(self):
        source = (
            "fn combine(rate: float, active: bool) -> float {\n"
            "    return if active { rate * 2.0 } else { rate };\n"
            "}\n"
            "fn caller() -> float {\n"
            "    let r = 1.5;\n"
            "    let flag = true;\n"
            "    return combine(r, flag);\n"
            "}\n"
        )
        checked = self.service.check(source)
        emitted = self.service.emit_c(checked).source

        self.assertNotIn("wysna_fn_combine((wysna_float(", emitted)
        self.assertNotIn("wysna_fn_combine((wysna_bool(", emitted)
        self.assertRegex(emitted, r"wysna_fn_combine\(_m\d+, _m\d+\);")


if __name__ == "__main__":
    unittest.main()

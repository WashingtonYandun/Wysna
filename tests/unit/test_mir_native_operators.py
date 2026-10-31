"""Verify that C99 emission lowers scalar arithmetic and relational operations directly to native C99 operators."""
import unittest
from compiler.CompilerService import CompilerService


class MirNativeOperatorsTests(unittest.TestCase):
    def setUp(self):
        self.service = CompilerService()

    def test_integer_arithmetic_emits_native_c_operators(self):
        source = (
            "fn math_ops(a: int, b: int) -> int {\n"
            "    let sum = a + b;\n"
            "    let diff = a - b;\n"
            "    let prod = a * b;\n"
            "    let neg = -a;\n"
            "    return sum + diff + prod + neg;\n"
            "}\n"
        )
        checked = self.service.check(source)
        emitted = self.service.emit_c(checked).source

        self.assertNotIn("wysna_add(wysna_int", emitted)
        self.assertNotIn("wysna_sub(wysna_int", emitted)
        self.assertNotIn("wysna_mul(wysna_int", emitted)
        self.assertNotIn("wysna_neg(wysna_int", emitted)

        self.assertIn("(int64_t)((uint64_t)", emitted)
        self.assertIn("(0ULL - (uint64_t)", emitted)

    def test_float_arithmetic_emits_native_c_operators(self):
        source = (
            "fn float_ops(a: float, b: float) -> float {\n"
            "    let sum = a + b;\n"
            "    let diff = a - b;\n"
            "    let prod = a * b;\n"
            "    let neg = -a;\n"
            "    return sum + diff + prod + neg;\n"
            "}\n"
        )
        checked = self.service.check(source)
        emitted = self.service.emit_c(checked).source

        self.assertNotIn("wysna_add(wysna_float", emitted)
        self.assertNotIn("wysna_sub(wysna_float", emitted)
        self.assertNotIn("wysna_mul(wysna_float", emitted)
        self.assertNotIn("wysna_neg(wysna_float", emitted)

    def test_relational_and_boolean_ops_emit_native_c_operators(self):
        source = (
            "fn compare(a: int, b: int, flag: bool) -> bool {\n"
            "    let lt = a < b;\n"
            "    let le = a <= b;\n"
            "    let gt = a > b;\n"
            "    let ge = a >= b;\n"
            "    let eq = a == b;\n"
            "    let ne = a != b;\n"
            "    let inverted = not flag;\n"
            "    return lt and le and gt and ge and eq and ne and inverted;\n"
            "}\n"
        )
        checked = self.service.check(source)
        emitted = self.service.emit_c(checked).source

        self.assertNotIn("wysna_lt(", emitted)
        self.assertNotIn("wysna_le(", emitted)
        self.assertNotIn("wysna_gt(", emitted)
        self.assertNotIn("wysna_ge(", emitted)
        self.assertNotIn("wysna_not(", emitted)

    def test_string_concatenation_preserves_runtime_helper(self):
        source = (
            "fn concat(a: str, b: str) -> str {\n"
            "    return a + b;\n"
            "}\n"
        )
        checked = self.service.check(source)
        emitted = self.service.emit_c(checked).source

        self.assertIn("wysna_add(", emitted)


if __name__ == "__main__":
    unittest.main()

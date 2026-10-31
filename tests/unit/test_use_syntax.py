"""Comprehensive unit test suite for the AI-predictable import syntax (from ... use ...)."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeToolchain
from compiler.LlvmToolchain import LlvmToolchain
from error.Error import TypeError
from parser.Parser import Parser
from formatter.Formatter import Formatter
from tests.formatter_contract import verify_formatting


class TestUseSyntax(unittest.TestCase):
    def setUp(self):
        self.service = CompilerService()
        self.repo_root = Path(__file__).resolve().parent.parent.parent

    def test_parse_and_format_simple_use(self):
        source = "from app.domain.user use User\n"
        formatted = verify_formatting(source)
        self.assertEqual(formatted, source)

    def test_parse_and_format_alias_use(self):
        source = "from app.domain.user use User as DomainUser\n"
        formatted = verify_formatting(source)
        self.assertEqual(formatted, source)

    def test_parse_and_format_braced_vertical(self):
        source = (
            "from app.infrastructure.database use {\n"
            "    Connection,\n"
            "    QueryBuilder,\n"
            "    Transaction,\n"
            "}\n"
        )
        formatted = verify_formatting(source)
        self.assertEqual(formatted, source)

    def test_parse_and_format_submodules(self):
        source = (
            "from app.domain use {\n"
            "    auth.Role,\n"
            "    user.User,\n"
            "    user.UserRepository,\n"
            "}\n"
        )
        formatted = verify_formatting(source)
        self.assertEqual(formatted, source)

    def test_collision_resolution_with_alias(self):
        self.service.set_overlay("/tmp/app/domain/user.wysna", """
pub struct User { name: str }
pub fn get_name(u: User) -> str { return u.name }
""")
        self.service.set_overlay("/tmp/app/infrastructure/auth.wysna", """
pub struct User { email: str }
pub fn get_email(u: User) -> str { return u.email }
""")

        src = """
from app.domain.user use { User as DomainUser, get_name }
from app.infrastructure.auth use { User as AuthUser, get_email }

let du = DomainUser { name: "Washo" }
let au = AuthUser { email: "washo@wysofia.com" }
print(get_name(du), get_email(au))
"""
        checked = self.service.check(src, file="/tmp/test_collision.wysna")

        with tempfile.TemporaryDirectory() as tmpdir:
            # C Backend
            c_out = Path(tmpdir) / "c_collision"
            c_res = self.service.emit_c(checked)
            NativeToolchain(self.repo_root).build(c_res.source, c_out)
            res_c = subprocess.run([str(c_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_c.stdout.strip(), "Washo washo@wysofia.com")

            # LLVM Backend
            llvm_out = Path(tmpdir) / "llvm_collision"
            llvm_res = self.service.emit_llvm(checked)
            LlvmToolchain(self.repo_root).build(llvm_res, llvm_out)
            res_llvm = subprocess.run([str(llvm_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_llvm.stdout.strip(), "Washo washo@wysofia.com")

    def test_submodule_extraction_native(self):
        self.service.set_overlay("/tmp/app/domain/user.wysna", """
pub struct User { id: int }
pub fn get_user_id(u: User) -> int { return u.id }
""")
        self.service.set_overlay("/tmp/app/domain/auth.wysna", """
pub struct Role { level: int }
pub fn get_role_level(r: Role) -> int { return r.level }
""")

        submod_src = """
from app.domain use { user.User, user.get_user_id, auth.Role, auth.get_role_level }

let u = User { id: 42 }
let r = Role { level: 99 }
let uid = get_user_id(u)
let rlvl = get_role_level(r)
print(uid + rlvl)
"""
        checked = self.service.check(submod_src, file="/tmp/test_submod.wysna")

        with tempfile.TemporaryDirectory() as tmpdir:
            c_out = Path(tmpdir) / "c_submod"
            c_res = self.service.emit_c(checked)
            NativeToolchain(self.repo_root).build(c_res.source, c_out)
            res_c = subprocess.run([str(c_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_c.stdout.strip(), "141")

            llvm_out = Path(tmpdir) / "llvm_submod"
            llvm_res = self.service.emit_llvm(checked)
            LlvmToolchain(self.repo_root).build(llvm_res, llvm_out)
            res_llvm = subprocess.run([str(llvm_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_llvm.stdout.strip(), "141")

    def test_whole_module_namespace_import(self):
        self.service.set_overlay("/tmp/app/domain/user.wysna", """
pub fn calculate(n: int) -> int { return n * 3 }
""")
        ns_src = """
from app.domain use user

let res = user.calculate(11)
print(res)
"""
        checked = self.service.check(ns_src, file="/tmp/test_ns.wysna")

        with tempfile.TemporaryDirectory() as tmpdir:
            c_out = Path(tmpdir) / "c_ns"
            c_res = self.service.emit_c(checked)
            NativeToolchain(self.repo_root).build(c_res.source, c_out)
            res_c = subprocess.run([str(c_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_c.stdout.strip(), "33")

            llvm_out = Path(tmpdir) / "llvm_ns"
            llvm_res = self.service.emit_llvm(checked)
            LlvmToolchain(self.repo_root).build(llvm_res, llvm_out)
            res_llvm = subprocess.run([str(llvm_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_llvm.stdout.strip(), "33")

    def test_stdlib_dot_path_import(self):
        src = """
from std.text use { contains, to_lower }

let s = to_lower("WYSOFIA")
let has_sofia = contains(s, "sofia")
if has_sofia {
    print("found sofia")
} else {
    print("not found")
}
"""
        checked = self.service.check(src, file="/tmp/test_stdlib.wysna")

        with tempfile.TemporaryDirectory() as tmpdir:
            c_out = Path(tmpdir) / "c_stdlib"
            c_res = self.service.emit_c(checked)
            NativeToolchain(self.repo_root).build(c_res.source, c_out)
            res_c = subprocess.run([str(c_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_c.stdout.strip(), "found sofia")

            llvm_out = Path(tmpdir) / "llvm_stdlib"
            llvm_res = self.service.emit_llvm(checked)
            LlvmToolchain(self.repo_root).build(llvm_res, llvm_out)
            res_llvm = subprocess.run([str(llvm_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_llvm.stdout.strip(), "found sofia")

    def test_diagnostic_w1003_with_export_hints(self):
        self.service.set_overlay("/tmp/app/domain/user.wysna", """
pub struct User { id: int }
pub fn get_user_id(u: User) -> int { return u.id }
""")
        bad_src = """
from app.domain.user use NonExistent
"""
        with self.assertRaises(TypeError) as ctx:
            self.service.check(bad_src, file="/tmp/test_bad.wysna")
        err_msg = str(ctx.exception)
        self.assertIn("W1003", err_msg)
        self.assertIn("Available exports: User, get_user_id", err_msg)

    def test_backward_compatibility_legacy_import(self):
        self.service.set_overlay("/tmp/legacy/math_lib.wysna", """
pub fn square(x: int) -> int { return x * x }
""")
        src = """
import "/tmp/legacy/math_lib.wysna" as math

let s = math.square(7)
print(s)
"""
        checked = self.service.check(src, file="/tmp/test_legacy.wysna")

        with tempfile.TemporaryDirectory() as tmpdir:
            c_out = Path(tmpdir) / "c_legacy"
            c_res = self.service.emit_c(checked)
            NativeToolchain(self.repo_root).build(c_res.source, c_out)
            res_c = subprocess.run([str(c_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_c.stdout.strip(), "49")

            llvm_out = Path(tmpdir) / "llvm_legacy"
            llvm_res = self.service.emit_llvm(checked)
            LlvmToolchain(self.repo_root).build(llvm_res, llvm_out)
            res_llvm = subprocess.run([str(llvm_out)], capture_output=True, text=True, check=True)
            self.assertEqual(res_llvm.stdout.strip(), "49")


if __name__ == "__main__":
    unittest.main()

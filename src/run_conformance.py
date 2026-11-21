import concurrent.futures
import glob
import hashlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent
_ROOT = _SRC.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if (_ROOT / "tests").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if __name__ in ("run_conformance", "src.run_conformance"):
    _self_mod = sys.modules.get(__name__)
    if _self_mod is not None:
        sys.modules.setdefault("run_conformance", _self_mod)
        if (_ROOT / "src" / "run_conformance.py").resolve() == Path(__file__).resolve():
            sys.modules.setdefault("src.run_conformance", _self_mod)

from lexer.Lexer import Lexer
from parser.Parser import Parser
from checker.TypeChecker import TypeChecker
from runtime.Interpreter import Interpreter
from codegen.CCodeGen import CCodeGen
from hir.HirBuilder import HirBuilder
from hir.HirNodes import HirModule
from hir.HirCCodeGen import HirCCodeGen
from tests.engine_harness import PROFILES, panic_expectation, unsupported_reason
from tests.unit.test_selfhost_split_conformance import test_split_conformance
from tests.unit.test_generic_hardening import run_generic_hardening_tests
from tests.unit.test_cow_telemetry import run_cow_telemetry_suite
from tests.unit.test_compilation_session import run_session_tests
from tests.unit.test_compiler_service import run_compiler_service_tests
from tests.unit.test_native_toolchain import run_native_toolchain_tests
from tests.unit.test_resolved_hir import run_all as run_resolved_hir_tests
from tests.unit.test_c03_generic_specialization import run_all as run_c03_generic_tests
from tests.unit.test_c04_adt_layout import run_all as run_c04_adt_tests
from tests.unit.test_c05_types_inference import run_all as run_c05_types_tests
from tests.unit.test_c06_default_backend import run_all as run_c06_backend_tests
from tests.unit.test_d01_parity_matrix import run_all as run_d01_parity_tests
from error.Error import DIAGNOSTIC_CODES, LexerError, ParserError, TypeError, WysnaError, WysnaRuntimeError
from tokens.TokenType import TokenType
from node.NodeType import Program


def _check_expected_diag_code(code_text: str, err: WysnaError) -> None:
    assert err.code in DIAGNOSTIC_CODES, f"Invalid diagnostic code {err.code!r}"
    for line in code_text.splitlines():
        s = line.strip()
        if s.startswith("# ERROR:"):
            expected_code = s.split(":", 1)[1].strip()
            assert err.code == expected_code, f"Expected diagnostic code {expected_code}, got {err.code}"


def _assert_native_outcome(name, result, expected, panic_code):
    """Checks one native run against the fixture contract.

    A normal fixture must exit 0 and reproduce its expected stdout. A panic fixture
    (`# RUNTIME_PANIC: Wxxxx`) must exit non-zero and report that exact code on stderr.
    """
    if panic_code is not None:
        assert result.returncode != 0, f"{name}: expected {panic_code} panic, exited 0"
        assert panic_code in result.stderr, (
            f"{name}: expected {panic_code} on stderr, got {result.stderr!r}")
        return
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected, f"{name}: expected {expected!r}, got {result.stdout!r}"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


SUITE_DOMAINS = {
    "checker": [
        "tests/unit/test_types_conformance.py",
        "tests/unit/test_c03_generic_specialization.py",
        "tests/unit/test_c04_adt_layout.py",
        "tests/unit/test_c05_types_inference.py",
        "tests/unit/test_f03_error_unions.py",
        "tests/unit/test_generic_hardening.py",
        "tests/unit/test_strong_reference_cycles.py",
        "tests/unit/test_use_syntax.py",
    ],
    "mir": [
        "tests/unit/test_mir.py",
        "tests/unit/test_mir_ownership.py",
        "tests/unit/test_mir_optimizer.py",
        "tests/unit/test_mir_scalar_unboxing.py",
        "tests/unit/test_mir_call_unboxing.py",
        "tests/unit/test_mir_exchange.py",
        "tests/unit/test_resolved_hir.py",
        "tests/unit/test_hir_return_paths.py",
    ],
    "selfhost": [
        "tests/unit/test_selfhost_mir.py",
        "tests/unit/test_selfhost_split_conformance.py",
        "tests/unit/test_selfhost_compilation_context.py",
        "tests/unit/test_selfhost_diagnostics.py",
        "tests/unit/test_selfhost_source_provenance.py",
        "tests/unit/test_selfhost_generic_functions.py",
    ],
    "tooling": [
        "tests/unit/test_agent_diagnostics.py",
        "tests/unit/test_language_server.py",
        "tests/unit/test_tool_result.py",
        "tests/unit/test_compiler_service.py",
        "tests/unit/test_compilation_session.py",
        "tests/unit/test_effect_analysis.py",
        "tests/unit/test_capability_policy.py",
        "tests/unit/test_formatter_conformance.py",
        "tests/unit/test_project_scaffold.py",
        "tests/unit/test_semantic_graph_and_zerolang_gap.py",
    ],
    "stdlib": [
        "tests/unit/test_standard_toolkit.py",
        "tests/unit/test_foundations.py",
        "tests/unit/test_crypto.py",
        "tests/unit/test_http_client.py",
        "tests/unit/test_unicode_normalization.py",
    ],
    "contract": [
        "tests/unit/test_architecture_contract.py",
        "tests/unit/test_ci_harness.py",
        "tests/unit/test_audit_findings.py",
        "tests/unit/test_audit_remediation.py",
        "tests/unit/test_native_toolchain.py",
        "tests/unit/test_distribution_contract.py",
        "tests/unit/test_expansion_contracts.py",
    ],
}


def run_suite(*, fast: bool = False, suite: str | None = None) -> None:
    file_path = Path(__file__).resolve()
    if file_path.parent.name == "src":
        repo_root = str(file_path.parent.parent)
    else:
        repo_root = str(file_path.parent)
    tests_dir = Path(repo_root) / "tests"
    compiler_src = Path(repo_root) / "selfhost" / "compiler.wysna"
    if not tests_dir.is_dir() or not compiler_src.is_file():
        print(
            f"Conformance suite requires a source checkout (missing fixtures under {repo_root})",
            file=sys.stderr,
        )
        sys.exit(1)
    prev_cwd = os.getcwd()
    os.chdir(repo_root)
    try:
        if suite is not None:
            _run_domain_suite(repo_root, suite)
        elif fast:
            _run_fast_suite(repo_root)
        else:
            _run_suite_in_repo(repo_root)
    finally:
        os.chdir(prev_cwd)


def _run_domain_suite(repo_root: str, suite: str) -> None:
    if suite not in SUITE_DOMAINS:
        print(f"Unknown test suite '{suite}'. Valid suites: {', '.join(sorted(SUITE_DOMAINS))}", file=sys.stderr)
        sys.exit(2)
    targets = [p for p in SUITE_DOMAINS[suite] if os.path.exists(os.path.join(repo_root, p))]
    print(f"Running Wysna domain suite '{suite}' ({len(targets)} modules)...")
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", *targets], cwd=repo_root)
    sys.exit(res.returncode)


def _run_fast_suite(repo_root: str) -> None:
    import time
    t0 = time.perf_counter()
    print("==================================================")
    print("Wysna Fast Local Suite (--fast: in-process core & contracts)")
    print("==================================================")
    _run_inprocess_core_steps(repo_root)
    fast_modules = [
        "tests/unit/test_architecture_contract.py",
        "tests/unit/test_ci_harness.py",
        "tests/unit/test_effect_analysis.py",
        "tests/unit/test_agent_diagnostics.py",
        "tests/unit/test_compiler_service.py",
        "tests/unit/test_selfhost_split_conformance.py",
    ]
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", *fast_modules], cwd=repo_root)
    elapsed = time.perf_counter() - t0
    print(f"Fast suite finished in {elapsed:.2f}s")
    sys.exit(res.returncode)


def _run_inprocess_core_steps(repo_root: str) -> tuple:
    print("==================================================")
    print("Wysna Conformance Runner (SPEC v0.1)")
    print("==================================================")

    # 1. Lexer Tests
    valid_lexer = "tests/lexer/valid"
    invalid_lexer = "tests/lexer/invalid"

    v_lex_files = sorted([f for f in os.listdir(valid_lexer) if f.endswith(".wysna")]) if os.path.exists(valid_lexer) else []
    i_lex_files = sorted([f for f in os.listdir(invalid_lexer) if f.endswith(".wysna")]) if os.path.exists(invalid_lexer) else []

    v_lex_passed = 0
    for f in v_lex_files:
        path = os.path.join(valid_lexer, f)
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()
        try:
            tokens = Lexer(code, file=f).tokenize()
            assert len(tokens) > 0 and tokens[-1].type == TokenType.EOF
            # "produced at least one token" is satisfied by returning only EOF, so the
            # fixture has to be checked against the token stream it actually declares.
            expected = None
            kinds = []
            for line in code.splitlines():
                stripped = line.strip()
                if stripped.startswith("# TOKENS:"):
                    expected = stripped.split(":", 1)[1].split()
                elif stripped.startswith("# TOKEN:"):
                    kinds.append(stripped.split(":", 1)[1].strip())
            if expected is not None:
                actual = [t.type.name for t in tokens]
                assert actual == expected, f"token kinds {actual} != declared {expected}"
            actual_kinds = {t.type.name for t in tokens}
            for kind in kinds:
                assert kind in actual_kinds, f"declared token '{kind}' was not produced"
            # Position metadata must describe the real source, not a placeholder.
            for token in tokens:
                assert token.line >= 1 and token.col >= 1, f"invalid position on {token}"
                if token.type != TokenType.EOF:
                    assert token.start_offset is not None and token.end_offset is not None, \
                        f"token {token.type.name} is missing source offsets"
                    assert token.end_offset > token.start_offset, \
                        f"token {token.type.name} has an empty source span"
            v_lex_passed += 1
        except Exception as e:
            print(f"  [LEXER FAIL] {f}: {e}")

    i_lex_passed = 0
    for f in i_lex_files:
        path = os.path.join(invalid_lexer, f)
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()
        try:
            Lexer(code, file=f).tokenize()
            print(f"  [LEXER FAIL] {f}: Expected LexerError but succeeded")
        except LexerError as err:
            _check_expected_diag_code(code, err)
            i_lex_passed += 1
        except Exception as e:
            print(f"  [LEXER FAIL] {f}: Unexpected exception {e}")

    print(f"Lexer valid:         {v_lex_passed}/{len(v_lex_files)}")
    print(f"Lexer invalid:       {i_lex_passed}/{len(i_lex_files)}")

    # 2. Parser Tests
    valid_parser = "tests/parser/valid"
    invalid_parser = "tests/parser/invalid"

    v_parse_files = sorted([f for f in os.listdir(valid_parser) if f.endswith(".wysna")]) if os.path.exists(valid_parser) else []
    i_parse_files = sorted([f for f in os.listdir(invalid_parser) if f.endswith(".wysna")]) if os.path.exists(invalid_parser) else []

    v_parse_passed = 0
    for f in v_parse_files:
        path = os.path.join(valid_parser, f)
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()
        try:
            ast = Parser(file=f).parse(code)
            assert isinstance(ast, Program)
            # An empty Program satisfies isinstance, so check that the parsed body
            # matches the source and serialises with the declared AST version.
            assert ast.body, "parsed AST has an empty body"
            dumped = ast.to_dict()
            assert dumped.get("wysna_ast_version") == "0.1", "AST must declare wysna_ast_version 0.1"
            # Only column-zero declarations are top level; members of a class,
            # interface or struct body are indented and belong to that declaration.
            declared = sum(1 for line in code.splitlines()
                           if line[:1] not in (" ", "\t")
                           and line.startswith(("fn ", "struct ", "enum ", "class ", "interface ", "let ", "pub ")))
            if declared:
                assert len(ast.body) >= declared, \
                    f"AST body has {len(ast.body)} nodes for {declared} declared top-level statements"
            v_parse_passed += 1
        except Exception as e:
            print(f"  [PARSER FAIL] {f}: {e}")

    i_parse_passed = 0
    for f in i_parse_files:
        path = os.path.join(invalid_parser, f)
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()
        try:
            Parser(file=f).parse(code)
            print(f"  [PARSER FAIL] {f}: Expected ParserError but succeeded")
        except ParserError as err:
            _check_expected_diag_code(code, err)
            i_parse_passed += 1
        except Exception as e:
            print(f"  [PARSER FAIL] {f}: Unexpected exception {e}")

    print(f"Parser valid:        {v_parse_passed}/{len(v_parse_files)}")
    print(f"Parser invalid:      {i_parse_passed}/{len(i_parse_files)}")

    # 3. Type Checker Tests
    valid_types = "tests/types/valid"
    invalid_types = "tests/types/invalid"

    v_type_files = sorted([f for f in os.listdir(valid_types) if f.endswith(".wysna")]) if os.path.exists(valid_types) else []
    i_type_files = sorted([f for f in os.listdir(invalid_types) if f.endswith(".wysna")]) if os.path.exists(invalid_types) else []

    v_type_passed = 0
    for f in v_type_files:
        path = os.path.join(valid_types, f)
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()
        try:
            ast = Parser(file=path).parse(code)
            checker = TypeChecker(file=path)
            checker.check(ast)
            # A checker that accepts without recording anything is indistinguishable
            # from one that never ran, so require that annotated declarations were
            # actually visited and typed.
            from node.NodeType import FnDecl, LetDecl, StructDecl, EnumDecl, ClassDecl
            declarations = [node for node in ast.body if isinstance(node, (FnDecl, LetDecl, StructDecl, EnumDecl, ClassDecl))]
            assert declarations, "fixture declares nothing for the checker to verify"
            for node in ast.body:
                if isinstance(node, (FnDecl, StructDecl, EnumDecl)):
                    assert node.name in checker.global_scope.symbols or node.name in checker.global_scope.types, \
                        f"checker never registered declaration '{node.name}'"
            for node in ast.body:
                if isinstance(node, FnDecl):
                    assert node.name in checker.global_scope.symbols, \
                        f"function '{node.name}' missing from the checked global scope"
            v_type_passed += 1
        except Exception as e:
            print(f"  [TYPE FAIL] {f}: {e}")

    i_type_passed = 0
    for f in i_type_files:
        path = os.path.join(invalid_types, f)
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()
        try:
            ast = Parser(file=path).parse(code)
            checker = TypeChecker(file=path)
            checker.check(ast)
            print(f"  [TYPE FAIL] {f}: Expected TypeError but succeeded")
        except TypeError as err:
            _check_expected_diag_code(code, err)
            i_type_passed += 1
        except Exception as e:
            print(f"  [TYPE FAIL] {f}: Unexpected exception {e}")

    print(f"Types valid:         {v_type_passed}/{len(v_type_files)}")
    print(f"Types invalid:       {i_type_passed}/{len(i_type_files)}")

    # 4. Error Handling Tests (tests/errors/)
    err_dir = "tests/errors"
    err_files = sorted([f for f in os.listdir(err_dir) if f.endswith(".wysna")]) if os.path.exists(err_dir) else []
    err_passed = 0
    for f in err_files:
        path = os.path.join(err_dir, f)
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()
        try:
            ast = Parser(file=path).parse(code)
            TypeChecker(file=path).check(ast)
            if f.startswith("valid_"):
                err_passed += 1
            else:
                print(f"  [ERROR SUITE FAIL] {f}: Expected TypeError")
        except TypeError as err:
            if f.startswith("invalid_"):
                _check_expected_diag_code(code, err)
                err_passed += 1
            else:
                print(f"  [ERROR SUITE FAIL] {f}: Unexpected TypeError")
        except Exception as e:
            print(f"  [ERROR SUITE FAIL] {f}: {e}")

    print(f"Error handling:      {err_passed}/{len(err_files)}")

    # 5. Formatter Idempotence Tests
    from tests.formatter_contract import formatter_paths, verify_formatting
    fmt_files = formatter_paths()
    fmt_passed = 0
    for path in fmt_files:
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()
        try:
            verify_formatting(code, str(path))
            fmt_passed += 1
        except Exception as e:
            print(f"  [FORMATTER FAIL] {path}: {e}")

    print(f"Formatter idempotent:{fmt_passed}/{len(fmt_files)}")

    # 6. Typed HIR Lowering Tests
    hir_files = sorted(glob.glob("tests/runtime/*.wysna") + glob.glob("examples/*.wysna"))
    hir_passed = 0
    for path in hir_files:
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()
        try:
            ast = Parser(file=path).parse(code)
            checker = TypeChecker(file=path)
            checker.check(ast)
            builder = HirBuilder(checker.interner, file_path=path)
            hir_mod = builder.lower(ast)
            assert isinstance(hir_mod, HirModule)
            # An empty module satisfies `isinstance`, so require that lowering actually
            # reproduced the program's declarations and that every node carries a
            # resolved type rather than the TYPE_ERROR sentinel.
            source_fns = [n for n in ast.body if isinstance(n, FnDecl)]
            lowered_names = {fn.name for fn in hir_mod.functions}
            for node in source_fns:
                assert node.name in lowered_names, f"function '{node.name}' was not lowered to HIR"
            for fn in hir_mod.functions:
                assert fn.return_type_id != 0, f"function '{fn.name}' has an unresolved HIR return type"
                for parameter in fn.params:
                    assert parameter.type_id != 0, f"parameter '{parameter.name}' has an unresolved HIR type"
            hir_passed += 1
        except Exception as e:
            print(f"  [HIR FAIL] {path}: {e}")

    print(f"Typed HIR Lowering:  {hir_passed}/{len(hir_files)}")

    # 7. Wysna Seed Runtime Interpreter Tests
    runtime_dir = "tests/runtime"
    runtime_files = sorted([f for f in os.listdir(runtime_dir) if f.endswith(".wysna")]) if os.path.exists(runtime_dir) else []
    runtime_passed = 0

    for f in runtime_files:
        path = os.path.join(runtime_dir, f)
        stem = f[:-6]
        expected_path = os.path.join(runtime_dir, stem + ".expected")
        with open(path, "r", encoding="utf-8") as stream:
            code = stream.read()

        buf = io.StringIO()
        expected = ""
        if os.path.exists(expected_path):
            with open(expected_path, "r", encoding="utf-8") as exp_stream:
                expected = exp_stream.read().strip()
        panic_code = panic_expectation(expected)
        try:
            ast = Parser(file=path).parse(code)
            checker = TypeChecker(file=path)
            checker.check(ast)
            interp = Interpreter(stdout=buf, file=path)
            if panic_code is None:
                interp.interpret(ast)
            else:
                # Panic fixtures declare the diagnostic they must raise, so the expected
                # "output" is the error, not stdout.
                try:
                    interp.interpret(ast)
                except WysnaRuntimeError as err:
                    assert err.code == panic_code, (
                        f"{f}: fixture declares {panic_code}, interpreter raised {err.code}: {err.message}")
                else:
                    raise AssertionError(
                        f"{f}: fixture declares a {panic_code} runtime panic but the interpreter exited normally")

            actual = buf.getvalue().strip()
            if panic_code is None and expected:
                assert actual == expected, f"Output mismatch: expected {expected!r}, got {actual!r}"

            runtime_passed += 1
        except Exception as e:
            print(f"  [RUNTIME FAIL] {f}: {e}")

    print(f"Runtime execution:   {runtime_passed}/{len(runtime_files)}")
    if not (
        v_lex_passed == len(v_lex_files)
        and i_lex_passed == len(i_lex_files)
        and v_parse_passed == len(v_parse_files)
        and i_parse_passed == len(i_parse_files)
        and v_type_passed == len(v_type_files)
        and i_type_passed == len(i_type_files)
        and err_passed == len(err_files)
        and fmt_passed == len(fmt_files)
        and hir_passed == len(hir_files)
        and runtime_passed == len(runtime_files)
    ):
        raise AssertionError("In-process core conformance failure")
    return (
        v_lex_files, v_lex_passed, i_lex_files, i_lex_passed,
        v_parse_files, v_parse_passed, i_parse_files, i_parse_passed,
        v_type_files, v_type_passed, i_type_files, i_type_passed,
        err_files, err_passed, fmt_files, fmt_passed,
        hir_files, hir_passed, runtime_dir, runtime_files, runtime_passed,
    )


def _run_suite_in_repo(repo_root: str) -> None:
    (
        v_lex_files, v_lex_passed, i_lex_files, i_lex_passed,
        v_parse_files, v_parse_passed, i_parse_files, i_parse_passed,
        v_type_files, v_type_passed, i_type_files, i_type_passed,
        err_files, err_passed, fmt_files, fmt_passed,
        hir_files, hir_passed, runtime_dir, runtime_files, runtime_passed,
    ) = _run_inprocess_core_steps(repo_root)

    # 7. Native C99 Backend Tests (Wysna Seed) & 8. Self-Hosting Wysna Compiler Stage-2 Suite
    c99_targets = [f for f in runtime_files if os.path.exists(os.path.join(runtime_dir, f[:-6] + ".expected"))]
    c99_passed = 0
    bootstrap_ok = False
    selfhost_suite_ok = False
    examples_dir = "examples"
    support_files = PROFILES.get('support_files', {})
    example_files = sorted(f for f in os.listdir(examples_dir)
                           if f.endswith(".wysna") and "examples/" + f not in support_files)
    examples_passed = 0

    use_sanitize = os.environ.get("WYSNA_SANITIZE") == "1" or "--sanitize" in sys.argv
    extra_cc_flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if use_sanitize else []
    if use_sanitize:
        print("[SANITIZERS ENABLED] Compiling with Clang ASan and UBSan (-fsanitize=address,undefined)")

    compile_workers = min(4, max(1, os.cpu_count() or 2))
    cc_cmd = os.environ.get("CC", "cc")

    with tempfile.TemporaryDirectory() as tmpdir:
        seed_jobs = []
        seed_failures = {}
        for f in c99_targets:
            path = os.path.join(runtime_dir, f)
            expected_path = os.path.join(runtime_dir, f[:-6] + ".expected")
            with open(path, "r", encoding="utf-8") as stream:
                code = stream.read()
            with open(expected_path, "r", encoding="utf-8") as exp_stream:
                expected = exp_stream.read()
            try:
                ast = Parser(file=path).parse(code)
                TypeChecker(file=path).check(ast)
                c_code = CCodeGen(file=path).generate(ast)
                c_path = os.path.join(tmpdir, f[:-6] + ".c")
                bin_path = os.path.join(tmpdir, f[:-6])
                with open(c_path, "w", encoding="utf-8") as cf:
                    cf.write(c_code)
                seed_jobs.append((f, c_path, bin_path, expected))
            except Exception as e:
                seed_failures[f] = e

        def _build_seed_binary(job):
            f, c_path, bin_path, expected = job
            try:
                comp = subprocess.run([cc_cmd, "-std=c99", f"-I{repo_root}"] + extra_cc_flags + [c_path, "-o", bin_path], timeout=120, capture_output=True, text=True)
                assert comp.returncode == 0, comp.stderr
                return f, (bin_path, expected), None
            except Exception as exc:
                return f, None, exc

        if compile_workers > 1 and len(seed_jobs) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=compile_workers) as pool:
                built_map = {f: (res, err) for f, res, err in pool.map(_build_seed_binary, seed_jobs)}
        else:
            built_map = {f: (res, err) for f, res, err in (_build_seed_binary(job) for job in seed_jobs)}

        for f in c99_targets:
            if f in seed_failures:
                print(f"  [C99 FAIL] {f}: {seed_failures[f]}")
                continue
            compiled, compile_err = built_map[f]
            if compile_err is not None:
                print(f"  [C99 FAIL] {f}: {compile_err}")
                continue
            bin_path, expected = compiled
            try:
                run_res = subprocess.run([bin_path], timeout=30, capture_output=True, text=True)
                _assert_native_outcome(f, run_res, expected, panic_expectation(expected))
                c99_passed += 1
            except Exception as e:
                print(f"  [C99 FAIL] {f}: {e}")

        selfhost_targets = [f for f in c99_targets if not unsupported_reason("tests/runtime/" + f, "selfhost")]
        print(f"Wysna Compiler profile: {len(selfhost_targets)} supported, {len(c99_targets) - len(selfhost_targets)} unsupported (see tests/engine_profiles.json)")
        selfhost_runtime_passed = 0

        # 8. Self-Hosting Bootstrap Fixed-Point (Wysna Seed -> Wysna Compiler Stage 1 -> Wysna Compiler Stage 2)
        compiler_src_path = os.path.join(repo_root, "selfhost", "compiler.wysna")
        try:
            from compiler.Bootstrap import bootstrap_compiler, publish_bootstrap_cache
            artifacts = bootstrap_compiler(repo_root, os.path.join(tmpdir, "bootstrap"))
            stage2_command = [str(artifacts.compiler), "--stdlib-root", str(Path(repo_root) / "stdlib")]
            h0, h1, h2 = artifacts.hashes
            assert h0 == h1 == h2, f"Native MIR hash mismatch: {h0} vs {h1} vs {h2}"
            bootstrap_ok = True
            publish_bootstrap_cache(repo_root, artifacts)

            # 9. Verify Wysna Compiler Stage 2 Native Compiler on Core Runtime Conformance Programs
            selfhost_targets = [f for f in c99_targets if not unsupported_reason("tests/runtime/" + f, "selfhost")]

            def _compile_selfhost_target(f):
                path = os.path.join(runtime_dir, f)
                expected_path = os.path.join(runtime_dir, f[:-6] + ".expected")
                with open(expected_path, "r", encoding="utf-8") as exp_stream:
                    expected = exp_stream.read()
                selfhost_c_path = os.path.join(tmpdir, "selfhost_" + f[:-6] + ".c")
                selfhost_bin_path = os.path.join(tmpdir, "selfhost_" + f[:-6])
                subprocess.run(stage2_command + [path, selfhost_c_path], check=True, timeout=120, capture_output=True, cwd=repo_root)
                subprocess.run([cc_cmd, "-std=c99", f"-I{repo_root}"] + extra_cc_flags + [selfhost_c_path, "-o", selfhost_bin_path], check=True, timeout=120, capture_output=True)
                return f, selfhost_bin_path, expected

            if compile_workers > 1 and len(selfhost_targets) > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=compile_workers) as pool:
                    selfhost_compiled = list(pool.map(_compile_selfhost_target, selfhost_targets))
            else:
                selfhost_compiled = [_compile_selfhost_target(f) for f in selfhost_targets]

            for f, selfhost_bin_path, expected in selfhost_compiled:
                run_res = subprocess.run([selfhost_bin_path], timeout=30, capture_output=True, text=True, cwd=repo_root)
                _assert_native_outcome(f, run_res, expected, panic_expectation(expected))
                selfhost_runtime_passed += 1

            # 10. Verify Showcase Examples (examples/*.wysna) on Wysna Seed Interpreter, Wysna Seed C99, and Wysna Compiler Stage-2 Native
            for f in example_files:
                ex_path = os.path.join(examples_dir, f)
                ex_stem = f[:-6]
                exp_path = os.path.join(examples_dir, ex_stem + ".expected")
                with open(ex_path, "r", encoding="utf-8") as sf:
                    ex_code = sf.read()
                with open(exp_path, "r", encoding="utf-8") as ef:
                    ex_expected = ef.read()

                ex_ast = Parser(file=ex_path).parse(ex_code)
                TypeChecker(file=ex_path).check(ex_ast)
                ex_buf = io.StringIO()
                Interpreter(stdout=ex_buf, file=ex_path).interpret(ex_ast)
                assert ex_buf.getvalue() == ex_expected, f"Interpreter mismatch on {f}"

                ex_c_path = os.path.join(tmpdir, "ex_" + f[:-6] + ".c")
                ex_bin_path = os.path.join(tmpdir, "ex_" + f[:-6])
                subprocess.run(stage2_command + [ex_path, ex_c_path], check=True, timeout=120, capture_output=True, cwd=repo_root)
                subprocess.run([os.environ.get("CC", "cc"), "-std=c99", f"-I{repo_root}", ex_c_path, "-o", ex_bin_path], check=True, timeout=120, capture_output=True)
                ex_res = subprocess.run([ex_bin_path], timeout=30, capture_output=True, text=True, cwd=repo_root)
                assert ex_res.returncode == 0, ex_res.stderr
                assert ex_res.stdout == ex_expected, f"Wysna Compiler native mismatch on {f}"
                examples_passed += 1
            selfhost_suite_ok = True
        except Exception as e:
            print(f"  [BOOTSTRAP/SELFHOST FAIL] {e}")

    # 11. Typed HIR -> C99 Native Backend (Seed MIR/C99)
    hir_c99_passed = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        hir_jobs = []
        hir_failures = {}
        for f in c99_targets:
            path = os.path.join(runtime_dir, f)
            expected_path = os.path.join(runtime_dir, f[:-6] + ".expected")
            with open(path, "r", encoding="utf-8") as stream:
                code = stream.read()
            with open(expected_path, "r", encoding="utf-8") as exp_stream:
                expected = exp_stream.read()
            try:
                ast = Parser(file=path).parse(code)
                checker = TypeChecker(file=path)
                checker.check(ast)
                builder = HirBuilder(checker.interner, file_path=path)
                hir_mod = builder.lower(ast)
                gen = HirCCodeGen(checker.interner, file_path=path)
                c_code = gen.generate(hir_mod)
                c_path = os.path.join(tmpdir, "hir_" + f[:-6] + ".c")
                bin_path = os.path.join(tmpdir, "hir_" + f[:-6])
                with open(c_path, "w", encoding="utf-8") as cf:
                    cf.write(c_code)
                hir_jobs.append((f, c_path, bin_path, expected))
            except Exception as e:
                hir_failures[f] = e

        def _build_hir_binary(job):
            f, c_path, bin_path, expected = job
            try:
                comp = subprocess.run([cc_cmd, "-std=c99", f"-I{repo_root}"] + extra_cc_flags + [c_path, "-o", bin_path], timeout=120, capture_output=True, text=True)
                assert comp.returncode == 0, comp.stderr
                return f, (bin_path, expected), None
            except Exception as exc:
                return f, None, exc

        if compile_workers > 1 and len(hir_jobs) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=compile_workers) as pool:
                hir_built = {f: (res, err) for f, res, err in pool.map(_build_hir_binary, hir_jobs)}
        else:
            hir_built = {f: (res, err) for f, res, err in (_build_hir_binary(job) for job in hir_jobs)}

        for f in c99_targets:
            if f in hir_failures:
                print(f"  [HIR C99 FAIL] {f}: {hir_failures[f]}")
                continue
            compiled, compile_err = hir_built[f]
            if compile_err is not None:
                print(f"  [HIR C99 FAIL] {f}: {compile_err}")
                continue
            bin_path, expected = compiled
            try:
                run_res = subprocess.run([bin_path], timeout=30, capture_output=True, text=True, cwd=repo_root)
                _assert_native_outcome(f, run_res, expected, panic_expectation(expected))
                hir_c99_passed += 1
            except Exception as e:
                print(f"  [HIR C99 FAIL] {f}: {e}")

    # 12. Modular Wysna Compiler Source Conformance
    test_split_conformance()
    split_ok = True

    # 13. Generic Hardening & Metrics Conformance
    hardening_ok = run_generic_hardening_tests()

    # Clean temporary files if any
    temp_file = os.path.join(runtime_dir, "temp_file.txt")
    # 11. Copy-on-Write Telemetry & Path-Copying Asymptotics (B02)
    cow_telemetry_ok = run_cow_telemetry_suite()
    session_ok = run_session_tests()
    service_ok = run_compiler_service_tests()
    toolchain_ok = run_native_toolchain_tests()
    resolved_hir_ok = run_resolved_hir_tests()
    generic_c03_ok = run_c03_generic_tests()
    adt_c04_ok = run_c04_adt_tests()
    types_c05_ok = run_c05_types_tests()
    backend_c06_ok = run_c06_backend_tests()
    parity_d01_ok = run_d01_parity_tests()
    from tests.unit.test_d04_semantic_fuzzing import run_all as run_d04_fuzz_tests
    fuzz_d04_ok = run_d04_fuzz_tests()
    from tests.unit.test_d05_telemetry_benchmarks import run_all as run_d05_telemetry_tests
    telemetry_d05_ok = run_d05_telemetry_tests()
    from tests.unit.test_expansion_contracts import run_expansion_tests
    expansion_ok = run_expansion_tests()
    from tests.unit.test_audit_remediation import run_audit_tests
    audit_ok = run_audit_tests()
    from tests.unit.test_mir import run_mir_tests
    mir_ok = run_mir_tests()
    from tests.unit.test_audit_completion import run_completion_tests
    completion_ok = run_completion_tests()
    from tests.unit.test_audit_readiness import run_readiness_tests
    readiness_ok = run_readiness_tests()
    from tests.unit.test_audit_findings import run_findings_tests
    from tests.unit.test_standard_toolkit import run_toolkit_tests
    from tests.unit.test_foundations import run_foundations_tests
    foundations_ok = run_foundations_tests()
    findings_ok = run_findings_tests()
    toolkit_ok = run_toolkit_tests() and foundations_ok

    if os.path.exists(temp_file):
        try:
            os.remove(temp_file)
        except OSError:
            pass

    print(f"Seed C99 Native:     {c99_passed}/{len(c99_targets)}")
    print(f"Seed MIR C99:        {hir_c99_passed}/{len(c99_targets)}")
    print(f"Compiler Stage-2:    {selfhost_runtime_passed}/{len(selfhost_targets)}")
    print(f"Compiler Modular:    {'1/1 (Loss-less verified)' if split_ok else '0/1'}")
    print(f"Generic Hardening:   {'1/1 (Canonical cache verified)' if hardening_ok else '0/1'}")
    print(f"CoW Telemetry (B02): {'1/1 (Asymptotics verified)' if cow_telemetry_ok else '0/1'}")
    print(f"Compilation Session: {'1/1' if session_ok else '0/1'}")
    print(f"Compiler Service:    {'1/1' if service_ok else '0/1'}")
    print(f"Native Toolchain:    {'1/1' if toolchain_ok else '0/1'}")
    print(f"Resolved HIR (C02):  {'1/1' if resolved_hir_ok else '0/1'}")
    print(f"Generic Specialization (C03): {'1/1' if generic_c03_ok else '0/1'}")
    print(f"ADTs & Layout Graph (C04):    {'1/1' if adt_c04_ok else '0/1'}")
    print(f"Types & Inference (C05):      {'1/1' if types_c05_ok else '0/1'}")
    print(f"Default Backend (C06):        {'1/1 (HIR default verified)' if backend_c06_ok else '0/1'}")
    print(f"Parity Matrix (D01):          {'1/1 (Matrix & limits verified)' if parity_d01_ok else '0/1'}")
    print(f"Semantic Fuzz (D04):          {'1/1 (Determinism verified)' if fuzz_d04_ok else '0/1'}")
    print(f"Telemetry & Bench(D05):       {'1/1 (Metrics verified)' if telemetry_d05_ok else '0/1'}")
    print(f"Expansion (E01-E04):          {'1/1' if expansion_ok else '0/1'}")
    print(f"Examples E2E Suite:  {examples_passed}/{len(example_files)}")
    print(f"Bootstrap FixedPoint:{'1/1 (SHA256 verified)' if bootstrap_ok else '0/1'}")

    print("==================================================")
    all_ok = (
        v_lex_passed == len(v_lex_files)
        and i_lex_passed == len(i_lex_files)
        and v_parse_passed == len(v_parse_files)
        and i_parse_passed == len(i_parse_files)
        and v_type_passed == len(v_type_files)
        and i_type_passed == len(i_type_files)
        and err_passed == len(err_files)
        and fmt_passed == len(fmt_files)
        and hir_passed == len(hir_files)
        and runtime_passed == len(runtime_files)
        and c99_passed == len(c99_targets)
        and hir_c99_passed == len(c99_targets)
        and selfhost_runtime_passed == len(selfhost_targets)
        and split_ok
        and hardening_ok
        and cow_telemetry_ok
        and session_ok
        and service_ok
        and toolchain_ok
        and resolved_hir_ok
        and generic_c03_ok
        and adt_c04_ok
        and types_c05_ok
        and backend_c06_ok
        and parity_d01_ok
        and fuzz_d04_ok
        and telemetry_d05_ok
        and expansion_ok
        and audit_ok
        and mir_ok
        and completion_ok
        and readiness_ok
        and findings_ok
        and toolkit_ok
        and examples_passed == len(example_files)
        and bootstrap_ok
        and selfhost_suite_ok
    )

    if all_ok:
        total = (
            v_lex_passed + i_lex_passed + v_parse_passed + i_parse_passed +
            v_type_passed + i_type_passed + err_passed + fmt_passed + hir_passed +
            runtime_passed + c99_passed + hir_c99_passed + selfhost_runtime_passed +
            examples_passed + 22
        )
        print(f"ALL {total}/{total} CONFORMANCE, EXAMPLES & SELF-HOSTING TESTS PASSED (100%).")
        sys.exit(0)
    else:
        print("FAILURES DETECTED.")
        sys.exit(1)


if __name__ == "__main__":
    run_suite()

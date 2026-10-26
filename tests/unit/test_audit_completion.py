"""Audit closure gates: allocation oracles, typed composition and safe formatting."""
from collections import Counter
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from main import ensure_selfhost_compiler_binary
from tests.engine_harness import verify
from tests.formatter_contract import formatter_paths, structural_ast, verify_formatting
from tests.fuzz.typed_properties import generate_typed_properties

ROOT = Path(__file__).resolve().parents[2]
SEEDS = (0, 1, 2, 3, 5, 8, 13, 21)


def test_ascii_allocation_and_lifetime_contract():
    source = r'''#include "runtime/wysna_runtime.h"
#include <assert.h>

static WVal copy_bytes(const char* bytes, size_t count) {
    char* buffer = malloc(count + 1);
    assert(buffer);
    memcpy(buffer, bytes, count);
    buffer[count] = 0;
    return wysna_owned_str_n(buffer, count);
}

static void check_offsets(WVal text, const size_t* offsets, const size_t* widths, size_t count) {
    for (int pass = 0; pass < 2; pass++) {
        size_t offset = 0, width = 0;
        assert(!wysna_string_offset(text, INT64_MIN, &offset, &width));
        assert(!wysna_string_offset(text, -1, &offset, &width));
        for (size_t index = 0; index < count; index++) {
            assert(wysna_string_offset(text, (int64_t)index, &offset, &width));
            assert(offset == offsets[index] && width == widths[index]);
        }
        assert(!wysna_string_offset(text, (int64_t)count, &offset, &width));
        assert(!wysna_string_offset(text, INT64_MAX, &offset, &width));
        assert(wysna_string_length(text) == (int64_t)count);
    }
}

int main(void) {
    assert(W_RUNTIME_ABI_VERSION == 2 && sizeof(WVal) <= 32);
    for (int64_t code = 0; code < 128; code++) {
        WVal character = wysna_builtin_chr(wysna_int(code));
        assert(character.tag == W_STR && character.as.string.length == 1);
        assert(wysna_builtin_ord(character).as.i == code);
        WVal retained = wysna_clone(character);
        wysna_release(character);
        assert(wysna_builtin_ord(retained).as.i == code);
        wysna_release(retained);
    }
    assert(wysna_g_alloc_count == 0);
    WVal owner = wysna_copy_str("abc");
    WVal character = wysna_index_get_owned(owner, wysna_int(1));
    wysna_release(owner);
    assert(wysna_builtin_ord(character).as.i == 'b');
    wysna_release(character);
    assert(wysna_g_alloc_count == 1 && wysna_g_free_count == 1);
    WVal unicode = wysna_builtin_chr(wysna_int(128578));
    WVal copied = wysna_index_get_owned(unicode, wysna_int(0));
    wysna_release(unicode);
    assert(wysna_builtin_ord(copied).as.i == 128578);
    wysna_release(copied);
    assert(wysna_eq(wysna_float(0.0), wysna_float(-0.0)));
    assert(!wysna_eq(wysna_float(NAN), wysna_float(NAN)));
    assert(!wysna_eq(wysna_str_n("a\0b", 3), wysna_str_n("a\0c", 3)));
    assert(wysna_eq(wysna_str_n("a\0b", 3), wysna_str_n("a\0b", 3)));
    char* shared_data = malloc(4);
    assert(shared_data);
    memcpy(shared_data, "x\0y", 4);
    WVal shared = wysna_owned_str_n(shared_data, 3);
    WVal alias = wysna_clone(shared);
    WVal values = wysna_make_list(2, (WVal[]){shared, wysna_str("static")});
    wysna_release(shared);
    wysna_release(values);
    assert(wysna_eq(alias, wysna_str_n("x\0y", 3)));
    wysna_release(alias);
    const size_t mixed_offsets[] = {0, 1, 3, 7, 8};
    const size_t mixed_widths[] = {1, 2, 4, 1, 1};
    WVal mixed = wysna_str_n("A\xc3\xa9\xf0\x9f\x99\x82\0Z", 9);
    check_offsets(mixed, mixed_offsets, mixed_widths, 5);
    WVal owned_mixed = copy_bytes(mixed.as.string.data, mixed.as.string.length);
    check_offsets(owned_mixed, mixed_offsets, mixed_widths, 5);
    WVal escaped = wysna_index_get_owned(owned_mixed, wysna_int(2));
    wysna_release(owned_mixed);
    assert(wysna_builtin_ord(escaped).as.i == 128578);
    wysna_release(escaped);
    const size_t byte_offsets[] = {0, 1, 2};
    const size_t byte_widths[] = {1, 1, 1};
    WVal cached = copy_bytes("A\0Z", 3);
    assert(wysna_string_length(cached) == 3);
    alias = wysna_clone(cached);
    wysna_release(cached);
    check_offsets(alias, byte_offsets, byte_widths, 3);
    assert(alias.as.string.owner->codepoint_offsets == NULL);
    wysna_release(alias);
    const char invalid[] = {(char)0x80, (char)0xc0, (char)0xaf};
    WVal invalid_owned = copy_bytes(invalid, sizeof(invalid));
    check_offsets(invalid_owned, byte_offsets, byte_widths, 3);
    wysna_release(invalid_owned);
    check_offsets(wysna_str(""), NULL, NULL, 0);
    WVal empty_owned = wysna_copy_str("");
    check_offsets(empty_owned, NULL, NULL, 0);
    wysna_release(empty_owned);
    WVal option = wysna_enum_payload("Option", "Some", wysna_int(7));
    assert(wysna_eq(option, wysna_int(7)));
    wysna_release(option);
    int64_t released = wysna_g_release_count;
    wysna_release(wysna_unit());
    wysna_release(wysna_none());
    wysna_release(wysna_int(7));
    wysna_release(wysna_float(1.5));
    wysna_release(wysna_bool(true));
    wysna_release(wysna_pointer(NULL));
    wysna_release(wysna_str("static"));
    assert(wysna_g_release_count == released);
    assert(wysna_g_alloc_count == wysna_g_free_count && wysna_g_live_objects == 0);
    wysna_runtime_cleanup();
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='wysna-ascii-contract-') as temporary:
        path = Path(temporary) / 'contract.c'
        binary = Path(temporary) / 'contract'
        path.write_text(source)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c99', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', '-I' + str(ROOT), str(path), '-o', str(binary)],
                       check=True, capture_output=True, text=True, timeout=60)
        environment = dict(os.environ, WYSNA_MEM_CHECK='1', UBSAN_OPTIONS='halt_on_error=1')
        environment['ASAN_OPTIONS'] = 'detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0')
        result = subprocess.run([str(binary)], env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and result.stdout == '' and result.stderr == '', result


def test_formatter_preserves_guards_and_production_sources():
    paths = formatter_paths()
    from tools.bundle_selfhost import MODULE_ORDER
    required = ['selfhost/compiler.wysna', 'examples/ffi/sqlite/import_csv.wysna', 'stdlib/text.wysna']
    required += ['selfhost/src/' + name for name in MODULE_ORDER]
    assert all(ROOT / name in paths for name in required)
    for path in paths:
        verify_formatting(path.read_text(), str(path))
    from parser.Parser import Parser
    first = Parser().parse('fn guarded(x: int) -> int { match x { 1 if false => 9, _ => 0 } }')
    dropped = Parser().parse('fn guarded(x: int) -> int { match x { 1 => 9, _ => 0 } }')
    assert structural_ast(first) != structural_ast(dropped), 'oracle must detect a deleted match guard'


def test_typed_generator_shape_and_independent_model():
    kinds = Counter()
    sources = set()
    def visit(expression):
        kinds[expression.kind] += 1
        for child in expression.args:
            visit(child)
    for seed in range(64):
        source, expected, expressions = generate_typed_properties(seed)
        assert (source, expected, expressions) == generate_typed_properties(seed)
        assert len(source) < 50000 and expected
        sources.add(source)
        for expression in expressions:
            visit(expression)
    assert len(sources) == 64
    assert {'literal', 'variable', 'binary', 'observe', 'if', 'match', 'bind',
            'recover', 'list', 'index', 'not'} <= set(kinds)


def test_typed_composition_across_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-typed-composition-') as temporary:
        for seed in SEEDS:
            source, expected, _ = generate_typed_properties(seed)
            formatted = verify_formatting(source, 'typed_properties_' + str(seed) + '.wysna')
            for variant, code in [('source', source), ('formatted', formatted)]:
                ok, detail = verify(code, f'typed_properties_{seed}_{variant}.wysna', selfhost_bin, temporary,
                                    expected=expected, strict=True,
                                    sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
                assert ok, f'seed {seed}, {variant}: {detail}\n{code}'


def test_unqualified_enum_variant_pattern_exhaustiveness_and_dispatch():
    from parser.Parser import Parser
    from checker.TypeChecker import TypeChecker
    from error.Error import TypeError
    from runtime.Interpreter import Interpreter

    # 1. Incomplete unqualified arms must fail exhaustiveness (W2003)
    incomplete_source = '''
    enum Dir { North, South, East }
    fn f(d: Dir) -> int {
        return match d {
            North => 1,
            South => 2
        }
    }
    '''
    ast_incomplete = Parser().parse(incomplete_source)
    try:
        TypeChecker().check(ast_incomplete)
        assert False, "Incomplete unqualified enum match should have raised W2003"
    except TypeError as e:
        assert e.code == "W2003", f"Expected W2003, got {e.code}"

    # 2. Complete unqualified arms must pass check and evaluate correctly in interpreter
    complete_source = '''
    enum Dir { North, South, East }
    fn f(d: Dir) -> int {
        return match d {
            North => 1,
            South => 2,
            East => 3
        }
    }
    let res = f(Dir.East)
    '''
    ast_complete = Parser().parse(complete_source)
    TypeChecker().check(ast_complete)
    interp = Interpreter()
    interp.interpret(ast_complete)
    assert interp.global_env.lookup("res").value == 3, "f(Dir.East) must return 3, not 1"


def test_specification_operator_semantics():
    from parser.Parser import Parser
    from checker.TypeChecker import TypeChecker
    from runtime.Interpreter import Interpreter

    # SPEC §3.2: Integer division and modulo truncate toward zero
    arithmetic_source = '''
    let a = -7 / 3
    let b = 7 / -2
    let c = -7 % 3
    let d = 7 % -2
    '''
    ast = Parser().parse(arithmetic_source)
    TypeChecker().check(ast)
    interp = Interpreter()
    interp.interpret(ast)
    assert interp.global_env.lookup("a").value == -2, "-7 / 3 must truncate toward zero to -2"
    assert interp.global_env.lookup("b").value == -3, "7 / -2 must truncate toward zero to -3"
    assert interp.global_env.lookup("c").value == -1, "-7 % 3 must be -1"
    assert interp.global_env.lookup("d").value == 1, "7 % -2 must be 1"

    # SPEC §5: Unary precedence Level 3 tighter than comparison Level 8
    prec_source = '''
    let r1 = not false == false
    let r2 = (not false) == false
    let r3 = not true == false
    '''
    ast_prec = Parser().parse(prec_source)
    TypeChecker().check(ast_prec)
    interp_prec = Interpreter()
    interp_prec.interpret(ast_prec)
    assert interp_prec.global_env.lookup("r1").value is False, "'not false == false' is '(!false) == false' -> False"
    assert interp_prec.global_env.lookup("r2").value is False
    assert interp_prec.global_env.lookup("r3").value is True, "'not true == false' is '(!true) == false' -> True"


def run_completion_tests():
    test_ascii_allocation_and_lifetime_contract()
    test_formatter_preserves_guards_and_production_sources()
    test_typed_generator_shape_and_independent_model()
    test_typed_composition_across_engines()
    test_unqualified_enum_variant_pattern_exhaustiveness_and_dispatch()
    test_specification_operator_semantics()
    print('Audit completion: ASCII lifetime/allocation, formatter AST preservation, typed composition, enum pattern exhaustiveness and SPEC operator semantics passed')
    return True


if __name__ == '__main__':
    run_completion_tests()

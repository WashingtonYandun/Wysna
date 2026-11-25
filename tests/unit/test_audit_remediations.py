"""Regression and verification suite for Principal Technical Audit remediations (AUD-01..AUD-10)."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

from common.Version import WYSNA_VERSION
from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeToolchain
from error.Error import WysnaError
from main import ensure_selfhost_compiler_binary
from parser.Parser import Parser
from runtime.Interpreter import Interpreter
from tests.engine_harness import run_engine

ROOT = Path(__file__).resolve().parents[2]


def _compile_native_c(c_source: str, binary_path: Path, *, sanitize: bool = False) -> None:
    toolchain = NativeToolchain(str(ROOT))
    toolchain.build(c_source, str(binary_path), sanitize=sanitize)


def test_aud01_and_aud02_await_format_string_and_pending_deadlock_guard(tmp_path):
    """AUD-01/AUD-02: failed task messages with % specifiers and handle=0 pending tasks fail safely."""
    service = CompilerService(project_root=ROOT)

    # AUD-01: Error payload containing format specifiers must never be passed as fprintf format.
    fmt_source = '''import "std/async"

fn main() -> unit {
    let t = async.failed[int]("percent %s %n %x literal")
    let v = await t
    print(v)
}

main()
'''
    checked_fmt = service.check(fmt_source, file=str(tmp_path / "fmt_err.wysna"))
    emitted_fmt = service.emit_c(checked_fmt).source
    bin_fmt = tmp_path / "fmt_err_bin"
    _compile_native_c(emitted_fmt, bin_fmt, sanitize=False)
    proc_fmt = subprocess.run([str(bin_fmt)], capture_output=True, text=True, timeout=15)
    assert proc_fmt.returncode != 0
    assert "[W4003]" in proc_fmt.stderr
    assert "percent %s %n %x literal" in proc_fmt.stderr

    # AUD-02: Pending task with handle == 0 must report W4003 deadlock rather than looping forever.
    deadlock_source = '''from "std/async" use { Task }

fn main() -> unit {
    let empty_val: int? = None
    let pending = Task[int] {
        id: 1,
        handle: 0,
        is_ready: false,
        is_failed: false,
        value: empty_val,
        error: None,
    }
    let v = await pending
    print(v)
}

main()
'''
    checked_dead = service.check(deadlock_source, file=str(tmp_path / "deadlock.wysna"))
    emitted_dead = service.emit_c(checked_dead).source
    bin_dead = tmp_path / "deadlock_bin"
    _compile_native_c(emitted_dead, bin_dead, sanitize=False)
    proc_dead = subprocess.run([str(bin_dead)], capture_output=True, text=True, timeout=15)
    assert proc_dead.returncode != 0
    assert "[W4003]" in proc_dead.stderr
    assert "Deadlock" in proc_dead.stderr


def test_aud02_mir_verifier_rejects_invalid_wysna_await():
    """AUD-02: MIR Verifier checks wysna_await result type compatibility instead of pass."""
    from checker.TypeInterner import TYPE_INT, TYPE_STR
    from mir.Nodes import Operation, Slot, Source
    from mir.Verifier import Verifier

    service = CompilerService()
    checked = service.check("fn main() -> int { return 1 }\n", "<verifier>")
    mir = service.lower_mir(checked)
    fn = next(f for f in mir.functions if f.name == "wysna_fn_main")
    # Inject mismatched wysna_await(int) -> str
    arg_slot = len(fn.slots)
    fn.slots.append(Slot(arg_slot, TYPE_INT, "raw_arg"))
    bad_dst = len(fn.slots)
    fn.slots.append(Slot(bad_dst, TYPE_STR, "bad_await"))
    entry_block = fn.blocks[0]
    entry_block.operations.insert(
        0,
        Operation(
            opcode="release",
            result=None,
            args=(bad_dst,),
            source=Source("<verifier>", 1, 1),
        ),
    )
    entry_block.operations.insert(
        0,
        Operation(
            opcode="runtime",
            result=bad_dst,
            args=(arg_slot,),
            moves=(0,),
            source=Source("<verifier>", 1, 1),
            data={"name": "wysna_await"},
        ),
    )
    entry_block.operations.insert(
        0,
        Operation(
            opcode="const",
            result=arg_slot,
            args=(),
            moves=(),
            source=Source("<verifier>", 1, 1),
            data={"value": 7},
        ),
    )
    with pytest.raises(WysnaError) as exc_info:
        Verifier(checked._interner).verify(mir)
    assert exc_info.value.code == "W9999"


def test_aud05_bacon_rajan_cycle_collector_reclaims_class_cycles(tmp_path):
    """AUD-05: WYSNA_COLLECT_CYCLES=1 reclaims self-cycles and mutual class cycles under ASan/UBSan."""
    c_program = r'''
#include "runtime/wysna_runtime.h"
#include <assert.h>

static void build_mutual_cycle(void) {
    const char* keys[1] = {"peer"};
    WVal empty[1] = {wysna_none()};
    WVal a = wysna_make_object("Peer", 1, keys, empty);
    WVal b = wysna_make_object("Peer", 1, keys, empty);
    wysna_assign(wysna_member_ref(&a, "peer"), wysna_clone(b));
    wysna_assign(wysna_member_ref(&b, "peer"), wysna_clone(a));
    wysna_release(a);
    wysna_release(b);
}

static void build_list_cycle(void) {
    const char* keys[1] = {"children"};
    WVal lst = wysna_make_list(0, NULL);
    WVal init_vals[1] = {lst};
    WVal node = wysna_make_object("Tree", 1, keys, init_vals);
    wysna_release(lst);
    WVal* children_ref = wysna_member_ref(&node, "children");
    wysna_list_append(children_ref, node);
    wysna_release(node);
}

static WVal build_live_anchored_cycle(void) {
    const char* keys[1] = {"peer"};
    WVal empty[1] = {wysna_none()};
    WVal a = wysna_make_object("LivePeer", 1, keys, empty);
    WVal b = wysna_make_object("LivePeer", 1, keys, empty);
    wysna_assign(wysna_member_ref(&a, "peer"), wysna_clone(b));
    wysna_assign(wysna_member_ref(&b, "peer"), wysna_clone(a));
    wysna_release(b);
    return a; /* External root still holds a */
}

int main(void) {
    build_mutual_cycle();
    build_list_cycle();
    WVal anchor = build_live_anchored_cycle();

    /* Dead cycles are reclaimed while anchored live cycle stays alive. */
    int64_t freed = wysna_collect_cycles();
    assert(freed == 3);
    assert(wysna_as_struct(anchor) != NULL && wysna_as_struct(anchor)->control != NULL);
    assert(wysna_as_struct(anchor)->control->strong == 2);

    /* Drop external anchor, collect remaining cycle, verify zero live objects. */
    wysna_release(anchor);
    wysna_runtime_cleanup();
    assert(wysna_g_live_objects == 0);
    return 0;
}
'''
    c_file = tmp_path / "cycle_test.c"
    bin_file = tmp_path / "cycle_test"
    c_file.write_text(c_program, encoding="utf-8")
    cc = os.environ.get("CC", "cc")
    cmd = [
        cc, "-std=c99", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
        f"-I{ROOT}", str(c_file), "-o", str(bin_file),
    ]
    built = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr

    env = dict(os.environ, WYSNA_MEM_CHECK="1", WYSNA_COLLECT_CYCLES="1", UBSAN_OPTIONS="halt_on_error=1")
    env["ASAN_OPTIONS"] = "detect_leaks=1" if sys.platform.startswith("linux") else "detect_leaks=0"
    res = subprocess.run([str(bin_file)], env=env, capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr


def test_aud06_incremental_ast_cache_avoids_reparsing_unchanged_modules(tmp_path):
    """AUD-06: Editing entry module reuses cached ASTs of unchanged imported modules."""
    lib_file = tmp_path / "helpers.wysna"
    main_file = tmp_path / "app.wysna"
    lib_file.write_text("pub fn triple(x: int) -> int { return x * 3 }\n", encoding="utf-8")
    src_v1 = 'import "helpers.wysna" as helpers\nprint(helpers.triple(2))\n'
    src_v2 = 'import "helpers.wysna" as helpers\nprint(helpers.triple(5))\n'
    main_file.write_text(src_v1, encoding="utf-8")

    service = CompilerService(project_root=str(tmp_path))
    service.check(src_v1, str(main_file))
    assert service._session.parse_counts.get(str(lib_file.resolve()), 0) == 1

    # Modify only entry file; helpers.wysna source is untouched so Parser must not re-parse it.
    main_file.write_text(src_v2, encoding="utf-8")
    original_parse = Parser.parse
    parsed_paths = []

    def tracking_parse(self_parser, *args, **kwargs):
        parsed_paths.append(Path(self_parser.file).resolve())
        return original_parse(self_parser, *args, **kwargs)

    with patch.object(Parser, "parse", tracking_parse):
        service.check(src_v2, str(main_file))

    assert lib_file.resolve() not in parsed_paths
    assert main_file.resolve() in parsed_paths
    assert service._session.parse_counts.get(str(lib_file.resolve()), 0) == 1


def test_aud07_qualified_implements_extends_and_no_redundant_index_backtrack(tmp_path):
    """AUD-07: Module-qualified extends/implements work and _parse_index_alternative skips [int] backtracking."""
    contracts = tmp_path / "contracts.wysna"
    contracts.write_text(
        "pub interface Store {\n"
        "    fn save(key: str) -> int\n"
        "}\n"
        "pub class BaseStore {\n"
        "    pub prefix: str\n"
        "    fn init(p: str) { this.prefix = p }\n"
        "}\n",
        encoding="utf-8",
    )
    app_file = tmp_path / "main.wysna"
    app_src = (
        'import "contracts.wysna" as contracts\n'
        "class DiskStore extends contracts.BaseStore implements contracts.Store {\n"
        "    fn init(p: str) { super.init(p) }\n"
        "    pub fn save(key: str) -> int { return len(this.prefix) + len(key) }\n"
        "}\n"
        "fn persist(store: contracts.Store, k: str) -> int {\n"
        "    return store.save(k)\n"
        "}\n"
        'print(persist(DiskStore("db:"), "abc"))\n'
    )
    app_file.write_text(app_src, encoding="utf-8")

    service = CompilerService(project_root=str(tmp_path))
    checked = service.check_file(app_file)
    graph = service.graph(checked)
    assert any(
        edge["kind"] == "implements" and edge["from"].endswith("DiskStore") and edge["to"].endswith("Store")
        for edge in graph["edges"]
    )
    assert any(
        edge["kind"] == "extends" and edge["from"].endswith("DiskStore") and edge["to"].endswith("BaseStore")
        for edge in graph["edges"]
    )

    import io
    out_buf = io.StringIO()
    service.interpret(checked, stdout=out_buf)
    assert out_buf.getvalue().strip() == "6"

    for engine in ("interpreter", "ast", "hir"):
        eng_dir = tmp_path / engine
        eng_dir.mkdir(parents=True, exist_ok=True)
        (eng_dir / "contracts.wysna").write_text(contracts.read_text(encoding="utf-8"), encoding="utf-8")
        res = run_engine(engine, app_src, "qual_oop", "", tmp_path)
        assert res.exit_code == 0, f"{engine} failed: {res.stderr}"
        assert res.stdout.strip() == "6"

    # Verify _parse_index_alternative short-circuits without invoking parse_expr on primitive type arg [int].
    parser = Parser(file="<bt>")
    with patch.object(parser, "parse_expr", wraps=parser.parse_expr) as spy_expr:
        parser.parse("fn id[T](x: T) -> T { return x }\nlet v = id[int](42)\n")
        # parse_expr runs for 'return x' and '42' (2 top calls + nested id(42)), never once inside [int] index alt.
        for call in spy_expr.call_args_list:
            assert call.args == ()


def test_aud10_selfhost_cli_version_matches_release(tmp_path):
    """AUD-10: Selfhost compiler matches WYSNA_VERSION in --version and --check-json."""
    from tooling.CLI import main as cli_main
    import io
    import json
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit) as exc1:
            cli_main(["run", "selfhost/compiler.wysna", "--", "--version"])
    assert exc1.value.code == 0
    assert buf.getvalue().strip() == f"wysna {WYSNA_VERSION}"

    sample = tmp_path / "sample.wysna"
    sample.write_text("fn main() -> unit { print(1) }\n", encoding="utf-8")
    json_buf = io.StringIO()
    with redirect_stdout(json_buf):
        with pytest.raises(SystemExit) as exc2:
            cli_main(["run", "selfhost/compiler.wysna", "--", "--check-json", str(sample)])
    assert exc2.value.code == 0
    payload = json.loads(json_buf.getvalue().strip())
    assert payload["compiler_version"] == WYSNA_VERSION

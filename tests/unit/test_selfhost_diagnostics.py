"""Native compiler diagnostics must identify both sides of a type mismatch."""

from pathlib import Path
import subprocess

from compiler.Bootstrap import ensure_selfhost_compiler_binary

ROOT = Path(__file__).resolve().parents[2]


def test_native_compiler_reports_expected_and_found_types(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    cases = [
        ('let value: int = "wrong"\n', "expected 'int', found 'str'", ":1:18 -"),
        ('let name: str = None\n', "expected 'str', found 'none'", ":1:17 -"),
        ('fn use(value: list[int]) -> unit {}\nlet input: list[str] = ["x"]\nuse(input)\n',
         "expected 'list[int]', found 'list[str]'", ":3:5 -"),
        ('fn use(value: int?) -> unit {}\nuse("wrong")\n', "expected 'int?', found 'str'", ":2:5 -"),
    ]
    for index, (text, expected, location) in enumerate(cases):
        source = tmp_path / f"invalid-{index}.wysna"
        output = tmp_path / f"invalid-{index}.c"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run([compiler, str(source), str(output)], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode != 0
        assert "[W2001]" in result.stderr
        assert expected in result.stderr
        assert location in result.stderr
        assert not output.exists()


def test_native_compiler_points_at_offending_expression(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    cases = [
        ('fn main() {\n  let mut value: int = 1\n  value = "x"\n}\n',
         "W2001", "expected 'int', found 'str'", ":3:11 -"),
        ('fn main() {\n  let value: int = 1 + "x"\n}\n',
         "W2001", "binary operand types", ":2:24 -"),
        ('fn main() {\n  if 1 { print("x") }\n}\n',
         "W2001", "expected 'bool', found 'int'", ":2:6 -"),
        ('fn main() {\n  let values: list[int] = [1, "x"]\n}\n',
         "W2001", "expected 'int', found 'str'", ":2:31 -"),
        ('fn main() {\n  let values: dict[str, int] = {"a": "x"}\n}\n',
         "W2001", "expected 'int', found 'str'", ":2:38 -"),
        ('struct Point { x: int, y: int }\nfn main() {\n  let p = Point { x: 1, y: "z" }\n}\n',
         "W2001", "expected 'int', found 'str'", ":3:28 -"),
        ('fn main() {\n  match 1 {\n    1 if "x" => print("ok")\n    _ => print("no")\n  }\n}\n',
         "W2001", "expected 'bool', found 'str'", ":3:10 -"),
        ('fn f() -> int { return "x" }\n',
         "W2004", "expected 'int', found 'str'", ":1:24 -"),
        ('fn f() -> int { "x" }\n',
         "W2004", "expected 'int', found 'str'", ":1:15 -"),
        ('fn main() {\n  let values: list[int] = [1]\n  let item = values["x"]\n}\n',
         "W2001", "expected 'int', found 'str'", ":3:21 -"),
    ]
    for index, (text, code, expected, location) in enumerate(cases):
        source = tmp_path / f"location-{index}.wysna"
        output = tmp_path / f"location-{index}.c"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run([compiler, str(source), str(output)], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode != 0
        assert f"[{code}]" in result.stderr
        assert expected in result.stderr
        assert location in result.stderr, result.stderr
        assert not output.exists()


def test_native_compiler_locates_member_and_declaration_errors(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    cases = [
        ('fn main() {\n  print(-"x")\n}\n',
         "W2001", "numeric negation", ":2:10 -"),
        ('fn main() {\n  let values = [1]\n  values.append(2)\n}\n',
         "W3001", "mutation of immutable binding", ":3:10 -"),
        ('struct Point { x: int }\nfn main() {\n  let p = Point { x: 1 }\n  print(p.y)\n}\n',
         "W3002", "unknown member: y", ":4:11 -"),
        ('struct Point { x: int, y: int }\nfn main() {\n  let p = Point { x: 1 }\n}\n',
         "W2007", "missing struct field", ":3:11 -"),
        ('extern "C" fn bad(value: str) -> unit\n',
         "W2001", "FFI accepts only scalars and opaque handles", ":1:1 -"),
        ('fn main() {\n  let f = |x| x\n}\n',
         "W2001", "lambda parameter needs type context", ":2:12 -"),
        ('fn main() -> int {\n  let value = 1?\n  return value\n}\n',
         "W2006", "propagation operand", ":2:16 -"),
    ]
    for index, (text, code, expected, location) in enumerate(cases):
        source = tmp_path / f"declaration-{index}.wysna"
        output = tmp_path / f"declaration-{index}.c"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run([compiler, str(source), str(output)], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode != 0
        assert f"[{code}]" in result.stderr
        assert expected in result.stderr
        assert location in result.stderr, result.stderr
        assert not output.exists()


def test_native_compiler_points_to_unknown_names(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    cases = [
        ('let value = missing\n', 'unknown identifier: missing', ':1:13 -'),
        ('print(Option.Missing)\n', 'unknown built-in enum variant: Option.Missing', ':1:14 -'),
        ('enum Event { Data(int) }\nlet item = Data\n', 'enum variant requires a payload: Data', ':2:12 -'),
    ]
    for index, (text, expected, location) in enumerate(cases):
        source = tmp_path / f"unknown-{index}.wysna"
        output = tmp_path / f"unknown-{index}.c"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run([compiler, str(source), str(output)], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode != 0
        assert expected in result.stderr and location in result.stderr, result.stderr
        assert not output.exists()


def test_native_compiler_emits_demangled_actionable_diagnostics(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    cases = [
        (
            "enum-member.wysna",
            'import "std/json" as json\nfn main() -> unit {\n  let d = json.parse("{}") else err { return }\n  let _x = d.get("k")\n}\n',
            ["[W1001]", "enum 'Value' has no member or variant 'get'"],
            ["__wysna_module_"],
        ),
        (
            "expected-brace.wysna",
            'fn main() -> unit {\n  if true print(1)\n}\n',
            ["[W0006]", "expected '{'", "found 'print'"],
            ["expected LBRACE"],
        ),
        (
            "list-method.wysna",
            'fn main() -> unit {\n  let mut xs = [1]\n  xs.push(2)\n}\n',
            ["[W3002]", "unknown list method 'push'", "append"],
            [],
        ),
        (
            "err-wrap-hint.wysna",
            'fn f() -> int ! IoError {\n  return IoError { message: "bad" }\n}\n',
            ["[W2004]", "Wrap the error value with Err(...)"],
            [],
        ),
        (
            "dup-struct.wysna",
            'struct Dup { a: int }\nstruct Dup { b: int }\n',
            ["[W1002]", "duplicate type: Dup", "Rename or remove the duplicate declaration"],
            ["Check for missing delimiters"],
        ),
        (
            "coalesce-hint.wysna",
            'fn main() -> unit {\n  let _x = 1 ?? 2\n}\n',
            ["[W2006]", "found 'int'"],
            [],
        ),
    ]
    for filename, text, required, forbidden in cases:
        source = tmp_path / filename
        output = tmp_path / f"{filename}.c"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run(
            [compiler, "--stdlib-root", str(ROOT / "stdlib"), str(source), str(output)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode != 0, filename
        for needle in required:
            assert needle in result.stderr, f"{filename}: expected {needle!r} in {result.stderr!r}"
        for bad in forbidden:
            assert bad not in result.stderr, f"{filename}: unexpected {bad!r} in {result.stderr!r}"
        assert not output.exists()


def test_native_fix_apply_wraps_bare_error_return_and_emits_u8_hint(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))

    err_src = tmp_path / "wrap_err.wysna"
    err_src.write_text('fn f() -> int ! IoError {\n  return IoError { message: "bad" }\n}\nfn main() -> unit {}\n', encoding="utf-8")
    fixed = subprocess.run([compiler, "fix", "--apply", str(err_src)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert fixed.returncode == 0, fixed.stderr + fixed.stdout
    rewritten = err_src.read_text(encoding="utf-8")
    assert 'return Err(IoError { message: "bad" })' in rewritten

    checked = subprocess.run([compiler, "check", str(err_src)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert checked.returncode == 0, checked.stderr

    u8_src = tmp_path / "u8_hint.wysna"
    u8_src.write_text('fn main() -> unit {\n  let n: int = 12\n  let _b: u8 = n\n}\n', encoding="utf-8")
    u8_res = subprocess.run([compiler, str(u8_src), str(tmp_path / "u8_hint.c")], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert u8_res.returncode != 0
    assert "[W2001]" in u8_res.stderr
    assert "u8(x) ?? 0" in u8_res.stderr

    u8_json = subprocess.run([compiler, "check", "--output-format=json-v1", str(u8_src)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert u8_json.returncode != 0
    assert "u8(x) ?? 0" in u8_json.stdout


def test_native_w2006_result_coalesce_and_w1001_module_type_qualification(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))

    # 1. W2006 when using ?? on T ! E (e.g. json.get_str)
    coal_src = tmp_path / "coalesce_result.wysna"
    coal_src.write_text(
        'import "std/json" as json\n'
        'fn main() -> unit {\n'
        '  let doc = json.parse("{\\"k\\":\\"v\\"}") else _ { return }\n'
        '  let _s = json.get_str(doc, "k") ?? ""\n'
        '}\n',
        encoding="utf-8",
    )
    coal_human = subprocess.run(
        [compiler, "--stdlib-root", str(ROOT / "stdlib"), str(coal_src), str(tmp_path / "coal.c")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert coal_human.returncode != 0
    assert "[W2006]" in coal_human.stderr
    assert "else err" in coal_human.stderr
    assert "expr?" in coal_human.stderr

    coal_json_res = subprocess.run(
        [compiler, "--stdlib-root", str(ROOT / "stdlib"), "check", "--output-format=json-v1", str(coal_src)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert coal_json_res.returncode != 0
    assert '"code":"W2006"' in coal_json_res.stdout
    assert '"kind":"coalesce_result"' in coal_json_res.stdout
    assert "else err" in coal_json_res.stdout

    # 2. W1001 unqualified imported module type (JsonError -> json.JsonError) + fix --apply
    import json as py_json

    mod_src = tmp_path / "unqualified_json_err.wysna"
    mod_src.write_text(
        'import "std/json" as json\n'
        'fn read_it(raw: str) -> str ! JsonError {\n'
        '  let v = json.parse(raw)?\n'
        '  return json.get_str(v, "k")\n'
        '}\n'
        'fn main() -> unit {}\n',
        encoding="utf-8",
    )
    mod_json_res = subprocess.run(
        [compiler, "--stdlib-root", str(ROOT / "stdlib"), "check", "--output-format=json-v1", str(mod_src)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert mod_json_res.returncode != 0
    payload = py_json.loads(mod_json_res.stdout)
    diag = payload["diagnostics"][0]
    assert diag["code"] == "W1001"
    assert diag["data"]["kind"] == "unqualified_module_type"
    assert "json." in diag["data"]["help"] or "mod.JsonError" in diag["data"]["help"]
    assert diag["fixes"] and diag["fixes"][0]["applicability"] == "machine"
    assert diag["fixes"][0]["edits"][0]["replacement"] == "json.JsonError"

    fixed = subprocess.run(
        [compiler, "--stdlib-root", str(ROOT / "stdlib"), "fix", "--apply", str(mod_src)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert fixed.returncode == 0, fixed.stderr + fixed.stdout
    assert "-> str ! json.JsonError" in mod_src.read_text(encoding="utf-8")

    rechecked = subprocess.run(
        [compiler, "--stdlib-root", str(ROOT / "stdlib"), "check", str(mod_src)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rechecked.returncode == 0, rechecked.stderr

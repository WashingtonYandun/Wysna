"""Native compiler diagnostics must identify both sides of a type mismatch."""

from pathlib import Path
import subprocess

from compiler.Bootstrap import ensure_selfhost_compiler_binary

ROOT = Path(__file__).resolve().parents[2]


def test_native_compiler_reports_expected_and_found_types(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    cases = [
        ('let value: int = "wrong"\n', "expected 'int', found 'str'", ":1:18 -"),
        ('let name: str = none\n', "expected 'str', found 'none'", ":1:17 -"),
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
        ('fn main() {\n  match 1 {\n    1 if "x" => print("ok")\n    * => print("no")\n  }\n}\n',
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

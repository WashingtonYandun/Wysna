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
         "expected 'list[int]', found 'list[str]'", None),
        ('fn use(value: int?) -> unit {}\nuse("wrong")\n', "expected 'int?', found 'str'", None),
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
        if location:
            assert location in result.stderr
        assert not output.exists()

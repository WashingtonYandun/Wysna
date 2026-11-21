"""Automated validation of documented Wysna examples, manifests, and API signatures."""

from pathlib import Path
import re

from common._vendor import tomli
from compiler.CompilerService import CompilerService
from project.Manifest import ProjectManifest

ROOT = Path(__file__).resolve().parents[2]

_FRAGMENT_MARKERS = (
    "app.domain",
    "lexer.wysna",
    "math_utils.wysna",
    "# COMPILE ERROR",
    "parse_port(",
    "Command",
    "kind: TokenKind",
    "temperature >",
    "is_admin",
    "process(",
    "expr?",
    "tok.kind",
    "for item in items",
    "enumerate(items)",
    "counts",
    "while condition",
    'read_file("src/main.wysna")',
    "fn read_file(path: str) -> str ! IoError",
    "fn parse() -> Config ! ParseError | IoError",
)


def _standalone_wysna_blocks(doc_path: Path):
    text = doc_path.read_text(encoding="utf-8")
    blocks = []
    for index, raw in enumerate(re.findall(r"```wysna\n(.*?)```", text, flags=re.S), 1):
        if any(marker in raw for marker in _FRAGMENT_MARKERS):
            continue
        blocks.append((index, raw))
    return blocks


def test_documented_wysna_blocks_typecheck():
    service = CompilerService(project_root=str(ROOT))
    checked_count = 0
    for rel_path in ("README.md", "WYSNA_SINTAXIS.md", "SPEC.md"):
        doc_path = ROOT / rel_path
        blocks = _standalone_wysna_blocks(doc_path)
        assert blocks, f"expected standalone wysna blocks in {rel_path}"
        for idx, code in blocks:
            service.check(code, f"{doc_path}#block{idx}")
            checked_count += 1
    assert checked_count >= 30


def test_projects_doc_manifest_targets_current_version(tmp_path):
    projects_doc = (ROOT / "docs/PROJECTS.md").read_text(encoding="utf-8")
    match = re.search(r"```toml\n(.*?)```", projects_doc, flags=re.S)
    assert match, "expected a TOML manifest block in docs/PROJECTS.md"
    raw = match.group(1)
    parsed = tomli.loads(raw)
    assert parsed["package"]["language"] == "0.8"
    assert parsed["toolchain"]["compiler"] == "0.8.0"
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.wysna").write_text("fn main() -> unit {}\n", encoding="utf-8")
    (tmp_path / "src/library.wysna").write_text("pub fn ping() -> int { return 1 }\n", encoding="utf-8")
    (tmp_path / "vendor/stats").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/valid_input.wysna").write_text("fn main() -> unit {}\n", encoding="utf-8")
    (tmp_path / "wysna.toml").write_text(raw, encoding="utf-8")
    manifest = ProjectManifest.load(tmp_path / "wysna.toml")
    assert manifest.language_version == "0.8"
    assert manifest.compiler_version == "0.8.0"


def test_stdlib_doc_signatures_match_api_contracts():
    stdlib_doc = (ROOT / "docs/STDLIB.md").read_text(encoding="utf-8")
    for expected in (
        "io.read_line() -> Line ! IoError",
        "io.read_all() -> str ! IoError",
        "io.flush() -> unit ! IoError",
        "http.batch_get(urls: list[str]) -> list[BatchItemResult]",
        "http.batch_get_all(urls: list[str]) -> list[Response] ! HttpError",
        "process.capture(executable, arguments, timeout_ms, max_output_bytes) -> ProcessOutput ! ProcessError",
        "`std/csv`",
        "fs.is_dir(path)",
        "fs.is_file(path)",
        "fs.list_dir(path)",
    ):
        assert expected in stdlib_doc, f"missing expected stdlib doc contract: {expected}"

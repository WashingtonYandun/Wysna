"""Wysna executable entry point and supported Python compatibility facade."""
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from common.Paths import get_repo_root
from compiler.Bootstrap import SELFHOST_BOOTSTRAP_INPUTS, ensure_selfhost_compiler_binary
from tooling.CLI import main, print_help, repl
from tooling.SourceCommand import run_pipeline


if __name__ == "__main__":
    main()

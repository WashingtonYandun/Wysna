"""Wysna executable entry point and supported Python compatibility facade."""
from common.Paths import get_repo_root
from compiler.Bootstrap import SELFHOST_BOOTSTRAP_INPUTS, ensure_selfhost_compiler_binary
from tooling.CLI import main, print_help, repl
from tooling.SourceCommand import run_pipeline


if __name__ == "__main__":
    main()

"""Locate the installed package root independently of the executable entry point."""
from pathlib import Path


def get_repo_root() -> str:
    return str(Path(__file__).resolve().parents[1])

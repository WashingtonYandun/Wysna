"""Locate the installed package root independently of the executable entry point."""
from pathlib import Path


def get_repo_root() -> str:
    path = Path(__file__).resolve()
    for parent in path.parents:
        if (parent / "pyproject.toml").is_file():
            return str(parent)
    if len(path.parents) > 2 and path.parents[1].name == "src":
        return str(path.parents[2])
    return str(path.parents[1])

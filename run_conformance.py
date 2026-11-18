"""Compatibility entry point delegating to ``src/run_conformance.py``."""
import importlib.util
from pathlib import Path
import sys

_CANONICAL_PATH = Path(__file__).resolve().parent / "src" / "run_conformance.py"


def _load_canonical_module():
    existing = sys.modules.get("src.run_conformance")
    if existing is not None and getattr(existing, "__file__", None) == str(_CANONICAL_PATH):
        return existing
    spec = importlib.util.spec_from_file_location("run_conformance", _CANONICAL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load canonical conformance runner from {_CANONICAL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_conformance"] = module
    sys.modules["src.run_conformance"] = module
    spec.loader.exec_module(module)
    return module


_module = _load_canonical_module()
if __name__ == "__main__":
    _module.run_suite()
else:
    sys.modules[__name__] = _module

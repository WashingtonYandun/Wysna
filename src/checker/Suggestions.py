"""Typo suggestions that turn "unknown name" errors into repairable diagnostics."""

import difflib
import re
from typing import Iterable, List

_CUTOFF = 0.6
_MACHINE_RATIO = 0.85


def visible_names(scope, attribute: str = "symbols") -> List[str]:
    """Names reachable from ``scope`` through its parent chain."""
    names = set()
    while scope is not None:
        names.update(getattr(scope, attribute, {}).keys())
        scope = scope.parent
    return sorted(names)


def closest_names(name: str, candidates: Iterable[str], limit: int = 3) -> List[str]:
    pool = sorted({c for c in candidates if isinstance(c, str) and c and c != name})
    return difflib.get_close_matches(name, pool, n=limit, cutoff=_CUTOFF)


def suggest(error, name: str, candidates: Iterable[str]):
    """Attach a "did you mean" hint, structured candidates and an edit to ``error``.

    The edit is ``machine`` applicable only when exactly one close candidate exists
    and it is very similar; otherwise it stays ``maybe`` so a tool never applies a
    guess silently.
    """
    matches = closest_names(name, candidates)
    if not matches:
        return error
    best = matches[0]
    ratio = difflib.SequenceMatcher(None, name, best).ratio()
    applicability = "machine" if len(matches) == 1 and ratio >= _MACHINE_RATIO else "maybe"
    error.with_data(kind="unknown_name", unknown=name, candidates=matches)
    if not error.help:
        error.help = "Did you mean " + " or ".join(f"'{m}'" for m in matches) + "?"
    error.with_fix(f"Replace '{name}' with '{best}'", old=name, new=best, applicability=applicability)
    return error


def suggest_mutable(error, sym, file: str):
    """Attach declaration provenance and a ``let mut`` edit when mutating an immutable binding."""
    if sym is None:
        return error
    sym_name = getattr(sym, "name", "")
    if sym_name in ("__wysna_environment", "__wysna_internal_environment"):
        error.with_data(kind="captured_snapshot_mutation")
        if not error.help:
            error.help = (
                "Closures capture values as immutable snapshots by design. "
                "Return the updated value from the closure. Classes cannot cross worker boundaries."
            )
        return error
    if not sym_name or sym_name.startswith("__wysna_"):
        return error
    decl_file = getattr(sym, "file", None) or file
    decl_line = getattr(sym, "line", None)
    decl_col = getattr(sym, "col", None)
    sym_kind = getattr(sym, "kind", "var")
    error.with_data(
        kind="immutable_mutation",
        binding=sym_name,
        symbol=sym_name,
        binding_kind=sym_kind,
        declared_line=decl_line,
        declared_col=decl_col,
    )
    if isinstance(decl_line, int) and isinstance(decl_col, int):
        error.with_related(
            f"'{sym_name}' declared as immutable {sym_kind} here",
            line=decl_line,
            col=decl_col,
            file=decl_file,
        )
        if sym_kind == "var":
            if not error.help:
                error.help = f"Declare '{sym_name}' as mutable: 'let mut {sym_name}'."
            error.with_fix(
                f"Declare '{sym_name}' as mutable ('let mut {sym_name}')",
                old=f"let {sym_name}",
                new=f"let mut {sym_name}",
                line=decl_line,
                col=decl_col,
                applicability="machine",
            )
    return error


_EXPECTED_FOUND = re.compile(r"expected '([^']+)', found '([^']+)'")
_ASSIGN_MISMATCH = re.compile(r"cannot assign value of type '([^']+)' to variable '[^']+' of type '([^']+)'")
_MISSING_FIELD = re.compile(r"Missing required field '([^']+)' in struct initialization of '([^']+)'")
_MANGLED_SYMBOL = re.compile(r"__wysna_module_([0-9a-f]+)__([A-Za-z_][A-Za-z0-9_]*)")


def _demangle_type_text(text: str) -> str:
    def replace(match):
        try:
            origin = bytes.fromhex(match.group(1)).decode("utf-8", errors="replace")
        except ValueError:
            return match.group(2)
        return f"{origin}::{match.group(2)}"
    return _MANGLED_SYMBOL.sub(replace, text)
_SCALAR_CASTS = {
    ("str", "int"): "str(...)",
    ("str", "float"): "str(...)",
    ("str", "bool"): "str(...)",
    ("int", "float"): "int(...)",
    ("int", "str"): "int(...)",
    ("float", "int"): "float(...)",
    ("float", "str"): "float(...)",
}


def _clean_symbol_name(text: str) -> str:
    return _MANGLED_SYMBOL.sub(lambda match: match.group(2), text)


def _annotate_mismatch(error, expected: str, found: str):
    expected = _demangle_type_text(expected)
    found = _demangle_type_text(found)
    error.with_data(kind="type_mismatch", expected=expected, found=found)
    if (expected, found) in _SCALAR_CASTS:
        error.with_data(conversion_hint=_SCALAR_CASTS[(expected, found)])
    elif found == f"{expected}?" or found.startswith(f"{expected} ! "):
        error.with_data(unwrap_hint="?")
        if not error.help:
            if found == f"{expected}?":
                requirement = "an optional return type"
            else:
                requirement = f"a result return type that accepts {found[len(expected) + 3:]}"
            error.help = (
                f"'?' can return early and requires {requirement} on the enclosing function. "
                "Use 'else' for a local fallback."
            )
    elif expected == "u8" and found == "int" and not error.help:
        error.help = "Use an integer literal in 0..255 with a u8 type context, or narrow checked with 'u8(x) ?? 0'."
    return error


def annotate_type_mismatch(error, expected: str, found: str, **extra):
    """Attach canonical mismatch facts plus call/context metadata."""
    _annotate_mismatch(error, expected, found)
    cleaned = {}
    for key, value in extra.items():
        if value is None:
            continue
        if key == "callee" and isinstance(value, str):
            cleaned[key] = _clean_symbol_name(value)
        elif isinstance(value, str):
            cleaned[key] = _demangle_type_text(value)
        else:
            cleaned[key] = value
    if cleaned:
        error.with_data(**cleaned)
    return error


def enrich_from_message(error):
    """Expose ``expected``/``found`` facts that the diagnostic message already states.

    Type rules phrase mismatches uniformly, so one extraction point gives every
    mismatch a structured form without duplicating it at each raise site.
    """
    if error.data.get("kind"):
        return error
    message = error.message
    match = _EXPECTED_FOUND.search(message)
    if match:
        return _annotate_mismatch(error, match.group(1), match.group(2))
    match = _ASSIGN_MISMATCH.search(message)
    if match:
        return _annotate_mismatch(error, match.group(2), match.group(1))
    match = _MISSING_FIELD.search(message)
    if match:
        error.with_data(kind="missing_field", field=match.group(1), struct=match.group(2))
    return error

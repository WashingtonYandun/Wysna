"""``wysna fix``: apply the machine-applicable edits attached to diagnostics.

The checker attaches candidate edits to diagnostics (``fixes``). This command turns
them into a corrected source in one step, so an agent does not spend a model round
trip on a typo the compiler already knows how to repair.

Safety rules:
* only edits whose ``applicability`` is allowed are used (``machine`` by default);
* an edit is applied only if the file still hashes to the ``source_sha256`` it was
  computed against;
* overlapping edits are dropped, never merged;
* without ``--apply`` the file is not modified.
"""

import argparse
import hashlib
import os
import sys

from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from tooling.SourceWrite import read_source_snapshot, write_verified_source
from tooling.ToolResult import (
    diagnostic_from_error,
    diagnostic_record,
    tool_result,
    write_tool_result,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def collect_diagnostics(path, source, *, service=None):
    """Return all protocol diagnostics for ``source``; empty when it type-checks."""
    sources = {path: source, os.path.abspath(path): source, os.path.realpath(path): source}
    service = service or CompilerService()
    try:
        parsed = service.parse(source, path, recover=True)
        service.check_parsed(parsed)
    except WysnaError as error:
        sources.update(service.source_texts())
        cache = {}
        return [diagnostic_from_error(item, sources, span_cache=cache)
                for item in getattr(error, "diagnostics", (error,))]
    return []


def plan_edits(diagnostics, source, allowed=("machine",)):
    """Pick a non-overlapping, hash-verified edit set from diagnostic fixes."""
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    candidates = []
    for diagnostic in diagnostics:
        for fix in diagnostic.get("fixes", ()):
            if fix.get("applicability") not in allowed:
                continue
            edits = fix.get("edits", ())
            if edits and all(edit.get("source_sha256") == sha for edit in edits):
                candidates.append((diagnostic["code"], fix["description"], edits))
    chosen, taken, skipped = [], [], []
    for code, description, edits in candidates:
        spans = [(edit["start_byte"], edit["end_byte"]) for edit in edits]
        if any(a < d and c < b for a, b in spans for c, d in taken):
            skipped.append({"code": code, "description": description, "reason": "overlaps another edit"})
            continue
        taken.extend(spans)
        chosen.append({"code": code, "description": description, "edits": list(edits)})
    return chosen, skipped


def apply_edits(source, chosen):
    data = source.encode("utf-8")
    edits = sorted((edit for item in chosen for edit in item["edits"]),
                   key=lambda edit: edit["start_byte"], reverse=True)
    for edit in edits:
        data = data[:edit["start_byte"]] + edit["replacement"].encode("utf-8") + data[edit["end_byte"]:]
    return data.decode("utf-8")


def fix_source(path, source, *, include_maybe=False, service=None):
    """Compute the fix plan and corrected text without touching the filesystem."""
    service = service or CompilerService()
    allowed = ("machine", "maybe") if include_maybe else ("machine",)
    applied_all = []
    skipped_all = []
    current = source
    diagnostics = collect_diagnostics(path, current, service=service)
    diag_before = len(diagnostics)
    passes = 0
    while passes < 64 and diagnostics:
        chosen, skipped = plan_edits(diagnostics, current, allowed)
        skipped_all.extend(skipped)
        if not chosen:
            break
        fixed = apply_edits(current, chosen)
        if fixed == current:
            break
        applied_all.extend({"code": item["code"], "description": item["description"]} for item in chosen)
        current = fixed
        passes += 1
        diagnostics = collect_diagnostics(path, current, service=service)
    return {
        "applied": applied_all,
        "skipped": skipped_all,
        "changed": current != source,
        "fixed_source": current,
        "remaining_diagnostics": diagnostics,
        "diagnostics_before": diag_before,
    }


def apply_fix_to_file(
    path,
    *,
    include_maybe=False,
    apply=False,
    service=None,
    before_write_hook=None,
):
    """Compute fixes from an exact disk snapshot and atomically write if requested."""
    target_path, source, before_sha256 = read_source_snapshot(path)
    result = fix_source(str(target_path), source, include_maybe=include_maybe, service=service)
    if apply and result["changed"]:
        write_verified_source(
            target_path,
            result["fixed_source"],
            expected_sha256=before_sha256,
            stage="fix",
            before_lock_hook=before_write_hook,
        )
    return result, source


def run_fix_command(argv):
    parser = _Parser(prog="wysna fix", description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--apply", action="store_true", help="Write the corrected source back to the file")
    parser.add_argument("--include-maybe", action="store_true",
                        help="Also use fixes marked 'maybe' (heuristic suggestions)")
    parser.add_argument("--output-format", choices=("json-v1",))
    json_output = "--output-format=json-v1" in argv
    source_text = None
    source_path = None
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == "json-v1"
        source_path = options.source
        result, source_text = apply_fix_to_file(
            options.source,
            include_maybe=options.include_maybe,
            apply=options.apply,
        )
        still_failing = bool(result["remaining_diagnostics"])
        if json_output:
            payload = {k: v for k, v in result.items() if k != "fixed_source"}
            payload["written"] = bool(options.apply and result["changed"])
            if not options.apply:
                payload["fixed_source"] = result["fixed_source"]
            envelope = tool_result("fix", "diagnostic_error" if still_failing else "ok",
                                   1 if still_failing else 0,
                                   diagnostics=result["remaining_diagnostics"])
            envelope["result"] = payload
            write_tool_result(envelope, sys.stdout)
        else:
            verb = "Applied" if options.apply else "Would apply"
            for item in result["applied"]:
                print(f"{verb}: [{item['code']}] {item['description']}")
            if not result["applied"]:
                print("No machine-applicable fixes.")
            if still_failing:
                print(f"{len(result['remaining_diagnostics'])} diagnostic(s) remain; run 'wysna check'.",
                      file=sys.stderr)
        return 1 if still_failing else 0
    except WysnaError as error:
        if json_output:
            sources = {source_path: source_text} if source_path and source_text is not None else {}
            write_tool_result(tool_result("fix", "diagnostic_error", 1,
                                          diagnostics=[diagnostic_from_error(error, sources)]),
                              sys.stdout)
        else:
            print(f"[{error.code}] {error.message}", file=sys.stderr)
        return 1
    except (ValueError, OSError, UnicodeDecodeError) as error:
        message = str(error)
        if json_output:
            write_tool_result(tool_result("fix", "usage_error", 2,
                                          diagnostics=[diagnostic_record("W5002", "usage", message)]),
                              sys.stdout)
        else:
            print(f"wysna fix: {message}", file=sys.stderr)
        return 2

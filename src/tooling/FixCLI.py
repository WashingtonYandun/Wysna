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
    diagnostics = collect_diagnostics(path, source, service=service)
    allowed = ("machine", "maybe") if include_maybe else ("machine",)
    chosen, skipped = plan_edits(diagnostics, source, allowed)
    fixed = apply_edits(source, chosen) if chosen else source
    remaining = collect_diagnostics(path, fixed, service=service) if chosen else diagnostics
    return {
        "applied": [{"code": item["code"], "description": item["description"]} for item in chosen],
        "skipped": skipped,
        "changed": fixed != source,
        "fixed_source": fixed,
        "remaining_diagnostics": remaining,
        "diagnostics_before": len(diagnostics),
    }


def run_fix_command(argv):
    parser = _Parser(prog="wysna fix", description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--apply", action="store_true", help="Write the corrected source back to the file")
    parser.add_argument("--include-maybe", action="store_true",
                        help="Also use fixes marked 'maybe' (heuristic suggestions)")
    parser.add_argument("--output-format", choices=("json-v1",))
    json_output = "--output-format=json-v1" in argv
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == "json-v1"
        with open(options.source, "r", encoding="utf-8") as handle:
            source = handle.read()
        result = fix_source(options.source, source, include_maybe=options.include_maybe)
        if options.apply and result["changed"]:
            with open(options.source, "w", encoding="utf-8") as handle:
                handle.write(result["fixed_source"])
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
    except (ValueError, OSError, UnicodeDecodeError) as error:
        message = str(error)
        if json_output:
            write_tool_result(tool_result("fix", "usage_error", 2,
                                          diagnostics=[diagnostic_record("W5002", "usage", message)]),
                              sys.stdout)
        else:
            print(f"wysna fix: {message}", file=sys.stderr)
        return 2

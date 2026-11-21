"""Trace a symbol back to the project targets and tests that can reach it."""
from collections import defaultdict, deque
from pathlib import Path

from compiler.CompilerService import CompilerService
from project.Resolver import ProjectResolver


def project_impact(manifest_path, symbol, *, frozen=False):
    resolver = ProjectResolver.load(manifest_path)
    if frozen:
        resolver.verify_lock()
    manifest = resolver.manifest
    service = CompilerService(project_root=manifest.root, project_resolver=resolver)
    entries = [
        ("target", name, target.entry, target.profile)
        for name, target in sorted(manifest.targets.items())
    ]
    entries.extend(("test", test.name, test.entry, "native-cli") for test in manifest.tests)
    scopes = []
    matches = defaultdict(dict)
    for category, name, entry, profile in entries:
        path = (manifest.root / entry).resolve()
        checked = service.check_file(path)
        graph = service.graph(checked, include_effects=False)
        exports = checked.modules[str(path)].exports if profile == "browser-library" else ()
        scopes.append((category, name, entry, profile, path, graph, exports))
        for node in graph["nodes"]:
            if node["kind"] != "entrypoint":
                for field in ("id", "qualified_name", "name"):
                    if node[field] == symbol:
                        matches[field][_location(node)] = node

    candidates = next((matches[field] for field in ("id", "qualified_name", "name") if matches[field]), {})
    if not candidates:
        raise ValueError(f"Symbol '{symbol}' not found in the declared targets or tests")
    if len(candidates) != 1:
        labels = sorted(f"{node['file']}:{node['line']} ({node['qualified_name']})" for node in candidates.values())
        raise ValueError(f"Ambiguous symbol '{symbol}': " + ", ".join(labels))

    location, selected = next(iter(candidates.items()))
    results = []
    for category, name, entry, profile, path, graph, exports in scopes:
        result = _trace(path, profile, graph, exports, location, manifest.root)
        results.append({"kind": category, "name": name, "entry": entry, "profile": profile, **result})
    return {
        "schema_version": "wysna.project-impact/1",
        "symbol": {key: selected[key] for key in ("id", "name", "file", "line")},
        "entries": results,
    }


def _location(node):
    return node["file"], node["kind"], node["name"], node["line"], node["col"]


def _trace(path, profile, graph, exports, location, project_root):
    nodes = {node["id"]: node for node in graph["nodes"]}
    reverse = defaultdict(list)
    forward = defaultdict(set)
    for edge in graph["edges"]:
        reverse[edge["to"]].append(edge)
        forward[edge["from"]].add(edge["to"])
    roots = set()
    boundaries = []
    for node in nodes.values():
        if Path(node["file"]) != path:
            continue
        if profile == "browser-library":
            is_root = node["kind"] == "fn" and node["name"] in exports
        else:
            is_root = node["kind"] == "entrypoint" or (node["kind"] == "fn" and node["name"] == "main")
        if is_root:
            roots.add(node["id"])
    if profile == "browser-library":
        boundaries.append("javascript")
    if any(node["kind"] == "extern" and Path(node["file"]).is_relative_to(project_root)
           for node in nodes.values()):
        boundaries.append("c-ffi")

    reachable = roots.copy()
    pending = deque(roots)
    while pending:
        successors = forward[pending.popleft()] - reachable
        reachable.update(successors)
        pending.extend(successors)
    if any(nodes[node_id].get("indirect_call") for node_id in reachable):
        boundaries.append("indirect-call")

    target = next((node["id"] for node in nodes.values() if _location(node) == location), None)
    predecessors = {}
    if target is not None:
        predecessors[target] = None
        queue = deque([target])
        while queue:
            for edge in reverse[queue.popleft()]:
                caller = edge["from"]
                if caller not in predecessors:
                    predecessors[caller] = edge
                    queue.append(caller)

    paths = []
    for root in sorted(roots.intersection(predecessors)):
        chain = []
        cursor = root
        while predecessors[cursor] is not None:
            edge = predecessors[cursor]
            chain.append({key: edge[key] for key in ("from", "to", "kind")})
            cursor = edge["to"]
        paths.append({"entry_symbol": root, "chain": chain})
    status = "affected" if paths else "uncertain" if boundaries else "no-static-path"
    return {"affected": bool(paths), "impact_status": status,
            "paths": paths, "unanalyzed_boundaries": sorted(boundaries)}

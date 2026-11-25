"""Evaluate direct architecture contracts against a checked target snapshot."""
import hashlib
from pathlib import Path

from compiler.SemanticGraph import build_semantic_graph
from error.Error import WysnaError
from project.Manifest import ManifestError

_RELATIONS = ("calls", "uses_type")


def classify_roles(manifest, graph):
    """Bind roles by checked schema identity, even through renamed imports."""
    policy = manifest.architecture
    if policy is None:
        return {}
    schema = {"file": str((manifest.root / policy.schema_path).resolve()), "name": policy.schema_name}
    roles = {}
    for node in graph["nodes"]:
        if node["kind"] != "class" or not Path(node["file"]).is_relative_to(manifest.source_root):
            continue
        for attribute in node.get("attributes", ()):
            if attribute.get("schema") != schema:
                continue
            roles[node["id"]] = attribute["arguments"].get("role")
    return roles


def select_role(manifest, graph, role):
    if manifest.architecture is None or role not in manifest.architecture.roles:
        raise ManifestError(f"unknown architecture role '{role}'")
    roles = classify_roles(manifest, graph)
    return [dict(node, architecture_role=role) for node in graph["nodes"] if roles.get(node["id"]) == role]


def _symbol(node, root, nodes):
    owner = nodes.get(node.get("owner"))
    name = f"{owner['name']}.{node['name']}" if owner else node["name"]
    file = Path(node["file"])
    source = file.relative_to(root).as_posix() if file.is_relative_to(root) else file.as_posix()
    return f"{source}::{name}"


def _diagnostic(rule, graph, revision, root, nodes, origin, relation, site,
                target=None, to_role=None, boundary=None):
    source_id = _symbol(origin, root, nodes)
    target_id = _symbol(target, root, nodes) if target else None
    if boundary:
        code = "W6102"
        status = "incomplete"
        message = f"Architecture rule '{rule.id}' cannot verify {relation} from {source_id}: {boundary}"
    else:
        code = "W6101"
        status = "violation"
        message = f"Architecture rule '{rule.id}' forbids {relation} from {source_id} to {target_id}"
    error = WysnaError(message, file=site["file"], line=site["line"], col=site.get("col", 1),
                       code=code, stage="architecture").with_data(
        kind=f"architecture_{status}", status=status, rule_id=rule.id, relation=relation,
        from_symbol=source_id, from_role=rule.from_role, to_symbol=target_id, to_role=to_role,
        boundary=boundary, graph_hash=graph["graph_hash"], revision=revision,
    )
    if target:
        owner = nodes.get(target.get("owner"), target)
        if boundary == "unclassified_target":
            error.with_related("Class requiring an architecture role",
                               file=owner["file"], line=owner["line"], col=owner["col"])
        if boundary != "unclassified_target" or owner != target:
            error.with_related("Target declaration", file=target["file"], line=target["line"], col=target["col"])
    return error


def check_architecture(manifest, checked):
    """Return a versioned report and one representative diagnostic per failed rule."""
    policy = manifest.architecture
    if policy is None:
        return None, []
    graph = build_semantic_graph(checked)
    nodes = {node["id"]: node for node in graph["nodes"]}
    roles = classify_roles(manifest, graph)
    schema_file = str((manifest.root / policy.schema_path).resolve())
    schema_valid = any(
        node["kind"] == "struct" and node["file"] == schema_file and node["name"] == policy.schema_name
        and not node["type_parameters"]
        and any(field["name"] == "role" and field["type"] == "str" for field in node["fields"])
        for node in nodes.values()
    )
    revision = hashlib.sha256(f"{manifest.raw_sha256}:{graph['graph_hash']}".encode()).hexdigest()
    report = {"schema_version": "wysna.architecture/1", "graph_hash": graph["graph_hash"],
              "revision": revision, "rules": []}
    edges = sorted(graph["edges"], key=lambda edge: (
        edge.get("file", ""), edge.get("line", 0), edge["from"], edge["to"],
    ))
    issues = []
    for rule in policy.rules:
        incomplete = None
        violation = None

        def diagnostic(origin, relation, site, target=None, to_role=None, boundary=None):
            if "col" not in site and site["file"] in checked.sources:
                lines = checked.sources[site["file"]].text.splitlines()
                line = site["line"]
                if 1 <= line <= len(lines):
                    row = lines[line - 1]
                    site = dict(site, col=len(row) - len(row.lstrip(" \t")) + 1)
            return _diagnostic(rule, graph, revision, manifest.root, nodes,
                               origin, relation, site, target, to_role, boundary)

        if not schema_valid:
            location = {"name": policy.schema_name, "file": str(manifest.path), "line": 1, "col": 1}
            incomplete = diagnostic(location, rule.relations[0], location, boundary="missing_role_schema")

        for node_id, role in roles.items():
            if role not in policy.roles:
                node = nodes[node_id]
                incomplete = diagnostic(node, rule.relations[0], node, boundary="unknown_role")
                break

        for relation in _RELATIONS:
            if relation not in rule.relations or violation:
                continue
            for edge in edges:
                if edge["kind"] != relation:
                    continue
                source, target = nodes[edge["from"]], nodes[edge["to"]]
                if roles.get(source.get("owner", source["id"])) != rule.from_role:
                    continue
                target_class = nodes.get(target.get("owner"), target)
                if target_class["id"] == source.get("owner", source["id"]):
                    continue
                to_role = roles.get(target_class["id"])
                if to_role in rule.deny_roles:
                    violation = diagnostic(source, relation, edge, target, to_role)
                    break
                if (incomplete is None and to_role is None and target_class["kind"] == "class"
                        and Path(target_class["file"]).is_relative_to(manifest.source_root)):
                    incomplete = diagnostic(source, relation, edge, target, boundary="unclassified_target")

        if not violation and not incomplete and "calls" in rule.relations:
            for source in nodes.values():
                if roles.get(source.get("owner", source["id"])) != rule.from_role:
                    continue
                for boundary in source.get("call_resolution", {}).get("uncertain_calls", ()):
                    if boundary["kind"] == "extern_ffi":
                        continue
                    candidates = boundary.get("candidate_owners", ())
                    if candidates and not any(
                        roles.get(candidate) in rule.deny_roles or (
                            roles.get(candidate) is None
                            and Path(nodes[candidate]["file"]).is_relative_to(manifest.source_root)
                        ) for candidate in candidates
                    ):
                        continue
                    incomplete = diagnostic(source, "calls", boundary, boundary=boundary["kind"])
                    break
                if incomplete:
                    break

        issue = violation or incomplete
        report["rules"].append({"id": rule.id, "status": issue.data["status"] if issue else "verified"})
        if issue:
            issues.append(issue)
    report["status"] = ("violation" if any(issue.code == "W6101" for issue in issues)
                        else "incomplete" if issues else "verified")
    return report, issues

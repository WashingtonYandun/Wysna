"""
checker/LayoutGraph.py - Layout graph construction and finite size verification for Wysna.

Part of Milestone C04:
- Distinguishes between INLINE edges (direct value embedding in struct / tuple) and
  INDIRECT edges (heap box in enum variants, class references, weak references, list/dict buffers).
- Detects strongly connected components (SCC) on the subgraph formed exclusively by INLINE edges
  using Tarjan's algorithm.
- Rejects any cycle consisting solely of INLINE edges (infinite size layout) with diagnostic [W2011].
- Permits regular recursive ADTs (e.g. Tree[T], List[T]) where recursive self-references
  occur through indirect edges (such as enum variant payloads or class references).
"""

from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Any
from error.Error import TypeError
from node.NodeType import (
    Program, Stmt, TypeNode, NamedType, GenericType, TupleType as AstTupleType,
    ListType as AstListType, DictType as AstDictType, OptionalType as AstOptionalType,
    ResultType as AstResultType, StructDecl, EnumDecl, ClassDecl
)
from checker.WysnaType import (
    StructType, EnumType, ClassType
)


class EdgeKind(Enum):
    INLINE = "inline"
    INDIRECT = "indirect"
    STRONG = "strong"
    WEAK = "weak"


class LayoutEdge:
    def __init__(
        self,
        source: str,
        target: str,
        kind: EdgeKind,
        field_name: Optional[str] = None,
        line: int = 0,
        col: int = 0,
    ):
        self.source = source
        self.target = target
        self.kind = kind
        self.field_name = field_name
        self.line = line
        self.col = col

    def __repr__(self) -> str:
        return f"LayoutEdge({self.source} -> {self.target}, {self.kind.value}, field={self.field_name})"


class LayoutNode:
    def __init__(self, name: str, kind: str, line: int = 0, col: int = 0):
        self.name = name
        self.kind = kind  # "struct", "enum", "class", "primitive"
        self.line = line
        self.col = col
        self.edges: List[LayoutEdge] = []

    def add_edge(
        self,
        target: str,
        kind: EdgeKind,
        field_name: Optional[str] = None,
        line: int = 0,
        col: int = 0
    ) -> None:
        self.edges.append(LayoutEdge(self.name, target, kind, field_name, line, col))


class LayoutGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, LayoutNode] = {}

    def get_or_create_node(self, name: str, kind: str = "struct", line: int = 0, col: int = 0) -> LayoutNode:
        if name not in self.nodes:
            self.nodes[name] = LayoutNode(name, kind, line, col)
        return self.nodes[name]

    def add_edge(
        self,
        source: str,
        target: str,
        kind: EdgeKind,
        field_name: Optional[str] = None,
        line: int = 0,
        col: int = 0,
    ) -> None:
        src_node = self.get_or_create_node(source, line=line, col=col)
        self.get_or_create_node(target, line=line, col=col)
        src_node.add_edge(target, kind, field_name, line, col)

    def find_inline_sccs(self) -> List[List[str]]:
        """
        Finds strongly connected components on the subgraph formed exclusively
        by INLINE edges using Tarjan's algorithm.
        Returns only components with at least 2 nodes or 1 node with a self-loop.
        """
        index = 0
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        on_stack: Set[str] = set()
        stack: List[str] = []
        cyclic_sccs: List[List[str]] = []

        inline_adj: Dict[str, List[str]] = {name: [] for name in self.nodes}
        for name, node in self.nodes.items():
            for edge in node.edges:
                if edge.kind == EdgeKind.INLINE:
                    inline_adj[name].append(edge.target)

        def strongconnect(v: str) -> None:
            nonlocal index
            indices[v] = index
            lowlink[v] = index
            index += 1
            stack.append(v)
            on_stack.add(v)

            for w in inline_adj.get(v, []):
                if w not in indices:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], indices[w])

            if lowlink[v] == indices[v]:
                scc: List[str] = []
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    scc.append(w)
                    if w == v:
                        break
                if len(scc) > 1:
                    cyclic_sccs.append(scc)
                elif len(scc) == 1:
                    node_name = scc[0]
                    if node_name in inline_adj.get(node_name, []):
                        cyclic_sccs.append(scc)

        for name in sorted(self.nodes.keys()):
            if name not in indices:
                strongconnect(name)

        return cyclic_sccs

    def find_inline_cycle_path(self, scc: List[str]) -> Tuple[List[str], LayoutEdge]:
        """
        Extracts an elementary directed cycle within the given SCC.
        Returns (cycle_path, initial_edge).
        """
        scc_set = set(scc)
        inline_edges: Dict[str, List[LayoutEdge]] = {name: [] for name in scc}
        for name in scc:
            node = self.nodes.get(name)
            if node:
                for edge in node.edges:
                    if edge.kind == EdgeKind.INLINE and edge.target in scc_set:
                        inline_edges[name].append(edge)

        # Handle direct self-loop
        for name in sorted(scc):
            for edge in inline_edges.get(name, []):
                if edge.target == name:
                    return [name, name], edge

        # Find shortest cycle starting from smallest lexicographical node in SCC
        start = sorted(scc)[0]
        queue: List[Tuple[str, List[str], LayoutEdge]] = []
        for edge in inline_edges.get(start, []):
            queue.append((edge.target, [start, edge.target], edge))

        visited: Set[Tuple[str, str]] = set()
        while queue:
            curr, path, initial_edge = queue.pop(0)
            if curr == start:
                return path, initial_edge
            for edge in inline_edges.get(curr, []):
                next_node = edge.target
                if next_node == start:
                    return path + [start], initial_edge
                if (curr, next_node) not in visited and next_node in scc_set and next_node not in path:
                    visited.add((curr, next_node))
                    queue.append((next_node, path + [next_node], initial_edge))

        # Fallback
        default_edge = LayoutEdge(scc[0], scc[0], EdgeKind.INLINE)
        node = self.nodes.get(scc[0])
        if node and node.edges:
            default_edge = node.edges[0]
        return scc + [scc[0]], default_edge

    def validate_finite_layouts(self, file_path: str = "") -> None:
        """
        Validates that all types in the graph have finite layouts in memory.
        Raises TypeError(code="W2011") if any inline layout cycle is detected.
        """
        cyclic_sccs = self.find_inline_sccs()
        if not cyclic_sccs:
            return

        # Sort SCCs deterministically
        cyclic_sccs.sort(key=lambda scc: sorted(scc)[0])
        first_scc = cyclic_sccs[0]
        cycle_path, edge = self.find_inline_cycle_path(first_scc)
        cycle_str = " -> ".join(cycle_path)
        root_name = cycle_path[0]

        raise TypeError(
            message=(
                f"Recursive generic struct cycle detected for '{root_name}' without indirection: "
                f"cycle {cycle_str} has only inline edges without reference indirection"
            ),
            line=edge.line,
            col=edge.col,
            file=file_path,
            code="W2011"
        )

    def find_strong_class_sccs(self) -> List[List[str]]:
        """
        Finds strongly connected components on the subgraph formed exclusively
        by STRONG class-reference edges using Tarjan's algorithm.
        Returns cycles of classes that strongly reference each other without
        weak-reference indirection.
        """
        index = 0
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        on_stack: Set[str] = set()
        stack: List[str] = []
        cyclic_sccs: List[List[str]] = []

        class_nodes = {name for name, node in self.nodes.items() if node.kind == "class"}
        strong_adj: Dict[str, List[str]] = {name: [] for name in class_nodes}
        for name in class_nodes:
            node = self.nodes[name]
            for edge in node.edges:
                if edge.kind == EdgeKind.STRONG and edge.target in class_nodes:
                    strong_adj[name].append(edge.target)

        def strongconnect(v: str) -> None:
            nonlocal index
            indices[v] = index
            lowlink[v] = index
            index += 1
            stack.append(v)
            on_stack.add(v)

            for w in strong_adj.get(v, []):
                if w not in indices:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], indices[w])

            if lowlink[v] == indices[v]:
                scc: List[str] = []
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    scc.append(w)
                    if w == v:
                        break
                if len(scc) > 1:
                    cyclic_sccs.append(scc)
                elif len(scc) == 1:
                    node_name = scc[0]
                    if node_name in strong_adj.get(node_name, []):
                        cyclic_sccs.append(scc)

        for name in sorted(class_nodes):
            if name not in indices:
                strongconnect(name)

        return cyclic_sccs

    def find_strong_cycle_path(self, scc: List[str]) -> Tuple[List[str], List[LayoutEdge]]:
        """
        Extracts an elementary directed cycle within a strong class SCC along with its edges.
        Returns (cycle_path, edge_list).
        """
        scc_set = set(scc)
        if len(scc) == 1:
            name = scc[0]
            node = self.nodes.get(name)
            if node:
                for edge in node.edges:
                    if edge.kind == EdgeKind.STRONG and edge.target == name:
                        return [name, name], [edge]

        start = sorted(scc)[0]
        queue: List[Tuple[str, List[str], List[LayoutEdge]]] = []
        node = self.nodes.get(start)
        if node:
            for edge in node.edges:
                if edge.kind == EdgeKind.STRONG and edge.target in scc_set:
                    queue.append((edge.target, [start, edge.target], [edge]))

        visited: Set[Tuple[str, str]] = set()
        while queue:
            curr, path, edge_list = queue.pop(0)
            if curr == start:
                return path, edge_list
            curr_node = self.nodes.get(curr)
            if curr_node:
                for edge in curr_node.edges:
                    if edge.kind == EdgeKind.STRONG and edge.target in scc_set:
                        next_node = edge.target
                        if next_node == start:
                            return path + [start], edge_list + [edge]
                        if (curr, next_node) not in visited and next_node not in path:
                            visited.add((curr, next_node))
                            queue.append((next_node, path + [next_node], edge_list + [edge]))

        # Fallback
        return scc + [scc[0]], []

    def explain_strong_reference_cycles(self) -> List[Dict[str, Any]]:
        """
        Returns structured diagnostic advisories for all detected strong reference cycles
        among classes, detailing cycle paths, field names, and weak-reference remediation.
        """
        sccs = self.find_strong_class_sccs()
        reports: List[Dict[str, Any]] = []
        for scc in sccs:
            path, edge_list = self.find_strong_cycle_path(scc)
            if edge_list:
                chain = " -> ".join([f"{e.source}.{e.field_name} ({e.target})" for e in edge_list])
            else:
                chain = " -> ".join(path)
            reports.append({
                "classes": sorted(scc),
                "path": path,
                "edges": edge_list,
                "is_mutual": len(scc) > 1,
                "chain": chain,
                "advisory": (
                    f"Strong reference cycle detected across classes: {chain}. "
                    f"Under ARC, mutual strong references cause permanent memory retention. "
                    f"Consider breaking the cycle by annotating back-references with 'Weak[T]'."
                )
            })
        return reports


def build_layout_graph_from_declarations(
    struct_decls: List[StructDecl],
    enum_decls: List[EnumDecl],
    class_decls: List[ClassDecl],
    known_enums: Optional[Set[str]] = None,
    known_classes: Optional[Set[str]] = None,
) -> LayoutGraph:
    """
    Constructs a LayoutGraph from explicit AST declarations.
    """
    graph = LayoutGraph()
    enum_names: Set[str] = set(known_enums or set())
    for e in enum_decls:
        enum_names.add(e.name)
        graph.get_or_create_node(e.name, kind="enum", line=e.line, col=e.col)

    class_names: Set[str] = set(known_classes or set())
    for c in class_decls:
        class_names.add(c.name)
        graph.get_or_create_node(c.name, kind="class", line=c.line, col=c.col)

    struct_names: Set[str] = {s.name for s in struct_decls}
    for s in struct_decls:
        graph.get_or_create_node(s.name, kind="struct", line=s.line, col=s.col)

    def classify_type_node(type_node: TypeNode, source_name: str, field_name: str, line: int, col: int) -> None:
        if isinstance(type_node, NamedType):
            target = type_node.name
            if target in enum_names or target in ("Option", "Result", "Weak"):
                graph.add_edge(source_name, target, EdgeKind.INDIRECT, field_name, line, col)
            elif target in class_names:
                graph.add_edge(source_name, target, EdgeKind.INDIRECT, field_name, line, col)
            elif target in struct_names or target == source_name:
                graph.add_edge(source_name, target, EdgeKind.INLINE, field_name, line, col)

        elif isinstance(type_node, GenericType):
            target = type_node.name
            if target in enum_names or target in ("Option", "Result", "Weak"):
                graph.add_edge(source_name, target, EdgeKind.INDIRECT, field_name, line, col)
            elif target in class_names:
                graph.add_edge(source_name, target, EdgeKind.INDIRECT, field_name, line, col)
            elif target in struct_names or target == source_name:
                graph.add_edge(source_name, target, EdgeKind.INLINE, field_name, line, col)
                for arg in getattr(type_node, "type_args", []):
                    classify_type_node(arg, source_name, field_name, line, col)

        elif isinstance(type_node, AstTupleType):
            # Tuples are laid out inline
            elems = getattr(type_node, "element_types", getattr(type_node, "elements", []))
            for idx, elem in enumerate(elems):
                classify_type_node(elem, source_name, f"{field_name}.{idx}", line, col)

        elif isinstance(type_node, (AstListType, AstDictType, AstOptionalType, AstResultType)):
            # Dynamic heap buffers or Option/Result enums are indirect
            if isinstance(type_node, AstListType):
                pass
            elif isinstance(type_node, AstDictType):
                pass
            elif isinstance(type_node, AstOptionalType):
                graph.add_edge(source_name, "Option", EdgeKind.INDIRECT, field_name, line, col)
            elif isinstance(type_node, AstResultType):
                graph.add_edge(source_name, "Result", EdgeKind.INDIRECT, field_name, line, col)

    def extract_class_target(type_node: TypeNode) -> Tuple[Optional[str], bool]:
        if isinstance(type_node, NamedType):
            if type_node.name in class_names:
                return type_node.name, False
        elif isinstance(type_node, GenericType):
            name_lower = type_node.name.lower()
            if name_lower == "weak" and type_node.type_args:
                inner_target, _ = extract_class_target(type_node.type_args[0])
                if inner_target:
                    return inner_target, True
            elif type_node.name in ("Option", "Result", "list") and type_node.type_args:
                return extract_class_target(type_node.type_args[0])
            elif type_node.name in class_names:
                return type_node.name, False
        elif isinstance(type_node, AstOptionalType):
            return extract_class_target(type_node.inner)
        elif isinstance(type_node, AstListType):
            return extract_class_target(type_node.element_type)
        return None, False

    # 1. Process struct fields
    for s in struct_decls:
        for f in s.fields:
            classify_type_node(f.type_ann, s.name, f.name, f.line, f.col)

    # 2. Process enum variants (all variant payloads are heap-indirect in Wysna)
    for e in enum_decls:
        for v in e.variants:
            if hasattr(v, "payload_types") and v.payload_types:
                for pt in v.payload_types:
                    target_name = getattr(pt, "name", None)
                    if target_name:
                        graph.add_edge(e.name, target_name, EdgeKind.INDIRECT, v.name, v.line, v.col)

    # 3. Process class fields (distinguishing strong ownership from weak references)
    for c in class_decls:
        for f in c.fields:
            target_class, is_weak = extract_class_target(f.type_ann)
            if target_class and target_class in class_names:
                edge_kind = EdgeKind.WEAK if is_weak else EdgeKind.STRONG
                graph.add_edge(c.name, target_class, edge_kind, f.name, f.line, f.col)
            else:
                classify_type_node(f.type_ann, c.name, f.name, f.line, f.col)

    return graph

"""Verify static detection of strong reference cycles and weak-reference health."""
import unittest

from checker.LayoutGraph import (
    EdgeKind,
    LayoutGraph,
    build_layout_graph_from_declarations,
)
from compiler.CompilerService import CompilerService
from node.NodeType import (
    ClassDecl,
    ClassField,
    GenericType,
    NamedType,
    OptionalType,
)


class StrongReferenceCyclesTests(unittest.TestCase):
    def test_mutual_strong_cycle_detected(self):
        c1 = ClassDecl(
            name="Parent",
            superclass=None,
            interfaces=[],
            fields=[ClassField(name="child", type_ann=NamedType("Child"))],
            methods=[],
        )
        c2 = ClassDecl(
            name="Child",
            superclass=None,
            interfaces=[],
            fields=[ClassField(name="parent", type_ann=NamedType("Parent"))],
            methods=[],
        )

        graph = build_layout_graph_from_declarations([], [], [c1, c2])
        sccs = graph.find_strong_class_sccs()
        self.assertEqual(len(sccs), 1)
        self.assertEqual(sorted(sccs[0]), ["Child", "Parent"])

        advisories = graph.explain_strong_reference_cycles()
        self.assertEqual(len(advisories), 1)
        adv = advisories[0]
        self.assertTrue(adv["is_mutual"])
        self.assertIn("Child", adv["classes"])
        self.assertIn("Parent", adv["classes"])
        self.assertIn("Weak[T]", adv["advisory"])

    def test_cycle_broken_by_weak_reference(self):
        c1 = ClassDecl(
            name="Parent",
            superclass=None,
            interfaces=[],
            fields=[ClassField(name="child", type_ann=NamedType("Child"))],
            methods=[],
        )
        c2 = ClassDecl(
            name="Child",
            superclass=None,
            interfaces=[],
            fields=[
                ClassField(
                    name="parent",
                    type_ann=GenericType("Weak", [NamedType("Parent")]),
                )
            ],
            methods=[],
        )

        graph = build_layout_graph_from_declarations([], [], [c1, c2])
        sccs = graph.find_strong_class_sccs()
        self.assertEqual(len(sccs), 0)
        self.assertEqual(len(graph.explain_strong_reference_cycles()), 0)

    def test_cycle_broken_by_optional_weak_reference(self):
        c1 = ClassDecl(
            name="Parent",
            superclass=None,
            interfaces=[],
            fields=[
                ClassField(name="child", type_ann=OptionalType(NamedType("Child")))
            ],
            methods=[],
        )
        c2 = ClassDecl(
            name="Child",
            superclass=None,
            interfaces=[],
            fields=[
                ClassField(
                    name="parent",
                    type_ann=OptionalType(GenericType("Weak", [NamedType("Parent")])),
                )
            ],
            methods=[],
        )

        graph = build_layout_graph_from_declarations([], [], [c1, c2])
        sccs = graph.find_strong_class_sccs()
        self.assertEqual(len(sccs), 0)
        self.assertEqual(len(graph.explain_strong_reference_cycles()), 0)

    def test_multi_class_cycle(self):
        a = ClassDecl(
            name="A",
            superclass=None,
            interfaces=[],
            fields=[ClassField(name="b", type_ann=NamedType("B"))],
            methods=[],
        )
        b = ClassDecl(
            name="B",
            superclass=None,
            interfaces=[],
            fields=[ClassField(name="c", type_ann=NamedType("C"))],
            methods=[],
        )
        c = ClassDecl(
            name="C",
            superclass=None,
            interfaces=[],
            fields=[ClassField(name="a", type_ann=NamedType("A"))],
            methods=[],
        )

        graph = build_layout_graph_from_declarations([], [], [a, b, c])
        sccs = graph.find_strong_class_sccs()
        self.assertEqual(len(sccs), 1)
        self.assertEqual(sorted(sccs[0]), ["A", "B", "C"])

        advisories = graph.explain_strong_reference_cycles()
        self.assertEqual(len(advisories), 1)
        self.assertTrue(advisories[0]["is_mutual"])

    def test_self_loop_strong_reference(self):
        node = ClassDecl(
            name="Node",
            superclass=None,
            interfaces=[],
            fields=[ClassField(name="next", type_ann=OptionalType(NamedType("Node")))],
            methods=[],
        )

        graph = build_layout_graph_from_declarations([], [], [node])
        sccs = graph.find_strong_class_sccs()
        self.assertEqual(len(sccs), 1)
        self.assertEqual(sccs[0], ["Node"])

        advisories = graph.explain_strong_reference_cycles()
        self.assertEqual(len(advisories), 1)
        self.assertFalse(advisories[0]["is_mutual"])

    def test_compiler_service_integration(self):
        source = (
            "class Parent {\n"
            "    public child: Child?\n"
            "    fn init() { this.child = none }\n"
            "}\n"
            "class Child {\n"
            "    public parent: Parent?\n"
            "    fn init() { this.parent = none }\n"
            "}\n"
        )
        service = CompilerService()
        checked = service.check_source(source)
        advisories = service.analyze_reference_cycles(checked)
        self.assertEqual(len(advisories), 1)
        self.assertTrue(advisories[0]["is_mutual"])
        self.assertIn("Parent", advisories[0]["classes"])
        self.assertIn("Child", advisories[0]["classes"])


if __name__ == "__main__":
    unittest.main()

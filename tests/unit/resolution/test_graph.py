"""BusinessGraph shape + validation (pure)."""
from __future__ import annotations

from primeqa.resolution.graph import BusinessGraph, GraphEdge, GraphNode


def _g(nodes, edges=()):
    return BusinessGraph(nodes=tuple(nodes), edges=tuple(edges))


def test_wellformed_graph_validates_empty():
    g = _g([GraphNode("subject", "entity", "Order"),
            GraphNode("f0", "attribute", "priority")],
           [GraphEdge("attribute_of", "f0", "subject")])
    assert g.validate() == []


def test_duplicate_and_unknown_are_reported():
    g = _g([GraphNode("a", "entity", "x"), GraphNode("a", "entity", "y"),
            GraphNode("b", "widget", " ")],
           [GraphEdge("attribute_of", "a", "missing"),
            GraphEdge("teleports_to", "a", "b")])
    problems = g.validate()
    assert any("duplicate node_id" in p for p in problems)
    assert any("unknown kind 'widget'" in p for p in problems)
    assert any("empty term" in p for p in problems)
    assert any("unknown node 'missing'" in p for p in problems)
    assert any("unknown kind 'teleports_to'" in p for p in problems)


def test_traversal_helpers_are_deterministic():
    g = _g([GraphNode("subject", "entity", "Order"),
            GraphNode("f0", "attribute", "priority"),
            GraphNode("f1", "attribute", "status"),
            GraphNode("s0", "state", "Submitted")],
           [GraphEdge("attribute_of", "f0", "subject"),
            GraphEdge("attribute_of", "f1", "subject"),
            GraphEdge("state_of", "s0", "f1")])
    assert [n.node_id for n in g.attributes_of("subject")] == ["f0", "f1"]
    assert [n.node_id for n in g.states_of("f1")] == ["s0"]
    assert g.states_of("f0") == ()
    assert g.node("nope") is None

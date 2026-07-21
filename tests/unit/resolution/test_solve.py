"""Phase B — dominance ranking, grades, never-pick-on-ties, the fixture trap."""
from __future__ import annotations

from primeqa.resolution.graph import BusinessGraph, GraphEdge, GraphNode
from primeqa.resolution.resolved import (
    AMBIGUOUS, BOUND_UNIQUE, BOUND_WEAK, UNRESOLVED)
from primeqa.resolution.solve import dominant_entity, resolve
from tests.unit.resolution import world


def _graph(term: str, fields=(), states=()):
    """subject + attribute nodes (+ optional (attr_index, value) states)."""
    nodes = [GraphNode("subject", "entity", term)]
    edges = []
    for i, f in enumerate(fields):
        nodes.append(GraphNode(f"f{i}", "attribute", f))
        edges.append(GraphEdge("attribute_of", f"f{i}", "subject"))
    for j, (i, v) in enumerate(states):
        nodes.append(GraphNode(f"s{j}", "state", v))
        edges.append(GraphEdge("state_of", f"s{j}", f"f{i}"))
    return BusinessGraph(nodes=tuple(nodes), edges=tuple(edges))


def test_the_wrong_but_real_trap_resolves_by_structural_coverage():
    """Order__c + the org's own field vocabulary must bind PLS_FB_Order__c,
    not the standard Order object — the D-363/D-364 class."""
    t = world.table()
    g = _graph("Order__c", fields=("Priority__c", "Tier__c"),
               states=((0, "High"),))
    r = resolve(g, t, requirement_text="high priority orders escalate")
    b = r.binding("subject")
    assert b.grade == BOUND_UNIQUE
    assert b.sf_api_name == "PLS_FB_Order__c"
    assert b.structural_coverage == (3, 3)
    assert b.matched_via == "joint"
    # the dependent nodes bound through the winner
    assert r.binding("f0").sf_api_name == "PLS_FB_Priority__c"
    assert r.binding("s0").sf_api_name == "High"
    assert dominant_entity(r, "subject") is b


def test_exact_api_name_wins_nominally_without_field_evidence():
    t = world.table()
    r = resolve(_graph("Order"), t)
    b = r.binding("subject")
    assert b.grade == BOUND_UNIQUE            # exact api + exact label
    assert b.sf_api_name == "Order"
    assert b.matched_via == "exact_api"


def test_exact_label_wins_nominally():
    t = world.table()
    r = resolve(_graph("PLS FB Order"), t)
    b = r.binding("subject")
    assert b.grade == BOUND_UNIQUE
    assert b.sf_api_name == "PLS_FB_Order__c"
    assert b.matched_via == "exact_label"


def test_pure_lexical_win_is_bound_weak():
    # only lexical evidence, no exact match, no field mentions: WorkOrder-ish
    t = world.table(world.work_order())
    r = resolve(_graph("Work Orders"), t)
    b = r.binding("subject")
    assert b.grade in (BOUND_UNIQUE, BOUND_WEAK)
    # exact-label normalization makes "Work Orders" NOT equal "Work Order";
    # with no structural evidence the win must be WEAK
    assert b.grade == BOUND_WEAK
    assert b.sf_api_name == "WorkOrder"


def test_tie_is_ambiguous_never_picked():
    from uuid import uuid4
    from primeqa.resolution.symbols import ObjectSymbol
    twin_a = ObjectSymbol(entity_id=uuid4(), api_name="Alpha_Order__c",
                          label="Alpha Order")
    twin_b = ObjectSymbol(entity_id=uuid4(), api_name="Alpha_Ordeq__c",
                          label="Alpha Ordeq")
    t = world.table(twin_a, twin_b)
    # same token distance to both twins, no context, no fields
    r = resolve(_graph("Alpha_Ordex__c"), t)
    b = r.binding("subject")
    assert b.grade == AMBIGUOUS
    assert b.sf_api_name is None
    assert len(b.candidates) == 2
    assert dominant_entity(r, "subject") is None


def test_no_candidate_is_unresolved():
    r = resolve(_graph("Zebra_Quantum__c"), world.table())
    assert r.binding("subject").grade == UNRESOLVED
    assert dominant_entity(r, "subject") is None


def test_malformed_graph_resolves_all_unresolved_without_raising():
    g = BusinessGraph(nodes=(GraphNode("a", "widget", "x"),), edges=())
    r = resolve(g, world.table())
    assert r.binding("a").grade == UNRESOLVED


def test_effect_entity_resolves_independently():
    from tests.unit.resolution.world import fld
    from primeqa.resolution.symbols import ObjectSymbol
    from uuid import uuid4
    task = ObjectSymbol(
        entity_id=uuid4(), api_name="PLS_FB_Fulfilment_Task__c",
        label="PLS FB Fulfilment Task", is_custom=True,
        fields=(fld("PLS_FB_Priority__c", "PLS_FB_Fulfilment_Task__c",
                    "Priority"),))
    t = world.table(world.standard_order(), world.fb_order(), task)
    g = BusinessGraph(
        nodes=(GraphNode("subject", "entity", "Order__c"),
               GraphNode("effect", "entity", "Fulfilment Task"),
               GraphNode("f0", "attribute", "Tier__c"),
               GraphNode("f1", "attribute", "Priority__c")),
        edges=(GraphEdge("effect_on", "subject", "effect"),
               GraphEdge("attribute_of", "f0", "subject"),
               GraphEdge("attribute_of", "f1", "effect")))
    r = resolve(g, t)
    assert r.binding("subject").sf_api_name == "PLS_FB_Order__c"
    assert r.binding("effect").sf_api_name == "PLS_FB_Fulfilment_Task__c"
    assert r.binding("f1").sf_api_name == "PLS_FB_Priority__c"


def test_resolution_is_deterministic():
    t = world.table()
    g = _graph("Order__c", fields=("Priority__c",))
    r1 = resolve(g, t, requirement_text="ctx").to_json()
    r2 = resolve(g, t, requirement_text="ctx").to_json()
    # entity ids differ per-table build; compare the decision surface
    for r in (r1, r2):
        for b in r["bindings"].values():
            b.pop("entity_id", None)
    assert r1 == r2

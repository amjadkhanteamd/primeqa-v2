"""D-376 shadow resolution — adapter reconstruction + verdict classes +
the would_veto truth table (pure, DB-free)."""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation import shadow_resolution as sr
from primeqa.resolution.solve import resolve
from primeqa.resolution.symbols import FieldSymbol, ObjectSymbol, SymbolTable

# (FieldSymbol used directly below for relationship-carrying fixtures)


def _fld(bare, obj, label, values=()):
    return FieldSymbol(entity_id=uuid4(), api_name=bare,
                       qualified_api_name=f"{obj}.{bare}", label=label,
                       picklist_values=values)


def _table():
    order = ObjectSymbol(
        entity_id=uuid4(), api_name="Order", label="Order",
        fields=(_fld("Status", "Order", "Status",
                     (("Draft", "Draft"), ("Activated", "Activated"))),))
    fb = ObjectSymbol(
        entity_id=uuid4(), api_name="PLS_FB_Order__c", label="PLS FB Order",
        is_custom=True,
        fields=(_fld("PLS_FB_Priority__c", "PLS_FB_Order__c", "Priority",
                     (("Low", "Low"), ("High", "High"))),
                _fld("PLS_FB_Status__c", "PLS_FB_Order__c", "Status",
                     (("Draft", "Draft"), ("Submitted", "Submitted"))),
                _fld("PLS_FB_Tier__c", "PLS_FB_Order__c", "Tier")))
    return SymbolTable([order, fb], at_seq=9)


def _desc(hint, archetype="data_behavior", claim_kind="value-claim"):
    return {"archetype_hint": archetype, "claim_kind_hint": claim_kind,
            "target_subject_hint": hint, "ac_ref": "AC1"}


# -- adapter reconstruction ---------------------------------------------------

def test_adapter_builds_subject_fields_and_states():
    g = sr.business_graph_from_intent(_desc({
        "object": "Order__c", "field_name": "Priority__c",
        "expected_value": "High",
        "trigger_fields": [{"field_name": "Tier__c", "value": "Gold"}]}),
        "orders escalate")
    assert g is not None and g.validate() == []
    assert g.node("subject").term == "Order__c"
    assert [n.term for n in g.attributes_of("subject")] == [
        "Priority__c", "Tier__c"]
    (f0, f1) = g.attributes_of("subject")
    assert [n.term for n in g.states_of(f0.node_id)] == ["High"]
    assert [n.term for n in g.states_of(f1.node_id)] == ["Gold"]


def test_adapter_effect_object_owns_effect_field():
    g = sr.business_graph_from_intent(_desc({
        "object": "Order__c", "effect_object": "Fulfilment Task",
        "effect_field": "Priority__c", "effect_value": "High",
        "effect_via_lookup_field": "Order__c"},
        claim_kind="automation-effect-claim"), "x")
    assert g.node("effect").term == "Fulfilment Task"
    assert [n.term for n in g.attributes_of("effect")] == ["Priority__c"]
    assert [n.term for n in g.attributes_of("subject")] == ["Order__c"]


def test_adapter_dedups_field_terms_and_skips_non_strings():
    g = sr.business_graph_from_intent(_desc({
        "object": "Order__c", "field_name": "Priority__c",
        "expected_value": 50000,                       # number -> no state node
        "rejection_conditions": [{"field": "Priority__c"},
                                 {"field": "Amount__c"}, "junk"]}), "x")
    assert [n.term for n in g.attributes_of("subject")] == [
        "Priority__c", "Amount__c"]
    assert all(n.kind != "state" for n in g.nodes)


def test_adapter_skips_unobserved_shapes():
    assert sr.business_graph_from_intent(
        _desc({"object": "X"}, archetype="configuration")) is None
    assert sr.business_graph_from_intent(_desc(
        {"entity_type": "Field", "sf_api_name": "A.B"})) is None
    assert sr.business_graph_from_intent(_desc({})) is None
    assert sr.business_graph_from_intent({}) is None
    # automation_name is NEVER reconstructed (D-362 behavioural boundary)
    g = sr.business_graph_from_intent(_desc(
        {"object": "Order__c", "automation_name": "FL09"}))
    assert all("FL09" not in n.term for n in g.nodes)


# -- verdict + would_veto -----------------------------------------------------

def _verdict(hint, actual_outcome, actual_api, table=None,
             requirement_text=None):
    table = table or _table()
    g = sr.business_graph_from_intent(_desc(hint), "x")
    r = resolve(g, table, requirement_text=requirement_text)
    return sr.shadow_verdict(g, r, table, actual_outcome=actual_outcome,
                             actual_api=actual_api)


def test_verdict_agree():
    v = _verdict({"object": "Order"}, "resolved", "Order")
    assert v["agreement"] == "agree" and v["would_veto"] is False


def test_verdict_conflict_with_veto_on_structural_dominance():
    """The flagship trap: model named Order__c, pipeline would resolve it to
    a wrong-but-real object; every field mention binds ONLY on the winner."""
    v = _verdict({"object": "Order__c", "field_name": "Priority__c",
                  "trigger_fields": [{"field_name": "Tier__c", "value": "Gold"}]},
                 "resolved", "Order")
    assert v["agreement"] == "conflict"
    assert v["shadow"]["winner"] == "PLS_FB_Order__c"
    assert v["shadow"]["model_binds"] == 0
    assert v["shadow"]["winner_binds"] == 2
    assert v["would_veto"] is True
    assert sr.would_veto(v) is True
    assert v["veto_evidence"]["discriminators"] == [
        "PLS_FB_Priority__c", "PLS_FB_Tier__c"]


def test_no_veto_when_model_object_binds_some_mentions():
    """Shared vocabulary (Status exists on both) keeps the conservative
    predicate silent — the conflict class carries it to telemetry instead."""
    table = _table()
    v = _verdict({"object": "Order__c", "field_name": "Priority__c",
                  "trigger_fields": [{"field_name": "Status", "value": "x"}]},
                 "resolved", "Order", table=table)
    assert v["agreement"] == "conflict"
    assert v["shadow"]["model_binds"] == 1
    assert v["would_veto"] is False


def test_no_veto_on_foreign_qualified_mentions():
    """Replay FP class 1 (2026-07-21): a mention whose qualifier names
    ANOTHER object ('PLS_FB_Order_Line__c.Order__c' under subject
    PLS_FB_Order__c) self-declares cross-object framing — it carries no
    evidence about the subject, so the veto set is empty."""
    line = ObjectSymbol(
        entity_id=uuid4(), api_name="PLS_FB_Order_Line__c",
        label="PLS FB Order Line", is_custom=True,
        fields=(_fld("PLS_FB_Order__c", "PLS_FB_Order_Line__c", "Order"),))
    t = _table()
    t = SymbolTable(list(t.objects) + [line], at_seq=9)
    v = _verdict({"object": "PLS_FB_Order__c",
                  "field_name": "PLS_FB_Order_Line__c.Order__c"},
                 "resolved", "PLS_FB_Order__c", table=t)
    assert v["shadow"]["veto_mentions"] == []
    assert v["would_veto"] is False


def test_no_veto_when_winner_is_lookup_adjacent():
    """Replay FP class 2 (2026-07-21): the subject is right but the field
    lives on a lookup-adjacent object (cross-object effect framing). The
    winner being reachable via a relationship suppresses the veto; the case
    stays in the conflict telemetry class."""
    line = ObjectSymbol(
        entity_id=uuid4(), api_name="PLS_FB_Order_Line__c",
        label="PLS FB Order Line", is_custom=True,
        fields=(FieldSymbol(
            entity_id=uuid4(), api_name="PLS_FB_Order__c",
            qualified_api_name="PLS_FB_Order_Line__c.PLS_FB_Order__c",
            label="Order", references_object="PLS_FB_Order__c"),))
    t = _table()
    t = SymbolTable(list(t.objects) + [line], at_seq=9)
    v = _verdict({"object": "PLS_FB_Order_Line__c",
                  "field_name": "Priority__c"},
                 "resolved", "PLS_FB_Order_Line__c", table=t,
                 requirement_text="order line priority")
    assert v["agreement"] == "conflict"
    assert v["shadow"]["winner"] == "PLS_FB_Order__c"
    assert v["shadow"]["model_binds"] == 0
    assert v["would_veto"] is False          # adjacency suppression


def test_no_veto_without_field_mentions():
    v = _verdict({"object": "Order__c"}, "resolved", "Order")
    assert v["would_veto"] is False


def test_no_veto_when_winner_is_not_unique():
    # no candidates at all -> UNRESOLVED -> model_only, no veto
    v = _verdict({"object": "Zebra_Quantum__c", "field_name": "Priority__c"},
                 "resolved", "Order")
    assert v["agreement"] == "model_only"
    assert v["would_veto"] is False


def test_shadow_only_and_neither_classes():
    v = _verdict({"object": "PLS FB Order", "field_name": "Priority__c"},
                 "miss", None)
    assert v["agreement"] == "shadow_only"
    v2 = _verdict({"object": "Zebra_Quantum__c"}, "miss", None)
    assert v2["agreement"] == "neither"


def test_attach_payload_counts():
    v1 = _verdict({"object": "Order"}, "resolved", "Order")
    v2 = _verdict({"object": "Order__c", "field_name": "Priority__c",
                   "trigger_fields": [{"field_name": "Tier__c", "value": "g"}]},
                  "resolved", "Order")
    payload = sr.attach_payload([v1, v2])
    assert payload["version"] == sr.SHADOW_VERSION
    assert payload["counts"]["total"] == 2
    assert payload["counts"]["agree"] == 1
    assert payload["counts"]["conflict"] == 1
    assert payload["counts"]["would_veto"] == 1


def test_stash_dedups_reobserved_intents():
    from types import SimpleNamespace
    state = SimpleNamespace()
    v = _verdict({"object": "Order"}, "resolved", "Order")
    sr._stash_shadow_verdict(state, v)
    sr._stash_shadow_verdict(state, dict(v))          # D-247 re-prompt replay
    assert len(state.shadow_verdicts) == 1
    sr._stash_shadow_verdict(None, v)                 # tolerates None state

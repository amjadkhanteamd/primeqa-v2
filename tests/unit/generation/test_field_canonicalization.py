"""F1 (D-377) — subject-owned field-slot canonicalization.

The helper rewrites ONLY names the ladder resolves uniquely to a different
real name; unresolvable/ambiguous names pass through untouched (existing
refusal / drop-never-refuse / offer paths keep seeing the model's proposal).
Effect-object-owned slots are never touched. Plus capture-style tests through
the REAL ``_resolve_one`` proving the per-kind readers receive canonical
names."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation.governance_core import (
    EDGE_BELONGS, GovernanceCore, _canonicalize_subject_fields)
import primeqa.generation.governance_core as gc

SUBJ = "PLS_FB_Order__c"


def _field_row(bare: str, label: str):
    return SimpleNamespace(
        edge_type=EDGE_BELONGS,
        entity=SimpleNamespace(id=uuid4(), entity_type="Field",
                               sf_api_name=f"{SUBJ}.{bare}",
                               display_name=label))


NBHD = [
    _field_row("PLS_FB_Priority__c", "Priority"),
    _field_row("PLS_FB_Status__c", "Status"),
    _field_row("PLS_FB_External_Ref__c", "External Reference"),
    _field_row("PLS_FB_Order_Total__c", "Order Total"),
]


def test_scalar_slots_canonicalize():
    hint = {"field_name": f"{SUBJ}.Priority__c",
            "trigger_field": f"{SUBJ}.Status__c",
            "effect_via_lookup_field": "External_Reference__c"}
    out = _canonicalize_subject_fields(hint, NBHD)
    assert out["field_name"] == f"{SUBJ}.PLS_FB_Priority__c"
    assert out["trigger_field"] == f"{SUBJ}.PLS_FB_Status__c"
    assert out["effect_via_lookup_field"] == f"{SUBJ}.PLS_FB_External_Ref__c"
    assert hint["field_name"] == f"{SUBJ}.Priority__c"      # input not mutated


def test_pair_and_clause_slots_canonicalize():
    hint = {
        "trigger_fields": [{"field_name": "Status__c", "value": "Submitted"},
                           {"field_name": "Nope__c", "value": "x"}, "junk"],
        "update_trigger_fields": [{"field": "Priority__c", "value": "High"}],
        "rejection_conditions": [
            {"field": "External_Reference__c", "predicate": "is_null"},
            {"field": f"{SUBJ}.Order_Total__c", "predicate": "exceeds",
             "compared_to": "Priority__c"}],
        "acceptance_conditions": [{"field": "Status", "predicate": "equals",
                                   "value": "Draft"}],
        "update_conditions": [{"field_name": "priority__c", "predicate": "set"}],
    }
    out = _canonicalize_subject_fields(hint, NBHD)
    assert out["trigger_fields"][0]["field_name"] == f"{SUBJ}.PLS_FB_Status__c"
    assert out["trigger_fields"][0]["value"] == "Submitted"     # value kept
    assert out["trigger_fields"][1]["field_name"] == "Nope__c"  # miss untouched
    assert out["trigger_fields"][2] == "junk"                   # junk tolerated
    assert out["update_trigger_fields"][0]["field"] == f"{SUBJ}.PLS_FB_Priority__c"
    assert out["rejection_conditions"][0]["field"] == f"{SUBJ}.PLS_FB_External_Ref__c"
    assert out["rejection_conditions"][1]["field"] == f"{SUBJ}.PLS_FB_Order_Total__c"
    assert out["rejection_conditions"][1]["compared_to"] == f"{SUBJ}.PLS_FB_Priority__c"
    assert out["acceptance_conditions"][0]["field"] == f"{SUBJ}.PLS_FB_Status__c"
    assert out["update_conditions"][0]["field_name"] == f"{SUBJ}.PLS_FB_Priority__c"


def test_effect_object_slots_and_unresolvables_untouched():
    hint = {"effect_field": "Priority__c",            # effect-object-owned
            "effect_lookup_field": "Status__c",       # effect-object-owned
            "field_name": "Total_Value__c",           # unresolvable
            "automation_name": "FL09"}
    out = _canonicalize_subject_fields(hint, NBHD)
    assert out is hint          # nothing rewrote -> same identity


def test_ambiguity_never_rewrites():
    nbhd = NBHD + [_field_row("B_Priority__c", "Priority B")]
    out = _canonicalize_subject_fields({"field_name": "Priority__c"}, nbhd)
    assert out == {"field_name": "Priority__c"}


# -- through the REAL _resolve_one: readers receive canonical names -----------

def _ctx():
    from primeqa.generation.governance import ConversationContext
    from primeqa.generation.protocol import (
        BudgetSpec, GovernanceContext, OperationalContext, SemanticContext)
    return ConversationContext(
        request_id=uuid4(), requirement_ref={"key": "req-t", "text": "t"},
        requirement_text="t",
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "req-t", "text": "t"}],
            s1_version_seq=1, s1_version_name="v1"),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(budgets=BudgetSpec()))


def _vr_row():
    """A grounding VR (APPLIES_TO + active) so the prohibition branch
    proceeds past admissibility to the conditions consumer."""
    return SimpleNamespace(
        edge_type="APPLIES_TO",
        entity=SimpleNamespace(
            id=uuid4(), entity_type="ValidationRule",
            sf_api_name=f"{SUBJ}.VR01",
            display_name="VR01",
            attributes={"formula_text": "ISBLANK(PLS_FB_External_Ref__c)",
                        "error_message": "External reference is required.",
                        "is_active": True}))


def _drive(monkeypatch, hint, claim_kind, capture_fn_name):
    # minimal S1 stub: the prohibition branch reads field details/picklists
    # for its fixture machinery — empty answers keep it on the happy path
    s1_stub = SimpleNamespace(
        get_entity_details=lambda _id, at_seq: {},
        get_picklist_values=lambda _id, at_seq: [],
        get_entities=lambda *a, **k: [],
        get_related=lambda *a, **k: [])
    gov = GovernanceCore(s1_stub)
    subject = SimpleNamespace(id=uuid4(), entity_type="Object",
                              sf_api_name=SUBJ)
    monkeypatch.setattr(gov._admit, "resolve_subject",
                        lambda et, api, at: [subject])
    monkeypatch.setattr(gov._admit, "scoped_neighborhood",
                        lambda subj, at: list(NBHD) + [_vr_row()])
    captured = {}
    real = getattr(gc, capture_fn_name)

    def spy(proposed, neighborhood, *a, **k):
        captured["proposed"] = proposed
        return real(proposed, neighborhood, *a, **k)

    monkeypatch.setattr(gc, capture_fn_name, spy)
    state = SimpleNamespace(attempted_interpretation={"candidate_paths": []},
                            groundings=[])
    gov._resolve_one(
        {"intent_descriptor": {
            "archetype_hint": "data_behavior",
            "claim_kind_hint": claim_kind,
            "polarity_hint": "negative",
            "target_subject_hint": {"entity_type": "Object",
                                    "sf_api_name": SUBJ, **hint}},
         "requirement_excerpt": "Orders must be commercially valid."},
        _ctx(), state)
    return captured


def test_resolve_one_hands_canonical_rejection_conditions(monkeypatch):
    captured = _drive(
        monkeypatch,
        {"rejection_conditions": [
            {"field": "External_Reference__c", "predicate": "is_null"}]},
        "prohibition-claim", "_ground_rejection_conditions")
    assert captured["proposed"][0]["field"] == f"{SUBJ}.PLS_FB_External_Ref__c"
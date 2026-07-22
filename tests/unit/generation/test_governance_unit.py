"""Pure unit tests for governance-core internals (no PG): the S1 edge-type
drift-guard, the emittable-set drift-guard (D-105), dismissal phase-tagging
(D-077), and explanation_hash mechanics (D-075) — determinism, prose-freeness,
typed-field sensitivity."""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from primeqa.generation import governance_core as gc
from primeqa.generation.enums import AdmissibilityLayer
from primeqa.generation.emission import (
    EMITTABLE,
    BehaviourIncomplete,
    GroundedAutomationEffect,
    GroundedCapability,
    GroundedEmission,
    GroundedExistence,
    GroundedLayout,
    GroundedNegative,
    GroundedPositive,
    GroundedProperty,
    GroundedStateTransition,
    _Endpoint,
    author_emission,
)
from primeqa.generation.explanation_hash import canonicalize, compute_explanation_hash
from primeqa.semantic.edges import TIER_1_EDGES


# ---------------------------------------------------------------------------
# Edge-type drift-guard (D-096.1 — bind verbatim to TIER_1_EDGES)
# ---------------------------------------------------------------------------

def test_neighborhood_edges_are_tier1_keys():
    for et in gc.OBJECT_NEIGHBORHOOD_EDGES:
        assert et in TIER_1_EDGES, f"{et!r} is not a TIER_1_EDGES key (drift)"


def test_validation_rule_edge_shape():
    md = TIER_1_EDGES[gc.EDGE_VALIDATION_RULE]   # APPLIES_TO
    assert "ValidationRule" in md.source_entity_types
    assert "Object" in md.target_entity_types


def test_flow_and_grant_edge_shapes():
    assert "Flow" in TIER_1_EDGES[gc.EDGE_FLOW].source_entity_types
    assert "Object" in TIER_1_EDGES[gc.EDGE_FLOW].target_entity_types
    grant = TIER_1_EDGES[gc.EDGE_OBJECT_GRANT]
    assert "Object" in grant.target_entity_types


# ---------------------------------------------------------------------------
# Emittable-set drift-guard (D-105.1/.3) — EMITTABLE and author_emission lockstep
# ---------------------------------------------------------------------------
# This guard binds the source of truth to the authoring capability: EMITTABLE
# can't claim a kind author_emission can't handle (a constructable grounded shape
# per pair, and no extras). Since the gate admits only EMITTABLE pairs, a *gated*
# PROCEED therefore always reaches an authorable finalize. It does NOT prove the
# no-crash guarantee for an *ungated* resolution path (a gating gap) — that is the
# runtime backstop in finalize_outcome (D-105.4). Adding an emittable kind MUST
# update this map with a real shape author_emission handles.

def _ep(entity_type: str, external_id: str) -> _Endpoint:
    return _Endpoint(entity_id=uuid4(), entity_type=entity_type, external_id=external_id)


_EMITTABLE_SHAPES = {
    ("configuration", "metadata-relationship-claim"): lambda: GroundedEmission(
        archetype="configuration", claim_kind="metadata-relationship-claim",
        edge_type="APPLIES_TO", version_seq=1,
        source=_ep("ValidationRule", "Case.RequireReason"),
        target=_ep("Object", "Case"), requirement_excerpt="x"),
    ("configuration", "existence-claim"): lambda: GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=1,
        subject=_ep("Field", "Account.Industry"), requirement_excerpt="x"),
    ("configuration", "property-claim"): lambda: GroundedProperty(
        archetype="configuration", claim_kind="property-claim", version_seq=1,
        subject=_ep("Field", "Account.Industry"), property_name="is_required",
        expected_value=True, requirement_excerpt="x"),
    ("permission", "capability-claim"): lambda: GroundedCapability(
        archetype="permission", claim_kind="capability-claim", version_seq=1,
        granting_subject=_ep("Profile", "Admin"),
        target=_ep("Field", "Account.AnnualRevenue"),
        granted_capability="edit", grant_type="field", requirement_excerpt="x"),
    ("ui", "layout-claim"): lambda: GroundedLayout(
        archetype="ui", claim_kind="layout-claim", version_seq=1,
        layout=_ep("Layout", "Account-Account Layout"),
        field=_ep("Field", "Account.AnnualRevenue"), requirement_excerpt="x"),
    ("data_behavior", "acceptance-claim"): lambda: __import__(
        "primeqa.generation.emission", fromlist=["GroundedAcceptance"]
    ).GroundedAcceptance(
        archetype="data_behavior", claim_kind="acceptance-claim",
        version_seq=1, subject=_ep("Object", "Case"), requirement_excerpt="x",
        conditions=(SimpleNamespace(
            field=_ep("Field", "Case.Amount"), predicate="equals",
            value="100"),)),
    ("data_behavior", "prohibition-claim"): lambda: GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint=None, version_seq=1,
        # D-293: a prohibition authors only when a behavioural reject recipe is
        # derivable — give a numeric VR so this drift-guard exercises authoring.
        subject=_ep("Object", "Case"), requirement_excerpt="x",
        vr_formulas=("Amount < 0",)),
    ("data_behavior", "value-claim"): lambda: GroundedPositive(
        archetype="data_behavior", claim_kind="value-claim", version_seq=1,
        target_object=_ep("Object", "Invoice"), field=_ep("Field", "Invoice.Amount"),
        value="100", requirement_excerpt="x"),
    ("data_behavior", "state-transition-claim"): lambda: GroundedStateTransition(
        archetype="data_behavior", claim_kind="state-transition-claim",
        version_seq=1, subject=_ep("Object", "Case"),
        field=_ep("Field", "Case.Status"), to_value="In Escalation",
        requirement_excerpt="x"),
    ("data_behavior", "automation-effect-claim"): lambda: GroundedAutomationEffect(
        archetype="data_behavior", claim_kind="automation-effect-claim",
        version_seq=1, subject=_ep("Object", "Case"),
        automation=_ep("Flow", "Escalate_Case"), requirement_excerpt="x",
        effect_field=_ep("Field", "Case.Status"), effect_value="In Escalation"),
}


def test_emittable_set_matches_author_emission_shapes():
    # Lockstep: every EMITTABLE pair has a constructable grounded shape here,
    # and the map carries no pair absent from EMITTABLE. A new emittable kind
    # that forgets either side trips this.
    assert set(_EMITTABLE_SHAPES) == set(EMITTABLE)


def test_every_emittable_pair_is_authorable():
    # The PROCEED gate only lets EMITTABLE pairs through; each MUST author into a
    # bundle (no TypeError) whose (archetype, claim_kind) round-trips the pair —
    # so a gated PROCEED can never reach an unauthorable finalize.
    for pair, make in _EMITTABLE_SHAPES.items():
        bundle = author_emission(make())
        assert (bundle.archetype, bundle.claim_kind) == pair


# ---------------------------------------------------------------------------
# D-222 — staged state-transition authoring (the trigger pair)
# ---------------------------------------------------------------------------

def _staged_state_transition(**overrides):
    kwargs = dict(
        archetype="data_behavior", claim_kind="state-transition-claim",
        version_seq=1, subject=_ep("Object", "Opportunity"),
        field=_ep("Field", "Opportunity.ForecastCategory"),
        to_value="Closed", requirement_excerpt="x",
        trigger_field=_ep("Field", "Opportunity.StageName"),
        trigger_value="Closed Won")
    kwargs.update(overrides)
    return GroundedStateTransition(**kwargs)


def test_staged_state_transition_authors_paired_shape():
    # The trigger pair rides from_state AND the create step sets it — the
    # asserted to-state field stays org-produced (absent from the create).
    bundle = author_emission(_staged_state_transition())
    body = bundle.asserted_truth
    assert body.from_state.field_values["Opportunity.StageName"].value == "Closed Won"
    assert body.to_state.field_values["Opportunity.ForecastCategory"].value == "Closed"
    assert {f.external_id for f in body.subject_fields} == {
        "Opportunity.ForecastCategory", "Opportunity.StageName"}
    create = bundle.observation_realization.steps[0]
    assert create.field_values == {"Opportunity.StageName": "Closed Won"}
    assert "Opportunity.ForecastCategory" not in create.field_values


def test_unstaged_state_transition_unchanged():
    # No trigger pair -> the D-210.1 shape exactly: empty from_state,
    # padding-only create, to-state field as the only subject_field.
    bundle = author_emission(_staged_state_transition(
        trigger_field=None, trigger_value=None))
    body = bundle.asserted_truth
    assert body.from_state.field_values == {}
    assert [f.external_id for f in body.subject_fields] == [
        "Opportunity.ForecastCategory"]
    assert bundle.observation_realization.steps[0].field_values == {}


# ---------------------------------------------------------------------------
# D-227 — cross-object trigger state-transition + parent-stamp automation
# ---------------------------------------------------------------------------

def test_cross_object_trigger_authors_two_create_chain():
    # The transition is provoked by creating a RELATED record: subject create
    # (padding), trigger create with the verified lookup ref, subject read,
    # to-state assert — the D-205 N-create chain shape.
    bundle = author_emission(GroundedStateTransition(
        archetype="data_behavior", claim_kind="state-transition-claim",
        version_seq=1, subject=_ep("Object", "Case"),
        field=_ep("Field", "Case.Status"), to_value="Escalated",
        requirement_excerpt="escalating a case",
        trigger_object=_ep("Object", "Escalation__c"),
        trigger_lookup_field=_ep("Field", "Escalation__c.Case__c")))
    steps = bundle.observation_realization.steps
    assert [s.step_id for s in steps] == [
        "create-subject", "create-trigger", "read-subject", "assert-value"]
    assert steps[0].target_object.external_id == "Case"
    assert steps[1].target_object.external_id == "Escalation__c"
    assert steps[1].field_values == {"Escalation__c.Case__c": "$create-subject.id"}
    assert "WHERE Id = '$create-subject.id'" in steps[2].soql
    assert steps[3].predicate.value == "Escalated"
    # the causal mutation is the TRIGGER create
    assert bundle.causal_initiation.target.external_id == "Escalation__c"
    assert "Escalation__c" in bundle.asserted_truth.triggering_event.description


def test_cross_object_trigger_composes_with_staged_pair():
    # D-222's staged pair rides the SUBJECT create even in the cross-object shape.
    bundle = author_emission(GroundedStateTransition(
        archetype="data_behavior", claim_kind="state-transition-claim",
        version_seq=1, subject=_ep("Object", "Case"),
        field=_ep("Field", "Case.Status"), to_value="Escalated",
        requirement_excerpt="x",
        trigger_field=_ep("Field", "Case.Priority"), trigger_value="High",
        trigger_object=_ep("Object", "Escalation__c"),
        trigger_lookup_field=_ep("Field", "Escalation__c.Case__c")))
    steps = bundle.observation_realization.steps
    assert steps[0].field_values == {"Case.Priority": "High"}
    assert bundle.asserted_truth.from_state.field_values[
        "Case.Priority"].value == "High"


def test_parent_stamp_authors_parent_first_chain_with_not_null():
    # The Flow stamps a record the trigger points to; value-less -> not_null.
    bundle = author_emission(GroundedAutomationEffect(
        archetype="data_behavior", claim_kind="automation-effect-claim",
        version_seq=1, subject=_ep("Object", "Escalation__c"),
        automation=_ep("Flow", "SQ205_Escalation_Effects"),
        requirement_excerpt="stamp the account",
        effect_object=_ep("Object", "Account"),
        effect_via_lookup_field=_ep("Field", "Escalation__c.Account__c"),
        effect_field=_ep("Field", "Account.Last_Escalation_Date__c"),
        effect_value=None))
    steps = bundle.observation_realization.steps
    assert [s.step_id for s in steps] == [
        "create-parent", "create-record", "read-effect", "assert-effect"]
    assert steps[0].target_object.external_id == "Account"
    assert steps[1].field_values == {
        "Escalation__c.Account__c": "$create-parent.id"}
    assert "WHERE Id = '$create-parent.id'" in steps[2].soql
    assert steps[3].predicate.predicate == "not_null"
    assert steps[3].predicate.value is None
    assert bundle.asserted_truth.affected_fields[0].external_id == \
        "Account.Last_Escalation_Date__c"


def test_parent_stamp_with_value_asserts_equals():
    bundle = author_emission(GroundedAutomationEffect(
        archetype="data_behavior", claim_kind="automation-effect-claim",
        version_seq=1, subject=_ep("Object", "Escalation__c"),
        automation=_ep("Flow", "SQ205_Escalation_Effects"),
        requirement_excerpt="x",
        effect_object=_ep("Object", "Account"),
        effect_via_lookup_field=_ep("Field", "Escalation__c.Account__c"),
        effect_field=_ep("Field", "Account.Rating"),
        effect_value="Hot"))
    assertion = bundle.observation_realization.steps[3]
    assert assertion.predicate.predicate == "equals"
    assert assertion.predicate.value == "Hot"


# ---------------------------------------------------------------------------
# D-107 formula capture at grounding — _grounding_vr_formulas
# ---------------------------------------------------------------------------

def _rel(edge_type: str, entity_type: str, attributes: dict):
    """A minimal stand-in for a RelatedEntity (only the fields the capture reads)."""
    return SimpleNamespace(
        edge_type=edge_type,
        entity=SimpleNamespace(entity_type=entity_type, attributes=attributes))


def test_grounding_vr_formulas_captures_matched_vrs_only():
    # Same (edge_type, far_type) the Layer-1 dimension matches: APPLIES_TO ->
    # ValidationRule. A Field on a different edge is ignored; a matched VR with no
    # formula_text contributes nothing; order is neighborhood order.
    nb = [
        _rel("APPLIES_TO", "ValidationRule", {"formula_text": "Amount < 0"}),
        _rel("BELONGS_TO", "Field", {"formula_text": "ignored — wrong edge/type"}),
        _rel("APPLIES_TO", "ValidationRule", {}),                 # VR, no formula
        _rel("APPLIES_TO", "ValidationRule", {"formula_text": "ISBLANK(R__c)"}),
    ]
    assert gc._grounding_vr_formulas("prohibition-claim", nb) == ("Amount < 0", "ISBLANK(R__c)")


def test_grounding_vr_formulas_unknown_dim_is_empty():
    # A claim_kind without a negative Layer-1 dimension has nothing to capture.
    assert gc._grounding_vr_formulas("value-claim", [_rel("APPLIES_TO", "ValidationRule",
                                                          {"formula_text": "Amount < 0"})]) == ()


# D-297 (lever 5, S1 dormant): _grounding_vr_messages — {formula_text -> error
# message} for the matched VRs, keyed the same as _grounding_vr_formulas so emission
# (5.2) can look up the DERIVED source formula's message. Ambiguity-guarded.

def test_grounding_vr_messages_maps_formula_to_message():
    nb = [
        _rel("APPLIES_TO", "ValidationRule",
             {"formula_text": "Amount < 0", "error_message": "Amount must be positive"}),
        _rel("APPLIES_TO", "ValidationRule", {"formula_text": "ISBLANK(R__c)"}),  # no message
        _rel("BELONGS_TO", "Field", {"formula_text": "x", "error_message": "wrong edge"}),
    ]
    assert gc._grounding_vr_messages("prohibition-claim", nb) == {
        "Amount < 0": "Amount must be positive"}


def test_grounding_vr_messages_ambiguity_guard_drops_conflicting():
    # Same formula_text, DIFFERING messages -> dropped (never bind an arbitrary one);
    # same formula + same message -> kept.
    nb = [
        _rel("APPLIES_TO", "ValidationRule", {"formula_text": "F", "error_message": "m1"}),
        _rel("APPLIES_TO", "ValidationRule", {"formula_text": "F", "error_message": "m2"}),
        _rel("APPLIES_TO", "ValidationRule", {"formula_text": "G", "error_message": "gm"}),
        _rel("APPLIES_TO", "ValidationRule", {"formula_text": "G", "error_message": "gm"}),
    ]
    out = gc._grounding_vr_messages("prohibition-claim", nb)
    assert "F" not in out
    assert out["G"] == "gm"


def test_grounding_vr_messages_raw_tooling_shape():
    # D-203.1 two-shape tolerance: raw Tooling ErrorMessage + Metadata.errorConditionFormula.
    nb = [_rel("APPLIES_TO", "ValidationRule",
               {"Metadata": {"errorConditionFormula": "Amt > 100"}, "ErrorMessage": "too big"})]
    assert gc._grounding_vr_messages("prohibition-claim", nb) == {"Amt > 100": "too big"}


def test_grounding_vr_messages_unknown_dim_is_empty():
    assert gc._grounding_vr_messages("value-claim", [_rel(
        "APPLIES_TO", "ValidationRule", {"formula_text": "F", "error_message": "m"})]) == {}


# ---------------------------------------------------------------------------
# D-293 (Slice 2): _ground_rejection_conditions — grounds an LLM-proposed
# prohibition business-state against the scoped neighborhood (Option A).
# ---------------------------------------------------------------------------

def _field_rel(sf_api_name: str, entity_id=None):
    """A BELONGS_TO Field relation carrying what _ground_rejection_conditions reads."""
    return SimpleNamespace(
        edge_type="BELONGS_TO",
        entity=SimpleNamespace(entity_type="Field", sf_api_name=sf_api_name,
                               id=entity_id or uuid4(), attributes={}))


def test_ground_rejection_conditions_empty_is_dormant():
    assert gc._ground_rejection_conditions(None, [], 7) == ([], [])
    assert gc._ground_rejection_conditions([], [_field_rel("Opportunity.Loan_Amount__c")], 7) == ([], [])


def test_ground_rejection_conditions_grounds_field_in_neighborhood():
    nb = [_field_rel("Opportunity.Loan_Amount__c")]
    grounded, invalid = gc._ground_rejection_conditions(
        [{"field": "Opportunity.Loan_Amount__c", "predicate": "is_null"}], nb, 7)
    assert invalid == [] and len(grounded) == 1
    assert grounded[0].field.external_id == "Opportunity.Loan_Amount__c"
    assert grounded[0].predicate == "is_null" and grounded[0].value is None


def test_ground_rejection_conditions_unresolved_field_is_invalid():
    grounded, invalid = gc._ground_rejection_conditions(
        [{"field": "Opportunity.Nope__c", "predicate": "is_null"}],
        [_field_rel("Opportunity.Loan_Amount__c")], 7)
    assert grounded == [] and invalid and "Nope__c" in invalid[0]


def test_ground_rejection_conditions_predicate_value_coupling():
    nb = [_field_rel("Opportunity.Loan_Amount__c")]
    f = "Opportunity.Loan_Amount__c"
    _, inv_free_with_value = gc._ground_rejection_conditions(
        [{"field": f, "predicate": "is_null", "value": 5}], nb, 7)
    _, inv_bearing_no_value = gc._ground_rejection_conditions(
        [{"field": f, "predicate": "equals"}], nb, 7)
    _, inv_unknown = gc._ground_rejection_conditions(
        [{"field": f, "predicate": "greater_than", "value": 5}], nb, 7)
    assert inv_free_with_value and inv_bearing_no_value and inv_unknown
    g, inv = gc._ground_rejection_conditions(
        [{"field": f, "predicate": "equals", "value": "On Hold"}], nb, 7)
    assert inv == [] and len(g) == 1 and g[0].value == "On Hold"


def test_ground_rejection_conditions_matches_pattern_drops_value():
    """D-384: the org owns the format (the VR's REGEX) — ANY proposed
    matches_pattern value drops to None before grounding (never refuses),
    and a value-less proposal grounds identically. compared_to stays
    forbidden (the D-330 coupling is untouched)."""
    nb = [_field_rel("PLS_FB_Order__c.External_Reference__c")]
    f = "PLS_FB_Order__c.External_Reference__c"
    for v in ("INVALID", "<invalid-format>", "<invalid format>", None):
        g, inv = gc._ground_rejection_conditions(
            [{"field": f, "predicate": "matches_pattern", "value": v}], nb, 7)
        assert inv == [] and len(g) == 1
        assert g[0].predicate == "matches_pattern" and g[0].value is None
    _, inv = gc._ground_rejection_conditions(
        [{"field": f, "predicate": "matches_pattern", "compared_to": f}], nb, 7)
    assert inv and "compared_to" in inv[0]


# ---------------------------------------------------------------------------
# D-299: automation-effect entry-condition grounding + named-flow binding.
# ---------------------------------------------------------------------------

def _flow_rel(sf_api_name: str, entity_id=None):
    """A TRIGGERS_ON Flow relation off the Object subject."""
    return SimpleNamespace(
        edge_type=gc.EDGE_FLOW,
        entity=SimpleNamespace(entity_type="Flow", sf_api_name=sf_api_name,
                               id=entity_id or uuid4(), attributes={}))


def _auto_cand():
    return gc._Candidate(
        path_id="c0", archetype="data_behavior",
        claim_kind="automation-effect-claim",
        subject_refs=[{"entity_type": "Object", "sf_api_name": "Order__c"}],
        requirement_anchor="x", status="dismissed")


def test_ground_trigger_fields_empty_is_dormant():
    assert gc._ground_trigger_fields(None, []) == ()
    assert gc._ground_trigger_fields([], [_field_rel("Opportunity.StageName")]) == ()


def test_ground_trigger_fields_resolves_object_qualified_belongs_to():
    nb = [_field_rel("Opportunity.StageName"),
          _field_rel("Opportunity.KYC_Complete__c")]
    out = gc._ground_trigger_fields(
        [{"field_name": "Opportunity.StageName", "value": "Credit Assessment"},
         {"field_name": "Opportunity.KYC_Complete__c", "value": True}], nb)
    assert [(ep.external_id, v) for ep, v in out] == [
        ("Opportunity.StageName", "Credit Assessment"),
        ("Opportunity.KYC_Complete__c", True)]


def test_ground_trigger_fields_drops_unverifiable_and_valueless():
    # drop-never-refuse: an unknown field or a value-less pair is dropped, and
    # the verified pair still comes through.
    nb = [_field_rel("Opportunity.StageName")]
    out = gc._ground_trigger_fields(
        [{"field_name": "Opportunity.StageName", "value": "Credit Assessment"},
         {"field_name": "Opportunity.Ghost__c", "value": "X"},   # not in nb
         {"field_name": "Opportunity.StageName"}],                # no value
        nb)
    assert [(ep.external_id, v) for ep, v in out] == [
        ("Opportunity.StageName", "Credit Assessment")]


def test_ground_trigger_fields_excludes_the_effect_field_k16():
    # k16 truth-bearing guard: the effect field (the value-under-test the Flow
    # must PRODUCE) is DROPPED if proposed as a trigger — the create must never
    # plant it, else the assert passes without the Flow firing (silent
    # wrong-green). Enforced by the substrate, not the prompt.
    nb = [_field_rel("Opportunity.StageName"),
          _field_rel("Opportunity.Risk_Rating__c")]
    out = gc._ground_trigger_fields(
        [{"field_name": "Opportunity.StageName", "value": "Credit Assessment"},
         {"field_name": "Opportunity.Risk_Rating__c", "value": "Medium"}],  # the effect
        nb, exclude_field="Opportunity.Risk_Rating__c")
    assert [(ep.external_id, v) for ep, v in out] == [
        ("Opportunity.StageName", "Credit Assessment")]
    # excluding the ONLY trigger falls back to the empty tuple (shallow shape)
    assert gc._ground_trigger_fields(
        [{"field_name": "Opportunity.Risk_Rating__c", "value": "Medium"}],
        nb, exclude_field="Opportunity.Risk_Rating__c") == ()


def test_evaluate_positive_named_flow_grounds_only_on_match():
    # D-299: with >1 Flow TRIGGERS_ON the subject a requirement-NAMED flow must
    # bind THAT flow; a named-but-absent flow is a genuine grounding miss.
    admit = gc.AdmissibilityEngine(None)   # _evaluate_positive touches no S1
    nb = [_flow_rel("Stamp_Order_Status"), _flow_rel("Escalate_Order")]
    matched = admit._evaluate_positive(
        _auto_cand(), "automation-effect-claim", nb, None, "Escalate_Order")
    assert matched.status == "admissibly_grounded"
    absent = admit._evaluate_positive(
        _auto_cand(), "automation-effect-claim", nb, None, "Ghost_Flow")
    assert absent.status == "dismissed"
    assert absent.dismissal_reason == "insufficient_grounding"
    # no name -> the any-flow floor (backward-compat, pre-D-299)
    unnamed = admit._evaluate_positive(
        _auto_cand(), "automation-effect-claim", nb, None, None)
    assert unnamed.status == "admissibly_grounded"


# ---------------------------------------------------------------------------
# D-293 (Slice 3): the completeness gate — prohibition_recipe_derivable (the
# shared predicate governance refuses on) + the behaviour_incomplete router.
# ---------------------------------------------------------------------------

def test_prohibition_recipe_derivable_truth_table():
    from primeqa.generation.emission import prohibition_recipe_derivable as d
    # numeric comparison / ISBLANK derive a behavioural recipe -> complete
    assert d("modify_record", ("Amount < 0",)) is True
    assert d("modify_record", ("ISBLANK(Reason__c)",)) is True
    assert d(None, ("Amount < 0",)) is True               # default op = modify_record
    assert d("create_duplicate", ("Amount = 0",)) is True
    # the at-least-one rule: one derivable formula among several -> complete
    assert d("modify_record", ('REGEX(Name,"x")', "Amount < 0")) is True
    # non-numeric / field-to-field / empty -> NOT derivable -> the gate refuses
    assert d("modify_record", ()) is False
    assert d("modify_record", ("Amount < Other__c",)) is False       # metadata-free (D-293)
    assert d("modify_record", ('REGEX(Name,"x")',)) is False
    # delete / share / transfer never derive (VRs do not reject them)
    assert d("delete", ("Amount < 0",)) is False
    assert d("transfer_ownership", ("Amount < 0",)) is False
    # D-294: the SAME cross-field VR flips to derivable WITH numeric field metadata
    xf = {"Loan__c": {"field_type": "currency"}, "Property__c": {"field_type": "double"}}
    assert d("modify_record", ("Loan__c > Property__c",), xf) is True
    # ...but still refuses without metadata, or when an operand is non-numeric
    assert d("modify_record", ("Loan__c > Property__c",)) is False
    assert d("modify_record", ("Loan__c > Property__c",),
             {"Loan__c": {"field_type": "text"}, "Property__c": {"field_type": "double"}}) is False
    # D-294 Slice 3: NOT(ISBLANK) + bare-boolean flip to derivable with metadata
    assert d("modify_record", ("NOT(ISBLANK(Reason__c))",),
             {"Reason__c": {"field_type": "text"}}) is True
    assert d("modify_record", ("NOT(ISBLANK(Reason__c))",)) is False       # no metadata
    assert d("modify_record", ("KYC_Done__c",), {"KYC_Done__c": {"field_type": "boolean"}}) is True
    assert d("modify_record", ("KYC_Done__c",)) is False                   # bare, no metadata
    # D-294 Slice 4: NOT(ISPICKVAL) flips to derivable with a ≥2-value picklist
    assert d("modify_record", ('NOT(ISPICKVAL(Stage__c,"Closed"))',),
             {"Stage__c": {"field_type": "picklist", "picklist_values": ("Open", "Closed")}}) is True
    assert d("modify_record", ('NOT(ISPICKVAL(Stage__c,"Closed"))',)) is False   # no metadata


def test_behaviour_incomplete_router_kind():
    from primeqa.generation.enums import RefusalKind
    directive = gc.RefusalRouter().behaviour_incomplete("no derivable reject recipe")
    assert directive.refusal_kind is RefusalKind.BEHAVIOUR_INCOMPLETE
    assert "no derivable reject recipe" in directive.payload["detail"]


# ---------------------------------------------------------------------------
# D-294 (Slice 1): _grounding_field_metadata — the read-only field-metadata rail
# (bare-keyed type/picklist). DORMANT: derive ignores it this slice, so this only
# proves the rail POPULATES; no derivation outcome changes yet.
# ---------------------------------------------------------------------------

class _StubS1Meta:
    """Minimal SemanticOrgModel stub: field_details by entity id + picklist rows."""

    def __init__(self, details_by_id, picklist_by_pvs):
        self._d, self._p = details_by_id, picklist_by_pvs

    def get_entity_details(self, entity_id, at_seq=None):
        return self._d.get(entity_id)

    def get_picklist_values(self, pvs_id, at_seq=None):
        return self._p.get(pvs_id, [])


def test_grounding_field_metadata_populates_bare_keyed_type_and_picklist():
    loan_id, stage_id = uuid4(), uuid4()
    nb = [_field_rel("Opportunity.Loan_Amount__c", loan_id),
          _field_rel("Opportunity.Stage__c", stage_id),
          # a non-Field relation is ignored
          SimpleNamespace(edge_type="APPLIES_TO",
                          entity=SimpleNamespace(entity_type="ValidationRule",
                                                 sf_api_name="Opportunity.VR", id=uuid4()))]
    s1 = _StubS1Meta(
        details_by_id={
            loan_id: {"field_type": "Currency", "length": None, "is_calculated": False},
            stage_id: {"field_type": "Picklist", "picklist_value_set_entity_id": "pvs1"}},
        picklist_by_pvs={"pvs1": [
            {"value_api_name": "Prospecting", "is_active": True},
            {"value_api_name": "Closed", "is_active": True},
            {"value_api_name": "Old", "is_active": False}]})
    meta = gc._grounding_field_metadata(nb, s1, at_seq=7)
    assert set(meta) == {"Loan_Amount__c", "Stage__c"}      # bare-keyed (Object.Field -> Field)
    assert meta["Loan_Amount__c"]["field_type"] == "currency"   # lowercased
    assert meta["Loan_Amount__c"]["picklist_values"] is None    # non-picklist -> None
    assert meta["Loan_Amount__c"]["is_calculated"] is False
    assert meta["Stage__c"]["field_type"] == "picklist"
    assert meta["Stage__c"]["picklist_values"] == ("Prospecting", "Closed")  # ACTIVE only


def test_grounding_field_metadata_empty_when_no_fields():
    # no BELONGS_TO Field -> empty rail -> derivation refuses exactly as today.
    nb = [SimpleNamespace(edge_type="APPLIES_TO",
                          entity=SimpleNamespace(entity_type="ValidationRule",
                                                 sf_api_name="X", id=uuid4()))]
    assert gc._grounding_field_metadata(nb, _StubS1Meta({}, {}), at_seq=7) == {}


# ---------------------------------------------------------------------------
# D-107 / D-293: an authored negative is ALWAYS LAYER_2 (caveat dropped). The
# pre-D-293 Layer-1 caveated fallback is gone — a non-derivable negative refuses
# (emission guard raises) rather than emitting a caveated inspection.
# ---------------------------------------------------------------------------

def _neg(formulas: tuple[str, ...]) -> GroundedNegative:
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint=None, version_seq=1,
        subject=_ep("Object", "Case"), requirement_excerpt="x", vr_formulas=formulas)


def test_verified_negative_is_layer2_no_caveat():
    # A derivable grounding formula discharges Layer 2 -> verified marker, caveat
    # dropped.
    b = author_emission(_neg(("Amount < 0",)))
    assert b.admissibility_layer is AdmissibilityLayer.LAYER_2
    assert b.caveat_required is False and b.caveat_kind is None


def test_non_derivable_negative_refuses_no_caveated_fallback():
    # D-293 decision-2: no derivable formula (empty; field-to-field; or unparsed)
    # -> incomplete behaviour instance -> emission guard raises (the pre-D-293
    # Layer-1 caveated inspection fallback is removed). Governance refuses these
    # before stash; the guard makes the invariant explicit.
    for formulas in ((), ("Amount < Other__c",), ('REGEX(Name, "x")',)):
        with pytest.raises(BehaviourIncomplete):
            author_emission(_neg(formulas))


def test_every_authored_negative_is_layer2_no_caveat():
    # D-293: every prohibition that authors at all is verified (LAYER_2, no
    # caveat) — the marker/caveat invariant degenerates to "always dropped".
    # Includes a multi-VR object where one of two formulas derives (at-least-one).
    for formulas in (("Amount < 0",), ("ISBLANK(R__c)",), ("Amount < 0", 'Name < "M"')):
        b = author_emission(_neg(formulas))
        assert b.admissibility_layer is AdmissibilityLayer.LAYER_2
        assert b.caveat_required is False
        assert b.caveat_kind is None


# ---------------------------------------------------------------------------
# Dismissal phase-tagging (D-077d)
# ---------------------------------------------------------------------------

def test_phase_for_reason():
    assert gc.phase_for_reason("no_constraint_supports_negative") == "grounding"
    assert gc.phase_for_reason("insufficient_grounding") == "grounding"
    assert gc.phase_for_reason("ambiguous_target_resolution") == "interpretation"
    assert gc.phase_for_reason("lower_specificity") == "interpretation"
    assert gc.phase_for_reason("policy_threshold_not_met") == "governance"


# ---------------------------------------------------------------------------
# explanation_hash (D-075) — mechanical, prose-free
# ---------------------------------------------------------------------------

def _ai(anchor="some excerpt prose", claim_kind="prohibition-claim"):
    return {
        "candidate_paths": [{
            "path_id": "c0", "archetype": "data_behavior", "claim_kind": claim_kind,
            "subject_refs": [{"entity_type": "Object", "sf_api_name": "Account"}],
            "requirement_anchor": anchor,
            "admissibility_status": "dismissed", "admissibility_layer": None,
        }],
        "dismissed_alternatives_by_reason": {"no_constraint_supports_negative": ["c0"]},
        "selected_path_id": None,
        "scoped_neighborhood": [],
    }


def test_hash_deterministic():
    assert compute_explanation_hash(_ai()) == compute_explanation_hash(_ai())
    assert len(compute_explanation_hash(_ai())) == 64


def test_hash_is_prose_free():
    # Changing the free-form requirement_anchor must NOT change the hash.
    assert compute_explanation_hash(_ai(anchor="prose A")) == compute_explanation_hash(_ai(anchor="prose B"))
    # And the canonical form carries no requirement_anchor at all.
    assert "requirement_anchor" not in json.dumps(canonicalize(_ai()))


def test_hash_sensitive_to_typed_substance():
    # Changing a typed field (claim_kind) MUST change the hash.
    assert compute_explanation_hash(_ai(claim_kind="prohibition-claim")) != \
           compute_explanation_hash(_ai(claim_kind="value-claim"))


# ---------------------------------------------------------------------------
# D-301: inactive VRs never ground — the formula/message sets filter them.
# ---------------------------------------------------------------------------

def _vr_rel(formula, *, active=None, message=None):
    attrs = {"formula_text": formula}
    if active is not None:
        attrs["is_active"] = active
    if message is not None:
        attrs["error_message"] = message
    return SimpleNamespace(
        edge_type="APPLIES_TO",
        entity=SimpleNamespace(entity_type="ValidationRule",
                               sf_api_name=f"VR_{id(attrs)}", id=uuid4(),
                               attributes=attrs))


def test_grounding_formulas_filter_inactive_vrs():
    # The live D-300 finding: an INACTIVE VR sharing a field with the live one
    # tied the D-295 alignment and vetoed grounding. Filtered, the live rule
    # stands alone.
    nb = [_vr_rel("Amount > 10000", active=True),
          _vr_rel("AND(ISPICKVAL(StageName,'Closed Won'), Amount <= 0)",
                  active=False)]
    formulas = gc._grounding_vr_formulas("prohibition-claim", nb)
    assert formulas == ("Amount > 10000",)


def test_grounding_formulas_missing_active_defaults_included():
    # seeds/pre-cutover rows carry no active flag -> assumed live (never
    # silently demote to caveated)
    nb = [_vr_rel("Amount > 10000")]
    assert gc._grounding_vr_formulas("prohibition-claim", nb) == ("Amount > 10000",)


def test_grounding_messages_filter_inactive_vrs():
    nb = [_vr_rel("Amount > 10000", active=True, message="Cap exceeded"),
          _vr_rel("Amount <= 0", active=False, message="Dead rule message")]
    msgs = gc._grounding_vr_messages("prohibition-claim", nb)
    assert msgs == {"Amount > 10000": "Cap exceeded"}


def test_identity_safe_floats_coerce_to_shortest_repr_strings():
    # D-304: canonicalization v1 forbids floats in identity-bearing content —
    # decimal hints coerce to strings at the hint->claim boundary (the S4
    # typed-tolerant equals grades "0.63" == 0.63 identically).
    assert gc._identity_safe(0.63) == "0.63"
    assert gc._identity_safe(0.9) == "0.9"
    assert gc._identity_safe(5.0) == "5.0"
    # non-floats pass through verbatim (D-115 §2)
    assert gc._identity_safe(720) == 720
    assert gc._identity_safe("Medium") == "Medium"
    assert gc._identity_safe(True) is True
    assert gc._identity_safe(None) is None


# ---------------------------------------------------------------------------
# D-318: _flows_producing_effect — bind by the effect a Flow verifiably produces.
# ---------------------------------------------------------------------------

def _flow_ent(*, updates=None, creates=None):
    md = {}
    if updates is not None:
        md["recordUpdates"] = [
            {"inputAssignments": [{"field": f, "value": v}]} for (f, v) in updates]
    if creates is not None:
        md["recordCreates"] = [{"object": o} for o in creates]
    return SimpleNamespace(attributes={"Metadata": md})


def test_flows_producing_effect_same_record_string():
    f = _flow_ent(updates=[("Risk_Rating__c", {"stringValue": "High"})])
    assert gc._flows_producing_effect([f], "Loan__c.Risk_Rating__c", "High", None) == [f]
    # a value the Flow does not stamp does not match (honest miss, never fake-green)
    assert gc._flows_producing_effect([f], "Loan__c.Risk_Rating__c", "Low", None) == []


def test_flows_producing_effect_numeric_shape_tolerant():
    # The Flow's Tooling JSON numberValue deserializes as float 750000.0; the LLM
    # commonly emits the effect value as an int or a string. All three must bind
    # (typed-tolerant, mirrors D-211) — the pre-fix exact-compare silently missed.
    f = _flow_ent(updates=[("Credit_Limit__c", {"numberValue": 750000.0})])
    for claim_val in (750000, "750000", 750000.0, "750000.0"):
        assert gc._flows_producing_effect([f], "Loan__c.Credit_Limit__c",
                                          claim_val, None) == [f], claim_val
    # a genuinely different number still misses
    assert gc._flows_producing_effect([f], "Loan__c.Credit_Limit__c", 750001, None) == []


def test_flows_producing_effect_bool_guard_no_leak():
    # a checkbox-stamping Flow: True must not equal the number 1 (Python leak guard)
    f = _flow_ent(updates=[("Is_Approved__c", {"booleanValue": True})])
    assert gc._flows_producing_effect([f], "x.Is_Approved__c", "true", None) == [f]
    assert gc._flows_producing_effect([f], "x.Is_Approved__c", True, None) == [f]
    assert gc._flows_producing_effect([f], "x.Is_Approved__c", 1, None) == []


def test_flows_producing_effect_cross_object_and_none_hint_floor():
    f = _flow_ent(creates=["Loan_Task__c"])
    assert gc._flows_producing_effect([f], None, None, "Loan_Task__c") == [f]
    # no field + no effect_object -> produces nothing (no spurious same-record match
    # on a None-valued reference assignment)
    ref = _flow_ent(updates=[("Owner__c", {"elementReference": "SomeVar"})])
    assert gc._flows_producing_effect([ref], "x.Owner__c", None, None) == []
    # Metadata-less entity produces nothing (name-trust / flows[0] fallback intact)
    assert gc._flows_producing_effect(
        [SimpleNamespace(attributes=None)], "x.Risk_Rating__c", "High", None) == []


# B1 arc: the Flow Behaviour IR joins the binder — a before-save guarded
# literal `$Record` assignment (FL01's mechanism) is a verifiable producer.

def _before_save_assignment_flow(*, guarded=True, literal=True):
    md = {
        "start": {"object": "PLS_FB_Order__c",
                  "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": ("Check" if guarded
                                                    else "Set")}},
        "assignments": [
            {"name": "Set",
             "assignmentItems": [{
                 "assignToReference": "$Record.PLS_FB_Priority__c",
                 "operator": "Assign",
                 "value": ({"stringValue": "Standard"} if literal
                           else {"elementReference": "fVar"})}]},
        ],
    }
    if guarded:
        md["decisions"] = [
            {"name": "Check",
             "rules": [{
                 "name": "Is_Blank", "conditionLogic": "and",
                 "conditions": [{
                     "leftValueReference": "$Record.PLS_FB_Priority__c",
                     "operator": "IsNull",
                     "rightValue": {"booleanValue": True}}],
                 "connector": {"targetReference": "Set"}}]},
        ]
    return SimpleNamespace(attributes={"Metadata": md})


def test_flows_producing_effect_before_save_guarded_literal_binds():
    f = _before_save_assignment_flow()
    assert gc._flows_producing_effect(
        [f], "PLS_FB_Order__c.PLS_FB_Priority__c", "Standard", None) == [f]
    # bare field hint binds too (the resolver bare-ifies)
    assert gc._flows_producing_effect(
        [f], "PLS_FB_Priority__c", "Standard", None) == [f]
    # wrong value never binds
    assert gc._flows_producing_effect(
        [f], "PLS_FB_Priority__c", "High", None) == []


def test_flows_producing_effect_formula_valued_assignment_never_binds():
    # FL02/FL08 shape: elementReference value — unsupported in the IR, so
    # the flow is NOT a verifiable producer (honest refusal preserved).
    f = _before_save_assignment_flow(literal=False)
    assert gc._flows_producing_effect(
        [f], "PLS_FB_Priority__c", "Standard", None) == []


# B1 arc: deterministic field-name resolution — the substrate supplies the
# org's naming mechanics; 0-or-ambiguous never guesses.

def _fb_field_rel(api, label):
    return SimpleNamespace(
        edge_type=gc.EDGE_BELONGS,
        entity=SimpleNamespace(entity_type="Field", sf_api_name=api,
                               display_name=label, attributes={}))


_NBHD = [
    _fb_field_rel("PLS_FB_Order__c.PLS_FB_Priority__c", "Priority"),
    _fb_field_rel("PLS_FB_Order__c.PLS_FB_Status__c", "Status"),
    _fb_field_rel("PLS_FB_Order__c.PLS_FB_Amount__c", "Amount"),
]


def test_resolve_field_exact_qualified_name_is_identity():
    assert gc._resolve_subject_field_name(
        _NBHD, "PLS_FB_Order__c.PLS_FB_Priority__c") \
        == "PLS_FB_Order__c.PLS_FB_Priority__c"


def test_resolve_field_bare_and_suffix_and_label_rules():
    # bare (case-insensitive)
    assert gc._resolve_subject_field_name(_NBHD, "pls_fb_priority__c") \
        == "PLS_FB_Order__c.PLS_FB_Priority__c"
    # suffix: the req-320 miss — Priority__c → PLS_FB_Priority__c
    assert gc._resolve_subject_field_name(_NBHD, "Priority__c") \
        == "PLS_FB_Order__c.PLS_FB_Priority__c"
    # qualified-but-wrong-bare resolves by its bare tail
    assert gc._resolve_subject_field_name(
        _NBHD, "PLS_FB_Order__c.Priority__c") \
        == "PLS_FB_Order__c.PLS_FB_Priority__c"
    # label
    assert gc._resolve_subject_field_name(_NBHD, "Priority") \
        == "PLS_FB_Order__c.PLS_FB_Priority__c"


def test_resolve_field_ambiguous_or_absent_returns_none():
    nbhd = _NBHD + [_fb_field_rel("PLS_FB_Order__c.Other_Priority__c",
                               "Other Priority")]
    # two ..._priority__c suffix matches -> ambiguous -> None (never guess)
    assert gc._resolve_subject_field_name(nbhd, "Priority__c") is None
    assert gc._resolve_subject_field_name(_NBHD, "Nonexistent__c") is None
    assert gc._resolve_subject_field_name(_NBHD, None) is None
    assert gc._resolve_subject_field_name(_NBHD, "") is None


def test_subject_field_inventory_line_is_compact_and_bounded():
    line = gc._subject_field_inventory_line(_NBHD, limit=2)
    assert line.startswith("subject fields include: ")
    assert "(+1 more)" in line


# ---------------------------------------------------------------------------
# D-320: _active_approvals / _names_a_subject_approval — approval bind-by-effect.
# ---------------------------------------------------------------------------

def _appr_rel(name, active, *, edge_type="TRIGGERS_ON", etype="ApprovalProcess"):
    return SimpleNamespace(edge_type=edge_type, entity=SimpleNamespace(
        entity_type=etype, sf_api_name=name, attributes={"_is_active": active}))


def test_active_approvals_filters_inactive_flow_and_wrong_edge():
    nb = [_appr_rel("A", True), _appr_rel("B", False),            # inactive dropped
          _appr_rel("Flow1", True, etype="Flow"),                  # not an approval
          _appr_rel("C", True, edge_type="BELONGS_TO")]            # wrong rail
    assert [a.sf_api_name for a in gc._active_approvals(nb)] == ["A"]


def test_names_a_subject_approval_active_or_inactive_but_not_unknown():
    nb = [_appr_rel("A", True), _appr_rel("B", False)]
    assert gc._names_a_subject_approval(nb, "A") is True          # active named
    assert gc._names_a_subject_approval(nb, "B") is True          # inactive still "named"
    assert gc._names_a_subject_approval(nb, "<UNKNOWN>") is False  # invented → enumerate
    assert gc._names_a_subject_approval(nb, None) is False
    # a Flow of the same name is NOT a subject approval
    assert gc._names_a_subject_approval(
        [_appr_rel("A", True, etype="Flow")], "A") is False


# --- D-330: the cross-field rejection clause (exceeds + compared_to) ---------

def test_ground_rejection_conditions_cross_field_grounds_both_fields():
    nb = [_field_rel("Opportunity.Loan_Amount__c"),
          _field_rel("Opportunity.Property_Value__c")]
    grounded, invalid = gc._ground_rejection_conditions(
        [{"field": "Opportunity.Loan_Amount__c", "predicate": "exceeds",
          "compared_to": "Opportunity.Property_Value__c"}], nb, 7)
    assert invalid == [] and len(grounded) == 1
    assert grounded[0].predicate == "exceeds" and grounded[0].value is None
    assert grounded[0].compared_to.external_id == "Opportunity.Property_Value__c"


def test_ground_rejection_conditions_cross_field_unresolved_other_is_invalid():
    nb = [_field_rel("Opportunity.Loan_Amount__c")]
    grounded, invalid = gc._ground_rejection_conditions(
        [{"field": "Opportunity.Loan_Amount__c", "predicate": "exceeds",
          "compared_to": "Opportunity.Nope__c"}], nb, 7)
    assert grounded == [] and invalid and "Nope__c" in invalid[0]


def test_ground_rejection_conditions_cross_field_value_is_invalid():
    nb = [_field_rel("Opportunity.Loan_Amount__c"),
          _field_rel("Opportunity.Property_Value__c")]
    grounded, invalid = gc._ground_rejection_conditions(
        [{"field": "Opportunity.Loan_Amount__c", "predicate": "exceeds",
          "compared_to": "Opportunity.Property_Value__c", "value": 5}], nb, 7)
    assert grounded == [] and invalid and "forbids a value" in invalid[0]


def test_ground_rejection_conditions_compared_to_on_v1_predicate_is_invalid():
    nb = [_field_rel("Opportunity.Loan_Amount__c"),
          _field_rel("Opportunity.Property_Value__c")]
    grounded, invalid = gc._ground_rejection_conditions(
        [{"field": "Opportunity.Loan_Amount__c", "predicate": "equals",
          "value": 5, "compared_to": "Opportunity.Property_Value__c"}], nb, 7)
    assert grounded == [] and invalid and "forbids compared_to" in invalid[0]


# --- D-332: picklist-value binding on staged clauses --------------------------

def _pl_cond(field, value):
    from primeqa.generation.emission import _Endpoint, _GroundedCondition
    return _GroundedCondition(
        field=_Endpoint(entity_id=uuid4(), entity_type="Field",
                        external_id=field),
        predicate="equals", value=value)


_PL_META = {"Loan_Type__c": {"field_type": "picklist",
                             "picklist_values": ["Home", "Personal", "Business"]}}


def test_bind_picklist_word_prefix_substitutes_org_value():
    # The req-302 live finding: the requirement's label "Home Loan" binds to
    # the org's actual value "Home" (unique word-prefix).
    bound, invalid = gc._bind_picklist_values(
        [_pl_cond("Opportunity.Loan_Type__c", "Home Loan")], _PL_META)
    assert invalid == [] and bound[0].value == "Home"


def test_bind_picklist_exact_and_case_insensitive():
    bound, invalid = gc._bind_picklist_values(
        [_pl_cond("Opportunity.Loan_Type__c", "Personal")], _PL_META)
    assert invalid == [] and bound[0].value == "Personal"
    bound, invalid = gc._bind_picklist_values(
        [_pl_cond("Opportunity.Loan_Type__c", "personal")], _PL_META)
    assert invalid == [] and bound[0].value == "Personal"


def test_bind_picklist_no_unique_match_is_invalid_with_valid_values():
    bound, invalid = gc._bind_picklist_values(
        [_pl_cond("Opportunity.Loan_Type__c", "Mortgage")], _PL_META)
    assert bound == [] and invalid
    assert "Mortgage" in invalid[0] and "Home" in invalid[0]


def test_bind_picklist_non_picklist_and_non_equals_pass_through():
    from primeqa.generation.emission import _Endpoint, _GroundedCondition
    plain = _pl_cond("Opportunity.Name__c", "anything")   # no rail entry
    isnull = _GroundedCondition(
        field=_Endpoint(entity_id=uuid4(), entity_type="Field",
                        external_id="Opportunity.Loan_Amount__c"),
        predicate="is_null", value=None)
    bound, invalid = gc._bind_picklist_values([plain, isnull], _PL_META)
    assert invalid == [] and bound[0].value == "anything"
    assert bound[1].predicate == "is_null"

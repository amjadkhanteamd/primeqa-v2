"""Unit: the Stage-1 readable-body skeleton (pure, deterministic, no LLM/DB).

Proves per claim-kind: byte-stable output (two builds identical; a fact change
moves the hash, an incidental change does not), correct section population vs
OMISSION, the never-fabricate guarantee for unregistered kinds, and that human
labels — never API names — reach the reader.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from primeqa.intelligence.readable_body import (
    ReadableBodySkeleton,
    build_readable_body,
)

# A label map covering every api name used below (business labels, SF-Label source).
LABELS = {
    "Loan__c": "Loan",
    "Loan__c.Credit_Score__c": "Credit Score",
    "Loan__c.Risk_Rating__c": "Risk Rating",
    "Loan__c.Applicant_Type__c": "Applicant Type",
    "Loan__c.Stage__c": "Stage",
    "Case_SLA__c": "Case SLA",
    "Case_SLA__c.SLA_Code__c": "SLA Code",
    "Contact.Email": "Email",
    "Opportunity": "Opportunity",
    "Opportunity.StageName": "Stage",
    "Home_Loan__c": "Home Loan",
}


def _ref(api):
    return {"ref_kind": "pinned", "entity_type": "Field", "entity_id": "e",
            "version_seq": 1, "external_id": api}


def _lit(v):
    return {"kind": "literal", "value": v}


def _conditions(*clauses):
    return {"kind": "conditions", "body_schema_version": 1,
            "conditions": list(clauses)}


def _data_recipe(recipe_id, steps):
    return {"recipe_id": recipe_id, "recipe_kind": "data-recipe",
            "trigger_kind": "data-mutation-trigger",
            "observation_realization": {"kind": "data-recipe", "steps": steps},
            "causal_initiation": {}, "execution_environment": {}}


def _build(claim_kind, asserted, *, conditions=None, recipes=None,
           strategy_kind=None, data_recipe_ids=None, labels=LABELS,
           archetype="data_behavior"):
    return build_readable_body(
        claim_kind=claim_kind, archetype=archetype, asserted_truth=asserted,
        semantic_conditions=conditions, recipes=recipes or [],
        strategy_kind=strategy_kind, data_recipe_ids=data_recipe_ids or [],
        labels=labels)


def _all_strings(skel: ReadableBodySkeleton):
    """Every rendered string in the skeleton (for the no-API-name assertion)."""
    d = asdict(skel)
    d.pop("grounded_tokens", None)   # a set — not JSON-serializable
    return json.dumps(d, default=list)


# ---------------------------------------------------------------------------
# Byte-stability (determinism + sensitivity)
# ---------------------------------------------------------------------------

def test_build_is_deterministic():
    asserted = {"kind": "value-claim", "subject": _ref("Contact.Email"),
                "expected_value": _lit("x@y.com")}
    a = _build("value-claim", asserted)
    b = _build("value-claim", asserted)
    assert a == b                                  # frozen dataclass equality
    assert a.skeleton_content_hash == b.skeleton_content_hash
    assert len(a.skeleton_content_hash) == 64      # sha256 hex


def test_hash_moves_on_a_fact_change():
    base = {"kind": "value-claim", "subject": _ref("Contact.Email"),
            "expected_value": _lit("x@y.com")}
    changed = {"kind": "value-claim", "subject": _ref("Contact.Email"),
               "expected_value": _lit("z@z.com")}
    assert _build("value-claim", base).skeleton_content_hash != \
        _build("value-claim", changed).skeleton_content_hash


def test_hash_stable_across_an_incidental_label_map_extra_key():
    asserted = {"kind": "value-claim", "subject": _ref("Contact.Email"),
                "expected_value": _lit("x@y.com")}
    extra = dict(LABELS, Unused__c="Unused")
    assert _build("value-claim", asserted).skeleton_content_hash == \
        _build("value-claim", asserted, labels=extra).skeleton_content_hash


def test_pinned_hash_regression():
    """A pinned hash for a fixed full-body fixture. This is a phrasing-CACHE key:
    any change to a rendered string silently invalidates every cached phrasing,
    so a hash move must be a CONSCIOUS, reviewed event (bump SKELETON_SHAPE_VERSION
    and update this constant) — not a silent side effect of an edit."""
    asserted = {"kind": "value-claim", "subject": _ref("Loan__c.Credit_Score__c"),
                "expected_value": _lit(720)}
    conds = _conditions({"subject": _ref("Loan__c.Applicant_Type__c"),
                         "predicate": "equals", "value": "Individual"})
    recipe = _data_recipe("r1", [
        {"kind": "create", "step_id": "c", "target_object": _ref("Loan__c"),
         "field_values": {"Loan__c.Credit_Score__c": 720}},
        {"kind": "read", "step_id": "rd", "target": _ref("Loan__c")},
        {"kind": "assert", "step_id": "a",
         "predicate": {"subject_ref": "rd.Credit_Score__c", "predicate": "equals",
                       "value": 720}}])
    skel = _build("value-claim", asserted, conditions=conds, recipes=[recipe])
    assert skel.skeleton_content_hash == \
        "ccc8bb52f5ad7d9cc34e69c0e53e49eb54c4002fe4ea897053681e07952fb171"


# ---------------------------------------------------------------------------
# Configuration kinds: light one-liner only
# ---------------------------------------------------------------------------

def test_existence_claim_headline_and_gloss_only():
    skel = _build("existence-claim", {"kind": "existence-claim",
                                      "subject": _ref("Case_SLA__c")})
    assert skel.registered is True
    assert skel.headline == "Case SLA exists in the org"
    assert skel.checks is not None and "configuration check" in skel.checks
    assert skel.preconditions == ()
    assert skel.test_data == ()
    assert skel.steps == ()
    assert skel.expected_result is None
    assert skel.probes == ()


def test_property_claim_headline_and_gloss_only():
    skel = _build("property-claim", {
        "kind": "property-claim", "subject": _ref("Case_SLA__c.SLA_Code__c"),
        "property_name": "length", "expected_value": _lit(18)})
    assert skel.headline == "SLA Code is 18 characters"
    assert skel.checks is not None and "configuration check" in skel.checks
    assert skel.test_data == () and skel.steps == () and skel.expected_result is None


# ---------------------------------------------------------------------------
# Behavioral kinds: full body
# ---------------------------------------------------------------------------

def test_value_claim_full_body():
    asserted = {"kind": "value-claim", "subject": _ref("Loan__c.Credit_Score__c"),
                "expected_value": _lit(720)}
    conds = _conditions({"subject": _ref("Loan__c.Applicant_Type__c"),
                         "predicate": "equals", "value": "Individual"})
    recipe = _data_recipe("r1", [
        {"kind": "create", "step_id": "c", "target_object": _ref("Loan__c"),
         "field_values": {"Loan__c.Credit_Score__c": 720}},
        {"kind": "read", "step_id": "rd", "target": _ref("Loan__c")},
        {"kind": "assert", "step_id": "a",
         "predicate": {"subject_ref": "rd.Credit_Score__c", "predicate": "equals",
                       "value": 720}}])
    skel = _build("value-claim", asserted, conditions=conds, recipes=[recipe])
    assert skel.depth == "behavioral"
    assert skel.preconditions == ("Applicant Type is Individual",)
    assert ("Credit Score", "720") in skel.test_data
    assert skel.steps[0].narration == "Create a Loan"
    assert skel.steps[-1].narration.startswith("Confirm the value matches")
    assert skel.expected_result == "Credit Score saves as 720"


def test_prohibition_claim_expected_rejection():
    asserted = {"kind": "prohibition-claim", "target": _ref("Opportunity"),
                "operation": "modify_field", "prohibition_mechanism": "validation_rule",
                "expected_rejection": {"error_code": "FIELD_CUSTOM_VALIDATION_EXCEPTION"}}
    recipe = _data_recipe("r1", [
        {"kind": "create", "step_id": "c", "target_object": _ref("Opportunity"),
         "field_values": {"Opportunity.StageName": "Closed Won"},
         "expect_rejection": {"error_code": "FIELD_CUSTOM_VALIDATION_EXCEPTION"}}])
    skel = _build("prohibition-claim", asserted, recipes=[recipe])
    assert skel.headline == "Rejects editing fields on Opportunity"
    assert "rejects the operation" in skel.expected_result
    assert "FIELD_CUSTOM_VALIDATION_EXCEPTION" in skel.expected_result
    assert skel.steps[0].narration == \
        "Attempt to create a Opportunity that the org should reject"


def test_acceptance_update_uses_update_state():
    asserted = {"kind": "acceptance-claim", "operation": "update",
                "target": _ref("Loan__c"),
                "update_state": {"field_values": {"Loan__c.Stage__c": _lit("Approved")}}}
    skel = _build("acceptance-claim", asserted)
    assert skel.expected_result == 'The org accepts setting Stage to "Approved"'


def test_state_transition_to_state():
    asserted = {"kind": "state-transition-claim", "subject": _ref("Opportunity"),
                "to_state": {"field_values": {"Opportunity.StageName": _lit("Closed Won")}}}
    skel = _build("state-transition-claim", asserted)
    assert skel.expected_result == 'Stage becomes "Closed Won"'


def test_automation_effect_field_change():
    asserted = {"kind": "automation-effect-claim",
                "automation": _ref("HL_Auto_Risk_Rating"),
                "automation_primitive": "flow",
                "triggering_action": {"trigger_kind": "data-mutation-trigger",
                                      "description": "creating a Loan"},
                "expected_effect": {"kind": "field_change", "changes": {
                    "field_values": {"Loan__c.Risk_Rating__c": _lit("High")}}},
                "affected_fields": [_ref("Loan__c.Risk_Rating__c")]}
    recipe = _data_recipe("r1", [
        {"kind": "create", "step_id": "c", "target_object": _ref("Loan__c"),
         "field_values": {"Loan__c.Credit_Score__c": 649}},
        {"kind": "read", "step_id": "rd", "target": _ref("Loan__c")}])
    skel = _build("automation-effect-claim", asserted, recipes=[recipe])
    assert skel.expected_result == 'Loan Risk Rating becomes "High"'
    assert ("Credit Score", "649") in skel.test_data
    # The flow api-name never reaches the spine.
    assert "HL_Auto_Risk_Rating" not in _all_strings(skel)


def test_automation_absence_v2():
    asserted = {"kind": "automation-effect-claim", "body_schema_version": 2,
                "automation": _ref("HL_Auto_Risk_Rating"),
                "automation_primitive": "flow",
                "triggering_action": {"trigger_kind": "data-mutation-trigger",
                                      "description": "creating a Loan"},
                "expected_absence": True}
    skel = _build("automation-effect-claim", asserted)
    assert skel.expected_result == "No automation record is produced."
    assert skel.checks is not None and "stays silent" in skel.checks


# ---------------------------------------------------------------------------
# Section-omission rules
# ---------------------------------------------------------------------------

def test_no_data_recipe_omits_steps_and_test_data():
    asserted = {"kind": "value-claim", "subject": _ref("Contact.Email"),
                "expected_value": _lit("x@y.com")}
    skel = _build("value-claim", asserted, recipes=[])   # no recipe
    assert skel.steps == ()
    assert skel.test_data == ()
    assert skel.expected_result == 'Email saves as "x@y.com"'   # claim-derived, stays
    assert skel.depth == "configuration-check"


def test_metadata_recipe_only_omits_steps():
    asserted = {"kind": "value-claim", "subject": _ref("Contact.Email"),
                "expected_value": _lit("x@y.com")}
    meta = {"recipe_id": "m1", "recipe_kind": "metadata-recipe",
            "observation_realization": {"kind": "metadata-recipe"}}
    skel = _build("value-claim", asserted, recipes=[meta])
    assert skel.steps == () and skel.test_data == ()


def test_empty_conditions_omits_preconditions():
    asserted = {"kind": "value-claim", "subject": _ref("Contact.Email"),
                "expected_value": _lit("x@y.com")}
    skel = _build("value-claim", asserted, conditions=_conditions())
    assert skel.preconditions == ()


def test_unregistered_kind_is_envelope_only_never_fabricates_steps():
    # capability-claim is a real kind but has no v1 readable body → envelope-only.
    asserted = {"kind": "capability-claim",
                "granting_subject": _ref("Admin Profile"),
                "target": _ref("Opportunity"), "granted_capability": "delete"}
    recipe = _data_recipe("r1", [
        {"kind": "create", "step_id": "c", "target_object": _ref("Opportunity"),
         "field_values": {"Opportunity.StageName": "x"}}])
    skel = _build("capability-claim", asserted, recipes=[recipe],
                  archetype="permission")
    assert skel.registered is False
    assert skel.checks == "Checks Opportunity."
    assert skel.steps == ()                 # never reads the recipe for an unknown shape
    assert skel.test_data == ()
    assert skel.expected_result is None
    assert skel.probes == ()


def test_unknown_kind_headline_only():
    skel = _build("quantum-claim", {"kind": "quantum-claim"})
    assert skel.registered is False
    assert skel.headline == "quantum claim"
    assert skel.checks is None
    assert skel.steps == () and skel.expected_result is None


# ---------------------------------------------------------------------------
# bva probe set
# ---------------------------------------------------------------------------

def test_bva_probes_one_per_data_recipe():
    asserted = {"kind": "automation-effect-claim",
                "automation": _ref("HL_Auto_Risk_Rating"), "automation_primitive": "flow",
                "triggering_action": {"trigger_kind": "data-mutation-trigger",
                                      "description": "d"},
                "expected_effect": {"kind": "field_change", "changes": {
                    "field_values": {"Loan__c.Risk_Rating__c": _lit("High")}}},
                "affected_fields": [_ref("Loan__c.Risk_Rating__c")]}
    r1 = _data_recipe("p1", [{"kind": "create", "step_id": "c",
                              "target_object": _ref("Loan__c"),
                              "field_values": {"Loan__c.Credit_Score__c": 649}}])
    r2 = _data_recipe("p2", [{"kind": "create", "step_id": "c",
                              "target_object": _ref("Loan__c"),
                              "field_values": {"Loan__c.Credit_Score__c": 650}}])
    skel = _build("automation-effect-claim", asserted, recipes=[r1, r2],
                  strategy_kind="bva", data_recipe_ids=["p1", "p2"])
    assert len(skel.probes) == 2
    assert skel.probes[0].input_value == "Credit Score = 649"
    assert skel.probes[1].input_value == "Credit Score = 650"


def test_single_strategy_has_no_probes():
    asserted = {"kind": "value-claim", "subject": _ref("Loan__c.Credit_Score__c"),
                "expected_value": _lit(720)}
    r1 = _data_recipe("p1", [{"kind": "create", "step_id": "c",
                              "target_object": _ref("Loan__c"),
                              "field_values": {"Loan__c.Credit_Score__c": 720}}])
    skel = _build("value-claim", asserted, recipes=[r1],
                  strategy_kind="single", data_recipe_ids=["p1"])
    assert skel.probes == ()


# ---------------------------------------------------------------------------
# Grounding: the boundary number, and the description-prose guard
# ---------------------------------------------------------------------------

def test_boundary_input_is_grounded_but_description_prose_is_not():
    # The 649 lives in the recipe create field_values → grounded. A number that
    # appears ONLY in the free-form triggering_action.description must NOT be
    # grounded (the Stage-2 validator must not be fooled by unstructured prose).
    asserted = {"kind": "automation-effect-claim",
                "automation": _ref("HL_Auto_Risk_Rating"), "automation_primitive": "flow",
                "triggering_action": {"trigger_kind": "data-mutation-trigger",
                                      "description": "reference number 999 applies"},
                "expected_effect": {"kind": "field_change", "changes": {
                    "field_values": {"Loan__c.Risk_Rating__c": _lit("High")}}},
                "affected_fields": [_ref("Loan__c.Risk_Rating__c")]}
    recipe = _data_recipe("r1", [{"kind": "create", "step_id": "c",
                                  "target_object": _ref("Loan__c"),
                                  "field_values": {"Loan__c.Credit_Score__c": 649}}])
    skel = _build("automation-effect-claim", asserted, recipes=[recipe])
    assert "649" in skel.grounded_tokens
    assert "999" not in skel.grounded_tokens


def test_labels_resolved_no_api_names_leak():
    asserted = {"kind": "value-claim", "subject": _ref("Loan__c.Credit_Score__c"),
                "expected_value": _lit(720)}
    recipe = _data_recipe("r1", [{"kind": "create", "step_id": "c",
                                  "target_object": _ref("Loan__c"),
                                  "field_values": {"Loan__c.Credit_Score__c": 720}}])
    skel = _build("value-claim", asserted, recipes=[recipe])
    blob = _all_strings(skel)
    assert "Credit_Score__c" not in blob
    assert "Loan__c" not in blob
    assert "Credit Score" in blob


# ---------------------------------------------------------------------------
# Never raises
# ---------------------------------------------------------------------------

def test_never_raises_on_garbage():
    for asserted in (None, "nonsense", 42, {"kind": "value-claim"}):
        skel = build_readable_body(
            claim_kind="value-claim", archetype=None, asserted_truth=asserted,
            semantic_conditions="bad", recipes="not-a-list",
            strategy_kind="bva", data_recipe_ids=None, labels=None)
        assert isinstance(skel, ReadableBodySkeleton)
        assert isinstance(skel.headline, str)


def test_never_raises_on_malformed_recipe_steps():
    asserted = {"kind": "value-claim", "subject": _ref("Contact.Email"),
                "expected_value": _lit("x@y.com")}
    bad_recipe = {"recipe_id": "r1", "recipe_kind": "data-recipe",
                  "observation_realization": {"steps": ["notadict", {"kind": "???"},
                                                        {"no_kind": True}]}}
    skel = _build("value-claim", asserted, recipes=[bad_recipe])
    assert isinstance(skel, ReadableBodySkeleton)
    assert skel.steps == ()          # unknown/garbage steps skipped, not fabricated

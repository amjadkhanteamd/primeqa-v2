"""Unit: the shape-tolerant VR attribute extractors (D-203.1).

Two attribute shapes coexist in entities.attributes JSONB: the designed
ValidationRuleAttributes projection (formula_text — seeds + pre-cutover sync)
and the post-cutover sync's raw Tooling record (Metadata.errorConditionFormula).
The extractors accept both; readers (S3 verified-gate, S6 attribution) must
never silently lose formulas again."""
from __future__ import annotations

from primeqa.semantic.entity_attributes import vr_error_message, vr_formula_text

_RAW = {  # the post-cutover sync shape (verbatim keys from a real synced row)
    "Id": "03dF9000000d0AAAA", "Active": True,
    "FullName": "Opportunity.Amount",
    "Metadata": {"active": True, "errorConditionFormula": "Amount  > 10000",
                 "errorMessage": "Amount too large"},
    "ErrorMessage": "Amount too large", "ValidationName": "Amount",
}
_DESIGNED = {"formula_text": "ISBLANK(Reason__c)",
             "error_message": "reason required", "error_display_field": None}


def test_designed_shape():
    assert vr_formula_text(_DESIGNED) == "ISBLANK(Reason__c)"
    assert vr_error_message(_DESIGNED) == "reason required"


def test_raw_tooling_shape():
    assert vr_formula_text(_RAW) == "Amount  > 10000"
    assert vr_error_message(_RAW) == "Amount too large"


def test_designed_wins_when_both_present():
    both = {**_RAW, "formula_text": "X > 1"}
    assert vr_formula_text(both) == "X > 1"


def test_empty_and_none_safe():
    assert vr_formula_text(None) is None
    assert vr_formula_text({}) is None
    assert vr_formula_text({"Metadata": None}) is None
    assert vr_error_message({}) is None


# ---------------------------------------------------------------------------
# D-301: vr_is_active — shape-tolerant active flag.
# ---------------------------------------------------------------------------

def test_vr_is_active_designed_shape():
    from primeqa.semantic.entity_attributes import vr_is_active
    assert vr_is_active({"is_active": True}) is True
    assert vr_is_active({"is_active": False}) is False


def test_vr_is_active_raw_tooling_shape():
    from primeqa.semantic.entity_attributes import vr_is_active
    # the live env-59 shape: top-level Active + Metadata.active
    assert vr_is_active({"Active": False, "Metadata": {"active": False}}) is False
    assert vr_is_active({"Active": True, "Metadata": {"active": True}}) is True
    # Metadata-only fallback
    assert vr_is_active({"Metadata": {"active": False}}) is False


def test_vr_is_active_missing_defaults_true():
    from primeqa.semantic.entity_attributes import vr_is_active
    # an attribute-less row must not silently demote negatives to caveated
    assert vr_is_active({}) is True
    assert vr_is_active(None) is True
    assert vr_is_active({"formula_text": "Amount > 0"}) is True


# ---------------------------------------------------------------------------
# D-304: field_is_calculated / field_formula_text — two-shape tolerance.
# ---------------------------------------------------------------------------

def test_field_is_calculated_shapes():
    from primeqa.semantic.entity_attributes import field_is_calculated
    assert field_is_calculated({"is_calculated": True}) is True       # designed
    assert field_is_calculated({"calculated": True}) is True          # raw describe
    assert field_is_calculated({"calculated": False}) is False
    assert field_is_calculated({}) is False                           # plain field
    assert field_is_calculated(None) is False


def test_field_formula_text_shapes():
    from primeqa.semantic.entity_attributes import field_formula_text
    assert field_formula_text({"formula": "A + B"}) == "A + B"        # designed
    # the live env-59 raw shape (probe-verified on Loan_to_Value__c)
    assert field_formula_text(
        {"calculatedFormula": "IF(P > 0, L / P, null)"}) == "IF(P > 0, L / P, null)"
    assert field_formula_text({}) is None
    assert field_formula_text(None) is None


# ---------------------------------------------------------------------------
# D-318: flow_effects — parse a Flow's raw Metadata into its EFFECT set so the
# automation-effect resolver can bind by effect, not by the LLM naming the Flow.
# ---------------------------------------------------------------------------

def _md(**metadata):
    return {"Metadata": metadata}


def test_flow_effects_same_record_from_record_updates():
    from primeqa.semantic.entity_attributes import flow_effects
    # env-59 HL_Auto_Risk_Rating shape: 3 recordUpdates each stamping Risk_Rating__c
    attrs = _md(recordUpdates=[
        {"name": "Set_Risk_High",
         "inputAssignments": [{"field": "Risk_Rating__c", "value": {"stringValue": "High"}}]},
        {"name": "Set_Risk_Medium",
         "inputAssignments": [{"field": "Risk_Rating__c", "value": {"stringValue": "Medium"}}]},
        {"name": "Set_Risk_Low",
         "inputAssignments": [{"field": "Risk_Rating__c", "value": {"stringValue": "Low"}}]},
    ])
    eff = flow_effects(attrs)
    assert eff["same_record"] == frozenset({
        ("Risk_Rating__c", "High"), ("Risk_Rating__c", "Medium"), ("Risk_Rating__c", "Low")})
    assert eff["cross_object"] == frozenset()


def test_flow_effects_cross_object_from_record_creates():
    from primeqa.semantic.entity_attributes import flow_effects
    # env-59 HL_High_Risk_Task shape: creates a Task
    eff = flow_effects(_md(recordCreates=[{"name": "Create_Review_Task", "object": "Task"}]))
    assert eff["cross_object"] == frozenset({"Task"})
    assert eff["same_record"] == frozenset()


def test_flow_effects_value_shapes_and_bare_field():
    from primeqa.semantic.entity_attributes import flow_effects
    eff = flow_effects(_md(recordUpdates=[{"inputAssignments": [
        {"field": "Opportunity.Amount", "value": {"numberValue": 5000}},   # qualified -> bare tail
        {"field": "IsWon__c", "value": {"booleanValue": True}},
        {"field": "Close_Date__c", "value": {"dateValue": "2026-01-01"}},
    ]}]))
    assert eff["same_record"] == frozenset({
        ("Amount", 5000), ("IsWon__c", True), ("Close_Date__c", "2026-01-01")})


def test_flow_effects_reference_assignment_has_none_value():
    from primeqa.semantic.entity_attributes import flow_effects
    # a formula/elementReference assignment carries no literal -> value None
    eff = flow_effects(_md(recordUpdates=[{"inputAssignments": [
        {"field": "Owner__c", "value": {"elementReference": "SomeVar"}}]}]))
    assert eff["same_record"] == frozenset({("Owner__c", None)})


def test_flow_effects_single_dict_not_list_tolerated():
    from primeqa.semantic.entity_attributes import flow_effects
    # Salesforce serializes a one-element collection as a bare dict
    eff = flow_effects(_md(
        recordUpdates={"inputAssignments": {"field": "X__c", "value": {"stringValue": "y"}}},
        recordCreates={"object": "Case"}))
    assert eff["same_record"] == frozenset({("X__c", "y")})
    assert eff["cross_object"] == frozenset({"Case"})


def test_flow_effects_empty_and_odd_metadata_safe():
    from primeqa.semantic.entity_attributes import flow_effects
    empty = {"same_record": frozenset(), "cross_object": frozenset()}
    assert flow_effects(None) == empty
    assert flow_effects({}) == empty
    assert flow_effects({"Metadata": None}) == empty
    assert flow_effects({"Metadata": "not a dict"}) == empty
    assert flow_effects(_md(recordUpdates="bad", recordCreates=123)) == empty
    # missing inputAssignments / non-dict items are skipped, never raise
    assert flow_effects(_md(recordUpdates=[{"name": "no-assigns"}, "junk", None])) == empty
    assert flow_effects(_md(recordCreates=[{"name": "no-object"}, {"object": ""}])) == empty


def test_flow_effects_nonscalar_value_is_no_literal_never_raises():
    from primeqa.semantic.entity_attributes import flow_effects
    # A dict/list under a scalar key (malformed / future API drift) must NOT crash
    # the .add() into the set — the field is kept with value None (no literal).
    eff = flow_effects(_md(recordUpdates=[{"inputAssignments": [
        {"field": "A__c", "value": {"numberValue": [1, 2]}},    # list — unhashable
        {"field": "B__c", "value": {"stringValue": {"nested": 1}}},  # dict — unhashable
    ]}]))
    assert eff["same_record"] == frozenset({("A__c", None), ("B__c", None)})

"""Flow Behaviour IR (B1 arc) — bounded parse of stored Flow Metadata.

Fixture-driven: ``fixtures/pls_fb_flows/*.json`` are byte snapshots of the
FB-V1 benchmark org's synced Flow entity ``attributes`` (16 flows,
2026-07-11). The suite pins the arc's scope contract, updated per slice:
**FL01 + FL03 are the LITERAL-grounded flows (FL03 via the C1 ordered-
decision fan-out) and FL02 the sole TRANSFORM-grounded flow**; every other
flow is unsupported/opaque with NAMED reasons, preserving honest refusal
downstream ("no unintended benchmark expansion").
"""
import json
import os

import pytest

from primeqa.semantic.entity_attributes import (
    flow_grounded_temporal_effects,
    _fb_parse_transform_formula, apply_transform_chain, flow_behaviour,
    flow_grounded_same_record_effects, flow_grounded_transforms)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures",
                           "pls_fb_flows")


def _load(name: str) -> dict:
    with open(os.path.join(FIXTURE_DIR, f"{name}.json")) as f:
        return json.load(f)


def _all_fixture_names():
    return sorted(f[:-5] for f in os.listdir(FIXTURE_DIR)
                  if f.endswith(".json"))


# ── the grounded one ────────────────────────────────────────────────

def test_fl01_is_grounded_with_guard_and_literal_effect():
    ir = flow_behaviour(_load("PLS_FB_FL01_Default_Priority"))
    assert ir["ir_version"] == 2
    assert ir["parse_status"] == "full"
    t = ir["trigger"]
    assert t["object"] == "PLS_FB_Order__c"
    assert t["save_phase"] == "before_save"
    assert t["record_trigger_type"] == "Create"
    assert t["entry_filter_count"] == 0
    assert t["requires_change_to_meet"] is False
    assert t["is_record_triggered"] is True
    [b] = ir["behaviours"]
    assert b["state"] == "grounded"
    assert b["kind"] == "set_record_field"
    assert b["field"] == "PLS_FB_Priority__c"
    assert b["value"] == "Standard"
    assert b["guard"] == [["PLS_FB_Priority__c", "IsNull", True]]
    assert b["reasons"] == []


def test_fl01_binder_projection_exposes_the_effect_pair():
    eff = flow_grounded_same_record_effects(
        _load("PLS_FB_FL01_Default_Priority"))
    assert eff == frozenset({("PLS_FB_Priority__c", "Standard")})


# ── the scope contract: nothing else grounds ────────────────────────

def test_grounded_scope_contract():
    # scope contract: FL01 (literal), FL02 (transform), FL03 (ordered
    # bands), FL08 (temporal stamp, C4); nothing else grounds.
    grounded = {
        name for name in _all_fixture_names()
        if any(b["state"] == "grounded"
               for b in flow_behaviour(_load(name))["behaviours"])
    }
    assert grounded == {"PLS_FB_FL01_Default_Priority",
                        "PLS_FB_FL02_Normalize_External_Ref",
                        "PLS_FB_FL03_Tier_Banding",
                        "PLS_FB_FL08_SLA_Stamp"}


def test_fl03_grounds_four_band_arms_with_negation_context():
    ir = flow_behaviour(_load("PLS_FB_FL03_Tier_Banding"))
    assert ir["parse_status"] == "full"
    arms = {b["value"]: b for b in ir["behaviours"]}
    assert set(arms) == {"Platinum", "Gold", "Silver", "Bronze"}
    assert all(b["state"] == "grounded"
               and b["kind"] == "set_record_field"
               and b["field"] == "PLS_FB_Tier__c"
               for b in ir["behaviours"])
    amt = "PLS_FB_Amount__c"
    assert arms["Platinum"]["guard"] == [[amt, "GreaterThanOrEqualTo", 250000.0]]
    assert arms["Platinum"]["negated_guards"] == []
    assert arms["Gold"]["guard"] == [[amt, "GreaterThanOrEqualTo", 50000.0]]
    assert arms["Gold"]["negated_guards"] == [[amt, "GreaterThanOrEqualTo", 250000.0]]
    assert arms["Silver"]["negated_guards"] == [
        [amt, "GreaterThanOrEqualTo", 250000.0],
        [amt, "GreaterThanOrEqualTo", 50000.0]]
    # the default arm: no positive guard, ALL rules negated
    assert arms["Bronze"]["guard"] == []
    assert arms["Bronze"]["negated_guards"] == [
        [amt, "GreaterThanOrEqualTo", 250000.0],
        [amt, "GreaterThanOrEqualTo", 50000.0],
        [amt, "GreaterThanOrEqualTo", 10000.0]]


def test_fl02_is_grounded_as_a_transform_with_consumed_entry_filter():
    ir = flow_behaviour(_load("PLS_FB_FL02_Normalize_External_Ref"))
    assert ir["ir_version"] == 2
    assert ir["parse_status"] == "full"
    t = ir["trigger"]
    assert t["save_phase"] == "before_save"
    assert t["record_trigger_type"] == "CreateAndUpdate"
    assert t["entry_filter_count"] == 1
    assert t["entry_filters_consumed"] is True
    [b] = ir["behaviours"]
    assert b["state"] == "grounded"
    assert b["kind"] == "set_record_field_transform"
    assert b["field"] == "PLS_FB_External_Ref__c"
    assert b["source_field"] == "PLS_FB_External_Ref__c"
    assert b["transform"] == ["TRIM", "UPPER"]   # application order
    assert b["guard"] == [["PLS_FB_External_Ref__c", "IsNull", False]]
    assert b["value"] is None


def test_fl02_binder_projections_split_cleanly():
    attrs = _load("PLS_FB_FL02_Normalize_External_Ref")
    # the LITERAL projection stays empty — transforms never leak into it
    assert flow_grounded_same_record_effects(attrs) == frozenset()
    (tr,) = flow_grounded_transforms(attrs)
    assert tr == {"field": "PLS_FB_External_Ref__c",
                  "transform": ("TRIM", "UPPER"),
                  "source_field": "PLS_FB_External_Ref__c",
                  "guard": (("PLS_FB_External_Ref__c", "IsNull", False),)}
    assert apply_transform_chain(tr["transform"], "  fb-000123  ") == "FB-000123"


def test_no_other_flow_contributes_binder_effects():
    literal_ok = {"PLS_FB_FL01_Default_Priority", "PLS_FB_FL03_Tier_Banding"}
    for name in _all_fixture_names():
        if name not in literal_ok:
            assert flow_grounded_same_record_effects(_load(name)) == frozenset(), name
        if name != "PLS_FB_FL02_Normalize_External_Ref":
            assert flow_grounded_transforms(_load(name)) == (), name


# ── named reasons per excluded flow (honest refusal is diagnosable) ──

@pytest.mark.parametrize("name,expected_reason", [
    # after-save side effect (task create)
    ("PLS_FB_FL04_Confirmation_Task", "element_outside_grammar:recordCreates"),
    # after-save fan-out via Get Records
    ("PLS_FB_FL05_Cancellation_Sync", "element_outside_grammar:recordLookups"),
    # data-dependent duplicate lookup
    ("PLS_FB_FL06_Duplicate_Flag", "element_outside_grammar:recordLookups"),
    # child-object loop rollup
    ("PLS_FB_FL07_Order_Rollup", "element_outside_grammar:recordLookups"),
    # $Record__Prior guard is outside the guard grammar
    ("PLS_FB_FL09_Reopen_Guard", "unparseable_guard_condition"),
    # scheduled path only — nothing observable at save time
    ("PLS_FB_FL10_Stale_Order_Escalation", "scheduled_path_only"),
    # async path only
    ("PLS_FB_FL11_Async_Enrichment", "async_path_only"),
    # composition capstone — lookup + subflow
    ("PLS_FB_FL12_Fulfilment_Orchestrator",
     "element_outside_grammar:recordLookups"),
    # fault-handled ledger create
    ("PLS_FB_FL13_Fault_Logged_Ledger",
     "element_outside_grammar:recordCreates"),
    # approval submit action
    ("PLS_FB_FL14_Approval_Submit", "element_outside_grammar:actionCalls"),
    # send-email action
    ("PLS_FB_FL15_Confirmation_Email", "element_outside_grammar:actionCalls"),
    # autolaunched subflow — not record-triggered
    ("PLS_FB_SF01_Close_Tasks", "not_record_triggered"),
])
def test_excluded_flows_carry_named_reasons(name, expected_reason):
    ir = flow_behaviour(_load(name))
    reasons = {r for b in ir["behaviours"] for r in b["reasons"]}
    assert expected_reason in reasons, (name, sorted(reasons))
    assert all(b["state"] in ("unsupported", "opaque")
               for b in ir["behaviours"]), name


# ── C4: FL08 grounds as a temporal stamp on an update-to-meet trigger ─

def test_fl08_grounds_as_a_temporal_stamp():
    ir = flow_behaviour(_load("PLS_FB_FL08_SLA_Stamp"))
    assert ir["parse_status"] == "full"
    t = ir["trigger"]
    assert t["record_trigger_type"] == "Update"
    assert t["requires_change_to_meet"] is True
    assert t["entry_filters_consumed"] is True
    [b] = ir["behaviours"]
    assert b["state"] == "grounded"
    assert b["kind"] == "set_record_field_temporal"
    assert b["field"] == "PLS_FB_SLA_Deadline__c"
    assert b["offset_days"] == 5
    assert b["guard"] == [["PLS_FB_Status__c", "EqualTo", "Submitted"]]
    assert b["value"] is None and b["transform"] == []


def test_fl08_enters_only_the_temporal_projection():
    attrs = _load("PLS_FB_FL08_SLA_Stamp")
    # Update-only: the create-scoped projections must all refuse it
    assert flow_grounded_same_record_effects(attrs) == frozenset()
    assert flow_grounded_transforms(attrs) == ()
    (tp,) = flow_grounded_temporal_effects(attrs)
    assert tp == {"field": "PLS_FB_SLA_Deadline__c", "offset_days": 5,
                  "guard": (("PLS_FB_Status__c", "EqualTo", "Submitted"),),
                  "negated_guards": ()}


def test_temporal_projection_is_update_trigger_only():
    # a Create-trigger clone of the FL08 shape stays OUT of the projection
    # (v1 scope: the transition shape is what updated-to-meet promises)
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "Stamp"}},
        "formulas": [{"name": "f", "dataType": "Date",
                      "expression": "{!$Flow.CurrentDate} + 3"}],
        "assignments": [
            {"name": "Stamp",
             "assignmentItems": [{"assignToReference": "$Record.Due__c",
                                  "operator": "Assign",
                                  "value": {"elementReference": "f"}}]},
        ],
    }}
    assert flow_grounded_temporal_effects(attrs) == ()
    # ...but the IR itself grounds it (honest representation; a create-
    # scoped temporal consumer is an evidence-driven future slice)
    ir = flow_behaviour(attrs)
    [b] = ir["behaviours"]
    assert b["state"] == "grounded" and b["offset_days"] == 3


# ── never-raises shape tolerance ────────────────────────────────────

@pytest.mark.parametrize("attrs", [
    None, {}, {"Metadata": None}, {"Metadata": "not-a-dict"},
    {"Metadata": {}}, {"Metadata": {"start": None}},
    {"Metadata": {"start": []}},
])
def test_malformed_attributes_are_opaque_never_raise(attrs):
    ir = flow_behaviour(attrs)
    assert ir["parse_status"] == "opaque"
    assert ir["behaviours"] == []
    assert flow_grounded_same_record_effects(attrs) == frozenset()


def test_unknown_walk_target_is_opaque():
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "Ghost"}},
    }}
    ir = flow_behaviour(attrs)
    assert ir["parse_status"] == "opaque"
    reasons = {r for b in ir["behaviours"] for r in b["reasons"]}
    assert "unknown_element:Ghost" in reasons


def test_cycle_is_opaque_and_bounded():
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "A"}},
        "assignments": [
            {"name": "A",
             "assignmentItems": [{"assignToReference": "$Record.F__c",
                                  "operator": "Assign",
                                  "value": {"stringValue": "v"}}],
             "connector": {"targetReference": "A"}},
        ],
    }}
    ir = flow_behaviour(attrs)
    assert ir["parse_status"] == "opaque"
    # the would-be effect is demoted, never exposed to the binder
    assert flow_grounded_same_record_effects(attrs) == frozenset()


def test_unconditional_literal_assignment_grounds_without_guard():
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "CreateAndUpdate",
                  "connector": {"targetReference": "Set"}},
        "assignments": [
            {"name": "Set",
             "assignmentItems": [{"assignToReference": "$Record.F__c",
                                  "operator": "Assign",
                                  "value": {"stringValue": "v"}}]},
        ],
    }}
    ir = flow_behaviour(attrs)
    assert ir["parse_status"] == "full"
    [b] = ir["behaviours"]
    assert b["state"] == "grounded" and b["guard"] == []
    assert flow_grounded_same_record_effects(attrs) == frozenset(
        {("F__c", "v")})


def test_partial_path_demotes_grounded_siblings():
    # a clean literal assignment FOLLOWED by an out-of-grammar element:
    # the grounded behaviour must demote (the later element could overwrite)
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "Set"}},
        "assignments": [
            {"name": "Set",
             "assignmentItems": [{"assignToReference": "$Record.F__c",
                                  "operator": "Assign",
                                  "value": {"stringValue": "v"}}],
             "connector": {"targetReference": "Look"}},
        ],
        "recordLookups": [{"name": "Look"}],
    }}
    ir = flow_behaviour(attrs)
    assert ir["parse_status"] == "partial"
    assert all(b["state"] != "grounded" for b in ir["behaviours"])
    assert flow_grounded_same_record_effects(attrs) == frozenset()


# ── IR v2: transform grammar + entry-filter boundaries ───────────────

@pytest.mark.parametrize("expr,expected", [
    ("UPPER(TRIM({!$Record.X__c}))", (("TRIM", "UPPER"), "X__c")),
    ("TRIM({!$Record.X__c})", (("TRIM",), "X__c")),
    ("LOWER(UPPER(TRIM({!$Record.A_B__c})))",
     (("TRIM", "UPPER", "LOWER"), "A_B__c")),
    ("LEN({!$Record.X__c})", None),                 # unknown function
    ("UPPER({!$Record.X__c}, 2)", None),            # extra argument
    ("UPPER(X__c)", None),                          # non-$Record argument
    ("UPPER(TRIM(LOWER(UPPER({!$Record.X__c}))))", None),  # depth > 3
    ("TODAY() + 5", None),                          # FL08's class
    (None, None),
])
def test_transform_formula_grammar_bounds(expr, expected):
    assert _fb_parse_transform_formula(expr) == expected


def _entry_filter_flow(filters):
    return {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "Create", "filterLogic": "and",
                  "filters": filters,
                  "connector": {"targetReference": "Set"}},
        "assignments": [
            {"name": "Set",
             "assignmentItems": [{"assignToReference": "$Record.F__c",
                                  "operator": "Assign",
                                  "value": {"stringValue": "v"}}]},
        ],
    }}


def test_equalto_entry_filter_is_consumed_as_a_guard():
    # C4: the literal EqualTo entry filter joins the consumption grammar —
    # the flow grounds and the filter rides the behaviour guard
    attrs = _entry_filter_flow([{"field": "Status__c", "operator": "EqualTo",
                                 "value": {"stringValue": "Open"}}])
    ir = flow_behaviour(attrs)
    assert ir["parse_status"] == "full"
    [b] = ir["behaviours"]
    assert b["state"] == "grounded"
    assert b["guard"] == [["Status__c", "EqualTo", "Open"]]


def test_out_of_grammar_entry_filter_still_demotes():
    # operators beyond IsNull/EqualTo (or a value-less EqualTo) keep the
    # flow-level entry_conditions_not_consumed demotion
    for filters in (
        [{"field": "Status__c", "operator": "Contains",
          "value": {"stringValue": "Op"}}],
        [{"field": "Status__c", "operator": "EqualTo",
          "value": {"elementReference": "someVar"}}],
    ):
        attrs = _entry_filter_flow(filters)
        ir = flow_behaviour(attrs)
        reasons = {r for b in ir["behaviours"] for r in b["reasons"]}
        assert "entry_conditions_not_consumed" in reasons, filters
        assert all(b["state"] != "grounded" for b in ir["behaviours"])
        assert flow_grounded_same_record_effects(attrs) == frozenset()


def test_transform_then_outside_grammar_demotes_conservatively():
    # conservatism holds for transforms exactly as for literals
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "Norm"}},
        "formulas": [{"name": "f", "dataType": "String",
                      "expression": "UPPER({!$Record.F__c})"}],
        "assignments": [
            {"name": "Norm",
             "assignmentItems": [{"assignToReference": "$Record.F__c",
                                  "operator": "Assign",
                                  "value": {"elementReference": "f"}}],
             "connector": {"targetReference": "Look"}},
        ],
        "recordLookups": [{"name": "Look"}],
    }}
    attrs_ir = flow_behaviour(attrs)
    assert attrs_ir["parse_status"] == "partial"
    assert flow_grounded_transforms(attrs) == ()


def test_cross_field_transform_grounds():
    # source != target stays within the grammar (copy-normalize shape)
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "Norm"}},
        "formulas": [{"name": "f", "dataType": "String",
                      "expression": "TRIM({!$Record.Raw__c})"}],
        "assignments": [
            {"name": "Norm",
             "assignmentItems": [{"assignToReference": "$Record.Clean__c",
                                  "operator": "Assign",
                                  "value": {"elementReference": "f"}}]},
        ],
    }}
    (tr,) = flow_grounded_transforms(attrs)
    assert tr["field"] == "Clean__c" and tr["source_field"] == "Raw__c"
    assert tr["transform"] == ("TRIM",)


# ── C1: ordered-decision fan-out boundaries ──────────────────────────

def _decision_flow(rules, default=None, extra=None):
    md = {
        "start": {"object": "X__c", "triggerType": "RecordBeforeSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "D"}},
        "decisions": [{"name": "D", "rules": rules,
                       **({"defaultConnector": {"targetReference": default}}
                          if default else {})}],
        "assignments": [],
    }
    if extra:
        md.update(extra)
    return {"Metadata": md}


def _rule(name, field, op, num, target):
    return {"name": name, "conditionLogic": "and",
            "conditions": [{"leftValueReference": f"$Record.{field}",
                            "operator": op,
                            "rightValue": {"numberValue": num}}],
            "connector": {"targetReference": target}}


def _assign(name, field, value):
    return {"name": name,
            "assignmentItems": [{"assignToReference": f"$Record.{field}",
                                 "operator": "Assign",
                                 "value": {"stringValue": value}}]}


def test_per_path_conservatism_sibling_arms_survive():
    # band A -> clean literal; band B -> a lookup (out of grammar):
    # A stays grounded, B demotes with its own reason, status is partial.
    attrs = _decision_flow(
        rules=[_rule("R1", "Amt__c", "GreaterThanOrEqualTo", 100, "SetA"),
               _rule("R2", "Amt__c", "GreaterThanOrEqualTo", 10, "Look")],
        extra={"assignments": [_assign("SetA", "Band__c", "A")],
               "recordLookups": [{"name": "Look"}]})
    ir = flow_behaviour(attrs)
    assert ir["parse_status"] == "partial"
    by_state = {}
    for b in ir["behaviours"]:
        by_state.setdefault(b["state"], []).append(b)
    assert len(by_state.get("grounded", [])) == 1
    assert by_state["grounded"][0]["value"] == "A"
    reasons = {r for b in by_state.get("unsupported", []) for r in b["reasons"]}
    assert "element_outside_grammar:recordLookups" in reasons
    # the grounded arm still feeds the binder
    assert flow_grounded_same_record_effects(attrs) == frozenset({("Band__c", "A")})


def test_multi_condition_rule_in_ordered_decision_demotes():
    two_cond = {"name": "R1", "conditionLogic": "and",
                "conditions": [
                    {"leftValueReference": "$Record.A__c", "operator": "EqualTo",
                     "rightValue": {"stringValue": "x"}},
                    {"leftValueReference": "$Record.B__c", "operator": "EqualTo",
                     "rightValue": {"stringValue": "y"}}],
                "connector": {"targetReference": "S1"}}
    attrs = _decision_flow(
        rules=[two_cond,
               _rule("R2", "Amt__c", "GreaterThan", 1, "S2")],
        extra={"assignments": [_assign("S1", "F__c", "a"),
                               _assign("S2", "F__c", "b")]})
    ir = flow_behaviour(attrs)
    reasons = {r for b in ir["behaviours"] for r in b["reasons"]}
    assert "multi_condition_rule_in_ordered_decision" in reasons
    assert all(b["state"] != "grounded" for b in ir["behaviours"])


def test_decision_rule_bound_is_enforced():
    rules = [_rule(f"R{i}", "Amt__c", "GreaterThan", i, "S")
             for i in range(7)]
    attrs = _decision_flow(rules=rules,
                           extra={"assignments": [_assign("S", "F__c", "v")]})
    ir = flow_behaviour(attrs)
    reasons = {r for b in ir["behaviours"] for r in b["reasons"]}
    assert "decision_rule_bound_exceeded" in reasons


def test_single_rule_decision_keeps_conjunctive_grammar():
    # FL01's shape: one rule, multiple conjunctive conditions still ground
    two_cond = {"name": "R1", "conditionLogic": "and",
                "conditions": [
                    {"leftValueReference": "$Record.A__c", "operator": "EqualTo",
                     "rightValue": {"stringValue": "x"}},
                    {"leftValueReference": "$Record.B__c", "operator": "IsNull",
                     "rightValue": {"booleanValue": True}}],
                "connector": {"targetReference": "S1"}}
    attrs = _decision_flow(rules=[two_cond],
                           extra={"assignments": [_assign("S1", "F__c", "v")]})
    ir = flow_behaviour(attrs)
    assert ir["parse_status"] == "full"
    [b] = ir["behaviours"]
    assert b["state"] == "grounded" and len(b["guard"]) == 2

# ── C2: after-save admission — phase is a property, not a demotion ────

def test_after_save_literal_flow_grounds():
    # phase-safety for literals holds by k16 (the create never stages the
    # effect field) + post-commit reads: an after-save flow's write is
    # observable exactly like a before-save one
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordAfterSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "Set"}},
        "assignments": [
            {"name": "Set",
             "assignmentItems": [{"assignToReference": "$Record.F__c",
                                  "operator": "Assign",
                                  "value": {"stringValue": "v"}}]},
        ],
    }}
    ir = flow_behaviour(attrs)
    assert ir["parse_status"] == "full"
    assert ir["trigger"]["save_phase"] == "after_save"
    [b] = ir["behaviours"]
    assert b["state"] == "grounded"
    assert flow_grounded_same_record_effects(attrs) == frozenset(
        {("F__c", "v")})


def test_after_save_transform_is_excluded_from_the_transform_projection():
    # an after-save transform's raw witness would face the validation rules
    # BEFORE the flow runs — no passing test is constructible, so the
    # projection (whose consumers rely on the runs-before-VRs fact)
    # excludes the whole flow even though the IR grounds the behaviour
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordAfterSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "Norm"}},
        "formulas": [{"name": "f", "dataType": "String",
                      "expression": "UPPER({!$Record.F__c})"}],
        "assignments": [
            {"name": "Norm",
             "assignmentItems": [{"assignToReference": "$Record.F__c",
                                  "operator": "Assign",
                                  "value": {"elementReference": "f"}}]},
        ],
    }}
    ir = flow_behaviour(attrs)
    [b] = ir["behaviours"]
    assert b["state"] == "grounded"          # the IR carries the behaviour…
    assert flow_grounded_transforms(attrs) == ()   # …the projection refuses

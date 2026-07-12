"""Flow Behaviour IR (B1 arc) — bounded parse of stored Flow Metadata.

Fixture-driven: ``fixtures/pls_fb_flows/*.json`` are byte snapshots of the
FB-V1 benchmark org's synced Flow entity ``attributes`` (16 flows,
2026-07-11). The suite pins the arc's scope contract: **FL01 is the sole
grounded flow**; every other flow is unsupported/opaque with NAMED reasons,
preserving honest refusal downstream ("no unintended benchmark expansion").
"""
import json
import os

import pytest

from primeqa.semantic.entity_attributes import (
    flow_behaviour, flow_grounded_same_record_effects)

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
    assert ir["ir_version"] == 1
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

def test_fl01_is_the_sole_grounded_flow_in_the_benchmark_org():
    grounded = {
        name for name in _all_fixture_names()
        if any(b["state"] == "grounded"
               for b in flow_behaviour(_load(name))["behaviours"])
    }
    assert grounded == {"PLS_FB_FL01_Default_Priority"}


def test_no_other_flow_contributes_binder_effects():
    for name in _all_fixture_names():
        if name == "PLS_FB_FL01_Default_Priority":
            continue
        assert flow_grounded_same_record_effects(_load(name)) == frozenset(), name


# ── named reasons per excluded flow (honest refusal is diagnosable) ──

@pytest.mark.parametrize("name,expected_reason", [
    # formula-valued assignment (UPPER/TRIM) — value not literal
    ("PLS_FB_FL02_Normalize_External_Ref", "non_literal_assignment_value"),
    # four ordered band outcomes — first-match semantics not in grammar
    ("PLS_FB_FL03_Tier_Banding", "multi_outcome_decision"),
    # after-save side effect (task create)
    ("PLS_FB_FL04_Confirmation_Task", "element_outside_grammar:recordCreates"),
    # after-save fan-out via Get Records
    ("PLS_FB_FL05_Cancellation_Sync", "element_outside_grammar:recordLookups"),
    # data-dependent duplicate lookup
    ("PLS_FB_FL06_Duplicate_Flag", "element_outside_grammar:recordLookups"),
    # child-object loop rollup
    ("PLS_FB_FL07_Order_Rollup", "element_outside_grammar:recordLookups"),
    # formula date assignment on an update-to-meet trigger
    ("PLS_FB_FL08_SLA_Stamp", "non_literal_assignment_value"),
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


# ── update-to-meet flows advertise both blockers, not just one ──────

def test_fl08_names_the_update_trigger_demotions_too():
    ir = flow_behaviour(_load("PLS_FB_FL08_SLA_Stamp"))
    reasons = {r for b in ir["behaviours"] for r in b["reasons"]}
    assert "entry_conditions_not_consumed" in reasons
    assert "updated_to_meet_not_consumed" in reasons
    assert "trigger_type_not_in_grammar:Update" in reasons


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

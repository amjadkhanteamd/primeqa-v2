"""Wave 3 CP1 — cross-record premise reasoning (pure, fixture-driven)."""
from __future__ import annotations

import json
import os

from primeqa.semantic.entity_attributes import flow_cross_record_premises
from primeqa.semantic.premise_reasoning import (
    CARDINALITY, classify_relation, staging_plan)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures",
                           "pls_fb_flows")


def _premise(name):
    with open(os.path.join(FIXTURE_DIR, f"{name}.json")) as f:
        d = json.load(f)
    (p,) = flow_cross_record_premises({"Metadata": d["Metadata"]})
    return p


def test_child_lookup_classification_fl05():
    rel = classify_relation(_premise("PLS_FB_FL05_Cancellation_Sync"))
    assert rel["kind"] == "child_lookup"
    assert rel["correlation_field"] == "PLS_FB_Order__c"
    assert rel["subject_field"] == "Id"
    assert rel["literals"] == (("PLS_FB_Status__c", "EqualTo", "Open"),)


def test_sibling_set_classification_fl07():
    rel = classify_relation(_premise("PLS_FB_FL07_Order_Rollup"))
    assert rel["kind"] == "sibling_set"
    assert rel["subject_field"] == "PLS_FB_Order__c"


def test_parent_lookup_and_uncorrelated():
    rel = classify_relation({"filters": (
        ("Id", "EqualTo", ("$Record", "PLS_FB_Parent__c")),)})
    assert rel["kind"] == "parent_lookup"
    rel = classify_relation({"filters": (("Status__c", "EqualTo", "Open"),)})
    assert rel["kind"] == "uncorrelated"


def test_staging_plans_per_cardinality():
    p = _premise("PLS_FB_FL05_Cancellation_Sync")
    for pred, n, k in [("exists", None, 1), ("not_exists", None, 0),
                       ("count_equals", 3, 3), ("count_at_least", 2, 2),
                       ("count_less_than", 2, 1), ("single_record", None, 1)]:
        plan = staging_plan(p, pred, n)
        assert "refusal" not in plan, (pred, plan)
        assert plan["create_matching"] == k, pred
        assert plan["template"] == (("PLS_FB_Status__c", "Open"),)
        assert plan["correlate"]["field"] == "PLS_FB_Order__c"
    # the distractor discriminates the filter (absent for not_exists)
    assert staging_plan(p, "exists")["distractor"] == {
        "flip_field": "PLS_FB_Status__c", "from_value": "Open"}
    assert staging_plan(p, "not_exists")["distractor"] is None


def test_refusals_are_named():
    unc = {"filters": (("Status__c", "EqualTo", "Open"),)}
    assert staging_plan(unc, "exists")["refusal"] == \
        "uncorrelated_premise_cannot_be_isolated"
    p = _premise("PLS_FB_FL05_Cancellation_Sync")
    assert "unknown_predicate" in staging_plan(p, "sum")["refusal"]
    assert "needs_n" in staging_plan(p, "count_equals")["refusal"]
    assert "staging_bound_exceeded" in \
        staging_plan(p, "count_equals", 99)["refusal"]
    parent = {"filters": (("Id", "EqualTo", ("$Record", "Par__c")),)}
    assert "existence_only" in \
        staging_plan(parent, "count_equals", 2)["refusal"]
    two = {"filters": (("A__c", "EqualTo", ("$Record", "Id")),
                       ("B__c", "EqualTo", ("$Record", "Id")))}
    assert staging_plan(two, "exists")["refusal"] == \
        "multiple_correlation_markers"


def test_deterministic_and_bounded():
    p = _premise("PLS_FB_FL07_Order_Rollup")
    assert staging_plan(p, "count_equals", 2) == \
        staging_plan(p, "count_equals", 2)
    assert set(CARDINALITY) == {"exists", "not_exists", "count_equals",
                                "count_at_least", "count_less_than",
                                "single_record"}


# ── Wave 3 (CP2): collections ────────────────────────────────────────

def test_fl07_loop_aggregates_are_captured():
    from primeqa.semantic.entity_attributes import flow_collection_aggregates
    with open(os.path.join(FIXTURE_DIR,
                           "PLS_FB_FL07_Order_Rollup.json")) as f:
        d = json.load(f)
    (c,) = flow_collection_aggregates({"Metadata": d["Metadata"]})
    assert c["source"] == "Get_All_Lines" and c["loop"] == "Loop_Lines"
    assert ({"fn": "Sum", "field": "PLS_FB_Line_Total__c",
             "into": "varTotal"} in [dict(a) for a in c["aggregates"]])
    assert ({"fn": "Count", "field": None, "into": "varCount"}
            in [dict(a) for a in c["aggregates"]])


def test_aggregate_expectations_compose_with_plans():
    from primeqa.semantic.premise_reasoning import aggregate_expectation
    p = _premise("PLS_FB_FL07_Order_Rollup")
    plan = staging_plan(p, "count_equals", 3)
    assert aggregate_expectation("Count", plan) == 3
    assert aggregate_expectation("Sum", plan, staged_value=100.0) == 300.0
    assert "sum_needs" in aggregate_expectation("Sum", plan)
    assert "unsupported_aggregate" in aggregate_expectation("Avg", plan)


def test_forall_composes_as_not_exists_plus_violating_distractor():
    from primeqa.semantic.premise_reasoning import forall_plan
    p = _premise("PLS_FB_FL05_Cancellation_Sync")
    plan = forall_plan(p, "PLS_FB_Status__c")
    assert "refusal" not in plan
    assert plan["create_matching"] == 0
    assert plan["distractor"]["flip_field"] == "PLS_FB_Status__c"
    assert plan["assert"]["predicate"] == "forall_via_not_exists"
    assert "not_a_literal" in forall_plan(p, "Nope__c")["refusal"]


# ── Wave 3 (CP4): typed cross-record effect ops ──────────────────────

def _ops(name):
    from primeqa.semantic.entity_attributes import flow_cross_record_effect_ops
    with open(os.path.join(FIXTURE_DIR, f"{name}.json")) as f:
        d = json.load(f)
    return flow_cross_record_effect_ops({"Metadata": d["Metadata"]})


def test_fl04_create_effect_fully_typed():
    (o,) = _ops("PLS_FB_FL04_Confirmation_Task")
    assert o["kind"] == "create_record"
    assert o["object"] == "PLS_FB_Fulfilment_Task__c"
    a = o["assignments"]
    assert a["PLS_FB_Order__c"] == ("subject_ref", "Id")
    assert a["PLS_FB_Status__c"] == ("literal", "Open")
    assert a["PLS_FB_Due_Date__c"] == ("relative_date", 3)
    assert o["fault"] is None


def test_fl05_set_update_effect_typed_with_premise_filters():
    (o,) = _ops("PLS_FB_FL05_Cancellation_Sync")
    assert o["kind"] == "update_records"
    assert o["object"] == "PLS_FB_Fulfilment_Task__c"
    assert ("PLS_FB_Order__c", "EqualTo", ("$Record", "Id")) in o["filters"]
    assert ("PLS_FB_Status__c", "EqualTo", "Open") in o["filters"]
    assert o["assignments"]["PLS_FB_Status__c"] == ("literal", "Cancelled")


def test_fl13_create_carries_the_fault_hook():
    ops = _ops("PLS_FB_FL13_Fault_Logged_Ledger")
    o = next(x for x in ops if x["object"] == "PLS_FB_Ledger_Entry__c")
    assert o["fault"] == "Create_Fault_Log"       # the CP5 hook
    assert o["assignments"]["PLS_FB_Order__c"] == ("subject_ref", "Id")


def test_fl07_parent_update_vars_resolve_to_aggregates():
    (o,) = _ops("PLS_FB_FL07_Order_Rollup")
    assert o["kind"] == "update_records"
    assert o["assignments"]["PLS_FB_Order_Total__c"] == ("var", "varTotal")
    # the var names tie to the CP2 collection aggregates
    from primeqa.semantic.entity_attributes import flow_collection_aggregates
    with open(os.path.join(FIXTURE_DIR,
                           "PLS_FB_FL07_Order_Rollup.json")) as f:
        d = json.load(f)
    (c,) = flow_collection_aggregates({"Metadata": d["Metadata"]})
    assert {a["into"] for a in c["aggregates"]} == {"varTotal", "varCount"}


def test_effect_ops_never_ground_behaviours():
    for name in ("PLS_FB_FL04_Confirmation_Task",
                 "PLS_FB_FL05_Cancellation_Sync",
                 "PLS_FB_FL13_Fault_Logged_Ledger"):
        from primeqa.semantic.entity_attributes import flow_behaviour
        with open(os.path.join(FIXTURE_DIR, f"{name}.json")) as f:
            d = json.load(f)
        ir = flow_behaviour({"Metadata": d["Metadata"]})
        assert all(b["state"] != "grounded" for b in ir["behaviours"]), name


# ── Wave 3 (CP5): fault-path reasoning ───────────────────────────────

def test_fl13_fault_path_is_observable_with_provenance():
    from primeqa.semantic.entity_attributes import flow_fault_paths
    with open(os.path.join(FIXTURE_DIR,
                           "PLS_FB_FL13_Fault_Logged_Ledger.json")) as f:
        d = json.load(f)
    attrs = {"Metadata": d["Metadata"]}
    (fp,) = flow_fault_paths(attrs)
    assert fp == {"of": "Create_Ledger_Entry", "handler": "Create_Fault_Log",
                  "observable": True, "rollback_scope": "flow_transaction"}
    ops = _ops("PLS_FB_FL13_Fault_Logged_Ledger")
    handler = next(o for o in ops if o["element"] == "Create_Fault_Log")
    assert handler["on_fault_of"] == "Create_Ledger_Entry"
    assert handler["assignments"]["PLS_FB_Detail__c"] == ("fault_message",)
    assert handler["fault"] is None      # one bounded hop, no chains


def test_unobservable_fault_path_is_explicit():
    from primeqa.semantic.entity_attributes import flow_fault_paths
    attrs = {"Metadata": {
        "start": {"object": "X__c", "triggerType": "RecordAfterSave",
                  "recordTriggerType": "Create",
                  "connector": {"targetReference": "Make"}},
        "recordCreates": [{"name": "Make", "object": "Y__c",
                           "inputAssignments": [
                               {"field": "A__c",
                                "value": {"stringValue": "v"}}],
                           "faultConnector":
                               {"targetReference": "Notify"}}],
        "actionCalls": [{"name": "Notify", "actionType": "emailSimple"}],
    }}
    (fp,) = flow_fault_paths(attrs)
    assert fp["observable"] is False
    assert "fault_handler_outside_grammar" in fp["reason"]
    assert fp["rollback_scope"] == "flow_transaction"


# ── Wave 3 (CP6): subflow architecture ───────────────────────────────

def _fl12_and_sf01():
    with open(os.path.join(FIXTURE_DIR,
                           "PLS_FB_FL12_Fulfilment_Orchestrator.json")) as f:
        fl12 = json.load(f)
    with open(os.path.join(FIXTURE_DIR,
                           "PLS_FB_SF01_Close_Tasks.json")) as f:
        sf01 = json.load(f)
    caller = {"Metadata": fl12["Metadata"],
              "_developer_name": "PLS_FB_FL12_Fulfilment_Orchestrator"}
    return caller, {"PLS_FB_SF01_Close_Tasks": {"Metadata": sf01["Metadata"]}}


def test_subflow_call_captured_with_typed_inputs():
    from primeqa.semantic.entity_attributes import flow_subflow_calls
    caller, _ = _fl12_and_sf01()
    (c,) = flow_subflow_calls(caller)
    assert c["flow_name"] == "PLS_FB_SF01_Close_Tasks"
    assert c["inputs"] == {"OrderId": ("subject_ref", "Id")}
    assert c["element"] == "Call_Close_Tasks"


def test_compose_subflow_substitutes_the_caller_frame():
    from primeqa.semantic.entity_attributes import compose_subflow
    caller, callees = _fl12_and_sf01()
    (e,) = compose_subflow(caller, callees)
    assert e["refusal"] is None
    (p,) = e["premises"]
    # SF01's OrderId-filtered lookup becomes a $Record.Id correlation in
    # the CALLER's frame, with the caller's entry guard carried
    assert ("PLS_FB_Order__c", "EqualTo", ("$Record", "Id")) in \
        tuple(tuple(f) if not isinstance(f, tuple) else f
              for f in map(tuple, p["filters"]))
    assert p["via_subflow"] == "PLS_FB_SF01_Close_Tasks"
    # SF01's collection-input update stays honestly unrepresented
    assert e["effect_ops"] == ()


def test_compose_subflow_cycle_depth_and_availability_refuse_by_name():
    from primeqa.semantic.entity_attributes import compose_subflow
    caller, callees = _fl12_and_sf01()
    (e,) = compose_subflow(caller, {})
    assert e["refusal"] == "callee_metadata_unavailable"
    # a callee with its own subflow calls exceeds depth 1
    deep = {"PLS_FB_SF01_Close_Tasks": {"Metadata": {
        "start": {"connector": {"targetReference": "S"}},
        "subflows": [{"name": "S", "flowName": "Another"}]}}}
    (e,) = compose_subflow(caller, deep)
    assert e["refusal"] == "subflow_depth_exceeded"
    # a callee calling BACK is a cycle
    cyc = {"PLS_FB_SF01_Close_Tasks": {"Metadata": {
        "start": {"connector": {"targetReference": "S"}},
        "subflows": [{"name": "S",
                      "flowName":
                          "PLS_FB_FL12_Fulfilment_Orchestrator"}]}}}
    (e,) = compose_subflow(caller, cyc)
    assert e["refusal"] == "subflow_cycle"


# ── Wave 3 (CP7): scheduled + async temporal paths ───────────────────

def test_fl11_async_path_effect_is_bounded_eventual():
    from primeqa.semantic.entity_attributes import flow_behaviour
    with open(os.path.join(FIXTURE_DIR,
                           "PLS_FB_FL11_Async_Enrichment.json")) as f:
        d = json.load(f)
    attrs = {"Metadata": d["Metadata"]}
    ir = flow_behaviour(attrs)
    (tp,) = ir["trigger"]["temporal_paths"]
    assert tp["kind"] == "async_after_commit"
    assert tp["observability"] == "bounded_eventual"
    (o,) = _ops("PLS_FB_FL11_Async_Enrichment")
    assert o["kind"] == "create_record"
    assert o["object"] == "PLS_FB_Audit_Log__c"
    assert o["observability"] == "bounded_eventual"
    assert o["temporal_path"] == "async"


def test_fl10_scheduled_offset_is_typed_and_deferred():
    from primeqa.semantic.entity_attributes import flow_behaviour
    with open(os.path.join(
            FIXTURE_DIR, "PLS_FB_FL10_Stale_Order_Escalation.json")) as f:
        d = json.load(f)
    ir = flow_behaviour({"Metadata": d["Metadata"]})
    (tp,) = ir["trigger"]["temporal_paths"]
    assert tp == {"name": "Two_Days_After_Submitted",
                  "connector": "Get_Current_Order",
                  "kind": "scheduled_offset", "offset_number": 2,
                  "offset_unit": "Days",
                  "time_source": "RecordTriggerEvent",
                  "observability": "deferred_reobservation_required"}
    # its path body (lookup → nested decision) stays honestly demoted —
    # no fabricated observability, no captured ops
    assert _ops("PLS_FB_FL10_Stale_Order_Escalation") == ()
    assert all(b["state"] != "grounded" for b in ir["behaviours"])

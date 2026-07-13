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

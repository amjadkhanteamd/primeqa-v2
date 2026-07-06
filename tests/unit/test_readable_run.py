"""Unit: the readable run RESULT skeleton (pure, deterministic, no DB).

``build_readable_run`` mirrors ``build_readable_body`` on the run side: from
one run's captured evidence + the claim's assertion it derives TEST DATA (as
staged, semantic/padding split), value-bearing step narrations, expected vs
actual (matched from the assert step's recorded ``held`` — never re-judged),
and a deterministic narrative paragraph. Never raises; an errored run gets the
minimal skeleton.
"""
from __future__ import annotations

from primeqa.intelligence.readable_run import (
    build_readable_run,
    to_display_dict,
)

LABELS = {
    "Opportunity": "Opportunity",
    "Opportunity.Loan_Amount__c": "Loan Amount",
    "Opportunity.Property_Value__c": "Property Value",
    "Opportunity.Loan_to_Value__c": "Loan-to-Value (%)",
    "Opportunity.Amount": "Amount",
}

# The live LTV shape (run d3de7f7c): automation-effect, staged inputs, read-back.
LTV_ASSERTED = {
    "expected_effect": {"kind": "field_change", "changes": {
        "field_values": {"Opportunity.Loan_to_Value__c": {
            "kind": "literal", "value": "50"}}}}}


def _ltv_steps(*, ltv=50.0, held=True):
    return [
        {"kind": "create", "sobject": "Opportunity", "success": True,
         "matched": None,
         "field_values": {"Loan_Amount__c": 5000000,
                          "Property_Value__c": 10000000,
                          "Name": "PQA", "StageName": "Needs Analysis"}},
        {"kind": "read", "sobject": "Opportunity", "soql": "SELECT ...",
         "row_count": 1, "fields_captured": ["Id", "Loan_to_Value__c"],
         "rows": [{"Id": "006x", "Loan_to_Value__c": ltv}]},
        {"kind": "assert", "predicate": "equals", "held": held},
    ]


def _build(**over):
    kw = dict(
        claim_kind="automation-effect-claim", asserted_truth=LTV_ASSERTED,
        outcome="passed", verdict_plain="Triggered the automation — it produced "
                                        "the expected result",
        steps=_ltv_steps(),
        semantic_field_keys=["Loan_Amount__c", "Property_Value__c"],
        labels=LABELS)
    kw.update(over)
    return build_readable_run(**kw)


# ---------------------------------------------------------------------------
# The passed LTV shape — the motivating render
# ---------------------------------------------------------------------------

def test_ltv_passed_full_shape():
    s = _build()
    d = to_display_dict(s)
    assert d["test_data"] == [["Loan Amount", "5,000,000"],
                              ["Property Value", "10,000,000"]]
    assert ["Name", "PQA"] in d["supporting_data"]
    narrations = [st["narration"] for st in d["steps"]]
    assert narrations[0] == ("Created an Opportunity with Loan Amount = "
                             "5,000,000, Property Value = 10,000,000")
    assert narrations[1] == "Read the record back — Loan-to-Value (%) = 50"
    assert narrations[2] == "Checked the result: expected 50 — matched"
    assert d["expected"] == "Loan-to-Value (%) is 50"
    assert d["result_sentence"] == "expected 50 — matched"
    assert "Loan Amount = 5,000,000" in d["narrative"]
    assert d["narrative"].endswith(
        "Triggered the automation — it produced the expected result.")
    assert d["version_caveat"] is None


def test_ltv_failed_shows_expected_vs_actual():
    s = _build(steps=_ltv_steps(ltv=62.5, held=False), outcome="failed",
               verdict_plain="Triggered the automation — but the expected "
                             "result never appeared")
    d = to_display_dict(s)
    assert d["result_sentence"] == "expected 50, got 62.5 — did not match"
    assert "Checked the result: expected 50, got 62.5 — did not match" in \
        [st["narration"] for st in d["steps"]]


def test_matched_comes_from_held_never_recomputed():
    # The read-back VALUE says 50 but the recorded assert says held=False —
    # the card must report the run's own judgment, never re-compare strings.
    s = _build(steps=_ltv_steps(ltv=50.0, held=False), outcome="failed")
    assert "did not match" in to_display_dict(s)["result_sentence"]


# ---------------------------------------------------------------------------
# Degradations
# ---------------------------------------------------------------------------

def test_errored_run_is_minimal():
    s = _build(outcome="errored")
    d = to_display_dict(s)
    assert d["narrative"] is None and d["test_data"] == []
    assert d["steps"] == [] and d["expected"] is None


def test_no_steps_is_minimal():
    d = to_display_dict(_build(steps=[]))
    assert d["narrative"] is None and d["steps"] == []


def test_garbage_never_raises():
    s = build_readable_run(
        claim_kind=None, asserted_truth="junk", outcome="passed",
        verdict_plain=None,
        steps=[{"kind": "create", "field_values": "notdict"}, "x", 42, None,
               {"no": "kind"}],
        semantic_field_keys=None, labels=None)
    assert to_display_dict(s)["outcome"] == "passed"


def test_padding_split_fallback_without_recipe_keys():
    # No recipe read: the binding field isn't staged → no split → everything
    # shows in test_data (honest, never hidden).
    d = to_display_dict(_build(semantic_field_keys=None))
    staged_labels = {p[0] for p in d["test_data"]}
    assert {"Loan Amount", "Property Value"} <= staged_labels
    assert d["supporting_data"] == []


def test_version_caveat_renders_when_drifted():
    d = to_display_dict(_build(version_drift=True))
    assert "edited since this run" in d["version_caveat"]


# ---------------------------------------------------------------------------
# Other kinds fall back to established step lines
# ---------------------------------------------------------------------------

def test_prohibition_run_uses_step_plain_lines():
    steps = [{"kind": "create", "sobject": "Opportunity", "success": False,
              "matched": True, "error_code": "FIELD_CUSTOM_VALIDATION_EXCEPTION",
              "field_values": {"Amount": 20000}}]
    s = build_readable_run(
        claim_kind="prohibition-claim", asserted_truth={"operation": "create"},
        outcome="passed", verdict_plain="Tried the forbidden change — "
                                        "Salesforce blocked it",
        steps=steps, semantic_field_keys=["Amount"], labels=LABELS)
    d = to_display_dict(s)
    # no field binding → no expected/actual section; the step line is the
    # established blocked-mutation sentence
    assert d["expected"] is None
    assert any("rejected" in st["narration"].lower() or
               "blocked" in st["narration"].lower() for st in d["steps"])


def test_absence_claim_result_semantics():
    steps = [
        {"kind": "create", "sobject": "Opportunity", "success": True,
         "matched": None, "field_values": {"Amount": 500}},
        {"kind": "read", "sobject": "Task", "soql": "S", "row_count": 0,
         "fields_captured": ["Id"], "rows": []},
        {"kind": "assert", "predicate": "not_exists", "held": True},
    ]
    s = build_readable_run(
        claim_kind="automation-effect-claim",
        asserted_truth={"expected_absence": True},
        outcome="passed", verdict_plain=None, steps=steps,
        semantic_field_keys=["Amount"], labels=LABELS)
    d = to_display_dict(s)
    assert d["expected"] == "no follow-up record appears"
    assert d["result_sentence"] == "no record appeared — as expected"


# ---------------------------------------------------------------------------
# Determinism, hashing, grounding
# ---------------------------------------------------------------------------

def test_deterministic_and_hash_moves_on_fact_change():
    a, b = _build(), _build()
    assert a.skeleton_content_hash == b.skeleton_content_hash
    assert to_display_dict(a) == to_display_dict(b)
    c = _build(steps=_ltv_steps(ltv=62.5, held=False), outcome="failed")
    assert c.skeleton_content_hash != a.skeleton_content_hash


def test_grounded_tokens_cover_every_rendered_value():
    s = _build()
    for needle in ("5000000", "10000000", "50", "loan amount",
                   "property value"):
        assert needle in s.grounded_tokens, needle

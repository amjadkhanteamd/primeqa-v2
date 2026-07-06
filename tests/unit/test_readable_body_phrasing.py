"""Unit: Stage-2 readable-body phrasing — the grounding validator, the fail-loud
Stage-1 fallback, and the content-addressed cache. The LLM is stubbed; no gateway
call, no DB.
"""
from __future__ import annotations

import types

import pytest

import primeqa.intelligence.readable_body_phrasing as rbp
from primeqa.intelligence.llm import LLMError
from primeqa.intelligence.readable_body import build_readable_body

LABELS = {"Loan__c": "Loan", "Loan__c.Credit_Score__c": "Credit Score",
          "Loan__c.Risk_Rating__c": "Risk Rating"}


def _ref(api):
    return {"external_id": api}


def _skeleton():
    """An automation-effect skeleton grounding 'Credit Score', '649', 'Risk
    Rating', 'High', 'Loan'."""
    asserted = {
        "kind": "automation-effect-claim", "automation": _ref("HL_Auto_Risk_Rating"),
        "automation_primitive": "flow",
        "triggering_action": {"trigger_kind": "data-mutation-trigger",
                              "description": "internal ref 999"},
        "expected_effect": {"kind": "field_change", "changes": {
            "field_values": {"Loan__c.Risk_Rating__c": {"kind": "literal",
                                                        "value": "High"}}}},
        "affected_fields": [_ref("Loan__c.Risk_Rating__c")]}
    recipe = {"recipe_id": "r1", "recipe_kind": "data-recipe",
              "observation_realization": {"kind": "data-recipe", "steps": [
                  {"kind": "create", "step_id": "c", "target_object": _ref("Loan__c"),
                   "field_values": {"Loan__c.Credit_Score__c": 649}},
                  {"kind": "read", "step_id": "rd", "target": _ref("Loan__c")}]}}
    return build_readable_body(
        claim_kind="automation-effect-claim", archetype="data_behavior",
        asserted_truth=asserted, semantic_conditions=None, recipes=[recipe],
        strategy_kind=None, data_recipe_ids=[], labels=LABELS)


class _Resp:
    def __init__(self, parsed):
        self.parsed_content = parsed
        self.raw_text = ""
        self.model = "claude-haiku-4-5-20251001"
        self.prompt_version = "readable_body_phrasing@v1"


def _stub(monkeypatch, parsed, counter=None):
    def fake(**kwargs):
        if counter is not None:
            counter["n"] += 1
        return _Resp(parsed)
    monkeypatch.setattr(rbp, "llm_call", fake)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    rbp.cache_clear()
    monkeypatch.delenv("PLIMSOL_READABLE_BODY_PHRASING", raising=False)
    yield
    rbp.cache_clear()


# ---------------------------------------------------------------------------
# Grounding validator
# ---------------------------------------------------------------------------

def test_clean_phrasing_passes(monkeypatch):
    parsed = {"plain_terms": "The Loan's Risk Rating becomes High when the Credit "
                             "Score is 649.",
              "step_narration": ["Create a Loan with Credit Score 649.",
                                 "Read the Loan back."]}
    _stub(monkeypatch, parsed)
    out = rbp.ReadableBodyPhrasingEnricher(tenant_id=1, api_key="k").phrase(_skeleton())
    assert out is not None
    assert out["plain_terms"].startswith("The Loan's Risk Rating")
    assert out["model"] == "claude-haiku-4-5-20251001"
    assert out["prompt_version"] == "readable_body_phrasing@v1"


def test_fabricated_number_is_rejected(monkeypatch):
    # 650 is NOT grounded (the skeleton grounds 649) → reject → Stage-1 fallback.
    parsed = {"plain_terms": "The Risk Rating becomes High when the Credit Score "
                             "is 650.",
              "step_narration": []}
    _stub(monkeypatch, parsed)
    assert rbp.ReadableBodyPhrasingEnricher(tenant_id=1, api_key="k").phrase(_skeleton()) is None


def test_fabricated_label_is_rejected(monkeypatch):
    parsed = {"plain_terms": "The Debt Ratio must stay under the limit.",
              "step_narration": []}
    _stub(monkeypatch, parsed)
    assert rbp.ReadableBodyPhrasingEnricher(tenant_id=1, api_key="k").phrase(_skeleton()) is None


def test_fabricated_number_in_a_step_is_rejected(monkeypatch):
    parsed = {"plain_terms": "The Risk Rating becomes High.",
              "step_narration": ["Create a Loan with Credit Score 650."]}   # 650 fabricated
    _stub(monkeypatch, parsed)
    assert rbp.ReadableBodyPhrasingEnricher(tenant_id=1, api_key="k").phrase(_skeleton()) is None


def test_common_word_label_does_not_false_positive(monkeypatch):
    # A sentence starting with "The Loan ..." must not be read as a fabricated
    # "The Loan" label; "Risk Rating"/"Credit Score" are grounded.
    parsed = {"plain_terms": "The Loan is created and Salesforce sets the Risk "
                             "Rating to High for a Credit Score of 649.",
              "step_narration": []}
    _stub(monkeypatch, parsed)
    assert rbp.ReadableBodyPhrasingEnricher(tenant_id=1, api_key="k").phrase(_skeleton()) is not None


# ---------------------------------------------------------------------------
# Malformed shapes
# ---------------------------------------------------------------------------

def test_non_dict_parsed_is_rejected(monkeypatch):
    _stub(monkeypatch, "not a dict")
    assert rbp.ReadableBodyPhrasingEnricher(tenant_id=1, api_key="k").phrase(_skeleton()) is None


def test_missing_plain_terms_is_rejected(monkeypatch):
    _stub(monkeypatch, {"step_narration": []})
    assert rbp.ReadableBodyPhrasingEnricher(tenant_id=1, api_key="k").phrase(_skeleton()) is None


def test_step_narration_not_a_list_is_rejected(monkeypatch):
    _stub(monkeypatch, {"plain_terms": "The Risk Rating becomes High.",
                        "step_narration": "nope"})
    assert rbp.ReadableBodyPhrasingEnricher(tenant_id=1, api_key="k").phrase(_skeleton()) is None


def test_llm_error_returns_none(monkeypatch):
    def boom(**kwargs):
        raise LLMError("rate_limited")
    monkeypatch.setattr(rbp, "llm_call", boom)
    assert rbp.ReadableBodyPhrasingEnricher(tenant_id=1, api_key="k").phrase(_skeleton()) is None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_cache_hit_avoids_second_llm_call(monkeypatch):
    counter = {"n": 0}
    parsed = {"plain_terms": "The Risk Rating becomes High for a Credit Score of "
                             "649.", "step_narration": []}
    _stub(monkeypatch, parsed, counter)
    skel = _skeleton()
    a = rbp.get_or_phrase(skel, tenant_id=1, api_key="k")
    b = rbp.get_or_phrase(skel, tenant_id=1, api_key="k")
    assert a is not None and a == b
    assert counter["n"] == 1                       # second call served from cache


def test_cache_busts_on_a_skeleton_fact_change(monkeypatch):
    counter = {"n": 0}
    parsed = {"plain_terms": "The Risk Rating becomes High for a Credit Score of "
                             "649.", "step_narration": []}
    _stub(monkeypatch, parsed, counter)
    rbp.get_or_phrase(_skeleton(), tenant_id=1, api_key="k")
    # A different skeleton (different fact → different content hash) misses.
    other_asserted = {"kind": "value-claim",
                      "subject": {"external_id": "Loan__c.Credit_Score__c"},
                      "expected_value": {"kind": "literal", "value": 720}}
    other = build_readable_body(
        claim_kind="value-claim", archetype="data_behavior",
        asserted_truth=other_asserted, semantic_conditions=None, recipes=[],
        strategy_kind=None, data_recipe_ids=[], labels=LABELS)
    rbp.get_or_phrase(other, tenant_id=1, api_key="k")
    assert counter["n"] == 2


def test_rejected_phrasing_caches_nothing(monkeypatch):
    counter = {"n": 0}
    parsed = {"plain_terms": "The Credit Score of 650 is used.",   # fabricated 650
              "step_narration": []}
    _stub(monkeypatch, parsed, counter)
    skel = _skeleton()
    assert rbp.get_or_phrase(skel, tenant_id=1, api_key="k") is None
    assert rbp.get_or_phrase(skel, tenant_id=1, api_key="k") is None
    assert counter["n"] == 2                       # not cached → re-invoked


# ---------------------------------------------------------------------------
# Flag gate (schema-free, default OFF)
# ---------------------------------------------------------------------------

def test_flag_default_off():
    assert rbp.readable_body_phrasing_enabled() is False


def test_flag_on_via_env(monkeypatch):
    monkeypatch.setenv("PLIMSOL_READABLE_BODY_PHRASING", "true")
    assert rbp.readable_body_phrasing_enabled() is True
    monkeypatch.setenv("PLIMSOL_READABLE_BODY_PHRASING", "0")
    assert rbp.readable_body_phrasing_enabled() is False


# ---------------------------------------------------------------------------
# Run phrasing (get_or_phrase_run) — same cache, same validator, run prompt
# ---------------------------------------------------------------------------

def _run_skeleton(**over):
    from primeqa.intelligence.readable_run import build_readable_run
    kw = dict(
        claim_kind="automation-effect-claim",
        asserted_truth={"expected_effect": {"kind": "field_change", "changes": {
            "field_values": {"Loan__c.Risk_Rating__c": {
                "kind": "literal", "value": "High"}}}}},
        outcome="passed",
        verdict_plain="Triggered the automation — it produced the expected result",
        steps=[
            {"kind": "create", "sobject": "Loan__c", "success": True,
             "matched": None, "field_values": {"Credit_Score__c": 649}},
            {"kind": "read", "sobject": "Loan__c", "soql": "S", "row_count": 1,
             "fields_captured": ["Id", "Risk_Rating__c"],
             "rows": [{"Risk_Rating__c": "High"}]},
            {"kind": "assert", "predicate": "equals", "held": True}],
        semantic_field_keys=["Credit_Score__c"], labels=LABELS)
    kw.update(over)
    return build_readable_run(**kw)


def test_run_phrasing_clean_pass(monkeypatch):
    parsed = {"plain_terms": "The run created a Loan with a Credit Score of 649. "
                             "The Risk Rating came back High — as expected.",
              "step_narration": ["Created the Loan with Credit Score 649.",
                                 "Read the Risk Rating back — High.",
                                 "The result matched."]}
    _stub(monkeypatch, parsed)
    out = rbp.get_or_phrase_run(_run_skeleton(), tenant_id=1, api_key="k")
    assert out is not None and out["plain_terms"].startswith("The run created")


def test_run_phrasing_fabricated_value_rejected(monkeypatch):
    # 651 was never recorded by the run → reject → deterministic fallback.
    parsed = {"plain_terms": "The run used a Credit Score of 651.",
              "step_narration": []}
    _stub(monkeypatch, parsed)
    assert rbp.get_or_phrase_run(_run_skeleton(), tenant_id=1, api_key="k") is None


def test_run_phrasing_cache_hits_and_is_distinct_from_body_cache(monkeypatch):
    counter = {"n": 0}
    parsed = {"plain_terms": "The Risk Rating came back High for a Credit Score "
                             "of 649.", "step_narration": []}
    _stub(monkeypatch, parsed, counter)
    skel = _run_skeleton()
    a = rbp.get_or_phrase_run(skel, tenant_id=1, api_key="k")
    b = rbp.get_or_phrase_run(skel, tenant_id=1, api_key="k")
    assert a == b and counter["n"] == 1            # second view = cache hit
    # a changed recorded fact → different hash → a fresh phrasing call
    other = _run_skeleton(outcome="failed", steps=[
        {"kind": "create", "sobject": "Loan__c", "success": True,
         "matched": None, "field_values": {"Credit_Score__c": 649}},
        {"kind": "read", "sobject": "Loan__c", "soql": "S", "row_count": 1,
         "fields_captured": ["Id", "Risk_Rating__c"],
         "rows": [{"Risk_Rating__c": "Low"}]},
        {"kind": "assert", "predicate": "equals", "held": False}])
    rbp.get_or_phrase_run(other, tenant_id=1, api_key="k")
    assert counter["n"] == 2

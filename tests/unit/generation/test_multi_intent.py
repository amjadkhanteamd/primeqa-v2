"""D-207 — multi-intent propose surface (Layer A + normalization).

One propose call may carry ``intent_descriptors`` (1..MAX_INTENTS flat items,
each with its own verbatim ``requirement_excerpt``). Layer A enforces
exactly-one-form (array XOR legacy singular); ``normalize_propose_input``
flattens either form to the legacy per-intent shape every downstream resolver
speaks.
"""
from __future__ import annotations

from primeqa.generation.tools import (
    MAX_INTENTS,
    TOOL_PROPOSE,
    normalize_propose_input,
    validate_layer_a,
)


def _item(api="Case", kind="prohibition-claim", polarity="negative",
          excerpt="the org must reject it"):
    return {
        "requirement_excerpt": excerpt,
        "archetype_hint": "data_behavior",
        "polarity_hint": polarity,
        "claim_kind_hint": kind,
        "target_subject_hint": {"entity_type": "Object", "sf_api_name": api},
    }


def _legacy(api="Case"):
    return {
        "requirement_excerpt": "the org must reject it",
        "intent_descriptor": {
            "archetype_hint": "data_behavior", "polarity_hint": "negative",
            "claim_kind_hint": "prohibition-claim",
            "target_subject_hint": {"entity_type": "Object", "sf_api_name": api},
        },
    }


# ---------------------------------------------------------------------------
# Layer A — array form
# ---------------------------------------------------------------------------

def test_array_form_valid():
    res = validate_layer_a(TOOL_PROPOSE, {"intent_descriptors": [_item(), _item("Lead")]})
    assert res.ok, res.errors


def test_array_and_legacy_together_rejected():
    res = validate_layer_a(TOOL_PROPOSE, {
        "intent_descriptors": [_item()], "intent_descriptor": _legacy()["intent_descriptor"]})
    assert not res.ok
    assert "not both" in res.feedback


def test_neither_form_rejected():
    res = validate_layer_a(TOOL_PROPOSE, {})
    assert not res.ok
    assert "intent_descriptors is required" in res.feedback


def test_array_over_cap_rejected():
    res = validate_layer_a(TOOL_PROPOSE, {
        "intent_descriptors": [_item() for _ in range(MAX_INTENTS + 1)]})
    assert not res.ok
    assert str(MAX_INTENTS) in res.feedback


# ---------------------------------------------------------------------------
# D-313 — intent_descriptors is mandatory; the AC-only shape is rejected with a
# skeleton echo; a large multi-AC array (req-302's 10 ACs) fits under the cap.
# ---------------------------------------------------------------------------

def test_acceptance_criteria_only_rejected_with_skeleton():
    # The exact req-302 failure shape: acceptance_criteria present, no
    # intent_descriptors. Rejected, and the feedback echoes the required skeleton
    # so the model can self-correct within the correction budget.
    res = validate_layer_a(TOOL_PROPOSE, {"acceptance_criteria": [
        {"index": i, "label": f"AC{i}"} for i in range(1, 11)]})
    assert not res.ok
    assert "intent_descriptors is required" in res.feedback      # lead phrase kept
    assert "acceptance_criteria alone is NOT a proposal" in res.feedback
    assert "intent_descriptors: [" in res.feedback               # the skeleton


def test_large_multi_ac_array_valid():
    # D-313.1: a 10-AC requirement (req-302) live-decomposes into 22 intents
    # (positive + negative + config per AC); MAX_INTENTS must accept that, else the
    # token fix alone would just move the failure to "22 > cap, rejected".
    assert MAX_INTENTS >= 22
    res = validate_layer_a(TOOL_PROPOSE, {
        "intent_descriptors": [_item() for _ in range(22)]})
    assert res.ok, res.errors


def test_generation_output_ceiling_fits_a_large_proposal():
    # D-313.1: the propose turn's output ceiling must fit acceptance_criteria +
    # ~22 intent_descriptors (~4200 tokens observed on req-302). 2048 truncated it.
    from primeqa.generation.gateway_binding import DEFAULT_MAX_TOKENS
    assert DEFAULT_MAX_TOKENS >= 8192


def test_propose_schema_mandates_via_description_not_top_level_combinator():
    # D-313: the Anthropic tool API rejects a top-level oneOf/allOf/anyOf in
    # input_schema (verified HTTP 400), so the mandatory-intent_descriptors contract
    # is carried by the tool DESCRIPTION, not the schema. Guard both: no top-level
    # combinator, and the description states the contract.
    from primeqa.generation.tools import PROPOSE_SEMANTIC_INTENT_SCHEMA as S
    isch = S["input_schema"]
    assert not (set(isch) & {"anyOf", "oneOf", "allOf"})   # API-incompatible at top level
    desc = S["description"]
    assert "intent_descriptors" in desc
    assert "never call this tool with only `acceptance_criteria`" in desc


def test_array_empty_rejected():
    res = validate_layer_a(TOOL_PROPOSE, {"intent_descriptors": []})
    assert not res.ok


def test_array_item_missing_excerpt_indexed_error():
    bad = _item()
    bad.pop("requirement_excerpt")
    res = validate_layer_a(TOOL_PROPOSE, {"intent_descriptors": [_item(), bad]})
    assert not res.ok
    assert "intent_descriptors[1].requirement_excerpt" in res.feedback


def test_array_item_bad_enum_indexed_error():
    bad = _item()
    bad["polarity_hint"] = "sideways"
    res = validate_layer_a(TOOL_PROPOSE, {"intent_descriptors": [bad]})
    assert not res.ok
    assert "intent_descriptors[0].polarity_hint" in res.feedback


# ---------------------------------------------------------------------------
# Layer A — legacy form unchanged
# ---------------------------------------------------------------------------

def test_legacy_form_still_valid():
    res = validate_layer_a(TOOL_PROPOSE, _legacy())
    assert res.ok, res.errors


def test_legacy_form_missing_descriptor_rejected():
    res = validate_layer_a(TOOL_PROPOSE, {"requirement_excerpt": "x"})
    assert not res.ok
    assert "intent_descriptor is required" in res.feedback


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_normalize_array_to_legacy_shapes():
    out = normalize_propose_input({"intent_descriptors": [_item("Case"), _item("Lead")]})
    assert len(out) == 2
    for per, api in zip(out, ("Case", "Lead")):
        assert per["requirement_excerpt"] == "the org must reject it"
        desc = per["intent_descriptor"]
        assert "requirement_excerpt" not in desc       # lifted out, not duplicated
        assert desc["target_subject_hint"]["sf_api_name"] == api
        assert desc["claim_kind_hint"] == "prohibition-claim"


def test_normalize_legacy_passthrough():
    inp = _legacy()
    out = normalize_propose_input(inp)
    assert out == [inp]


# ---------------------------------------------------------------------------
# D-317 — subject-hint normalization: the LLM names the subject `object` for the
# behavioral claim kinds (prohibition/state-transition/automation-effect/
# acceptance), matching how the rest of target_subject_hint is named; only
# existence-claims used the {entity_type, sf_api_name} entity-ref shape the
# subject-resolvers read. Inject the entity ref from `object` so behavioral
# claims resolve their subject instead of "subject None:None did not resolve".
# ---------------------------------------------------------------------------

def _behavioral_item(obj="Opportunity"):
    # the exact shape the model sends for a prohibition-claim: the object under
    # `object` + claim-kind fields, NO entity_type/sf_api_name.
    return {
        "requirement_excerpt": "Loan Amount is mandatory.",
        "archetype_hint": "data_behavior",
        "polarity_hint": "negative",
        "claim_kind_hint": "prohibition-claim",
        "target_subject_hint": {
            "object": obj, "operation": "modify_record",
            "rejection_conditions": [{"field": f"{obj}.Loan_Amount__c", "operator": "is_null"}],
        },
    }


def test_object_shaped_hint_gets_entity_ref_injected():
    out = normalize_propose_input({"intent_descriptors": [_behavioral_item()]})
    h = out[0]["intent_descriptor"]["target_subject_hint"]
    assert h["entity_type"] == "Object"
    assert h["sf_api_name"] == "Opportunity"
    # non-destructive: the claim-kind fields survive for the downstream resolvers
    assert h["object"] == "Opportunity" and h["operation"] == "modify_record"
    assert h["rejection_conditions"]


def test_explicit_entity_ref_is_not_overwritten():
    it = _item()
    it["target_subject_hint"] = {"entity_type": "Field",
                                 "sf_api_name": "Opportunity.X__c", "object": "IGNORED"}
    out = normalize_propose_input({"intent_descriptors": [it]})
    h = out[0]["intent_descriptor"]["target_subject_hint"]
    assert h["entity_type"] == "Field"                 # explicit ref wins, not "Object"
    assert h["sf_api_name"] == "Opportunity.X__c"


def test_hint_without_object_or_ref_is_left_alone():
    it = _item()
    it["target_subject_hint"] = {"descriptive": "something vague"}
    out = normalize_propose_input({"intent_descriptors": [it]})
    h = out[0]["intent_descriptor"]["target_subject_hint"]
    assert "entity_type" not in h and "sf_api_name" not in h


def test_normalization_does_not_mutate_the_caller_input():
    item = _behavioral_item()
    normalize_propose_input({"intent_descriptors": [item]})
    assert "entity_type" not in item["target_subject_hint"]   # copied before inject


def test_legacy_form_object_hint_is_also_normalized():
    legacy = {"requirement_excerpt": "x", "intent_descriptor": {
        "archetype_hint": "data_behavior", "polarity_hint": "negative",
        "claim_kind_hint": "prohibition-claim",
        "target_subject_hint": {"object": "Lead", "operation": "modify_record"}}}
    out = normalize_propose_input(legacy)
    h = out[0]["intent_descriptor"]["target_subject_hint"]
    assert h["entity_type"] == "Object" and h["sf_api_name"] == "Lead"
    assert "entity_type" not in legacy["intent_descriptor"]["target_subject_hint"]

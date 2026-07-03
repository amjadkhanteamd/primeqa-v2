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
    # D-313: MAX_INTENTS raised so a 10-AC requirement (req-302) proposes one
    # intent per AC in a single call rather than being capped mid-coverage.
    assert MAX_INTENTS >= 10
    res = validate_layer_a(TOOL_PROPOSE, {
        "intent_descriptors": [_item() for _ in range(10)]})
    assert res.ok, res.errors


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

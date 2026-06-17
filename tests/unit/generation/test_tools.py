"""Tool-surface unit tests: the grounding-free Layer-A validator + a guard
that the bound vocabularies match their locked sources (no drift)."""
from __future__ import annotations

from primeqa.generation import tools as T
from primeqa.generation.enums import (
    AdmissibilityLayer,
    DismissalReason,
    OutcomeKind,
    RefusalKind,
)

from .conftest import VALID_EMIT_DRAFT, VALID_PROPOSE, VALID_SELECT


# ---------------------------------------------------------------------------
# validate_layer_a — happy
# ---------------------------------------------------------------------------

def test_layer_a_accepts_valid_calls():
    assert T.validate_layer_a(T.TOOL_PROPOSE, VALID_PROPOSE).ok
    assert T.validate_layer_a(T.TOOL_SELECT, VALID_SELECT).ok
    assert T.validate_layer_a(T.TOOL_EMIT, VALID_EMIT_DRAFT).ok


def test_layer_a_accepts_emit_refusal():
    inp = {"outcome_kind": "refusal",
           "payload": {"refusal_kind": "no-relevant-context", "refusal_payload": {}}}
    assert T.validate_layer_a(T.TOOL_EMIT, inp).ok


# ---------------------------------------------------------------------------
# validate_layer_a — violations (all operational by construction)
# ---------------------------------------------------------------------------

def test_layer_a_rejects_missing_excerpt():
    inp = {"intent_descriptor": dict(VALID_PROPOSE["intent_descriptor"])}
    res = T.validate_layer_a(T.TOOL_PROPOSE, inp)
    assert not res.ok and "requirement_excerpt" in res.feedback


def test_layer_a_accepts_ac_coverage_fields():
    # D-247: declared AC list + ac_ref-tagged intents + a no_admissible_test refusal.
    inp = {
        "acceptance_criteria": [
            {"index": 1, "label": "rejects no Account"},
            {"index": 2, "label": "SLA object exists"},
        ],
        "intent_descriptors": [
            dict(VALID_PROPOSE["intent_descriptor"],
                 requirement_excerpt="reject without Account", ac_ref=1),
            {"requirement_excerpt": "the SLA object exists",
             "archetype_hint": "configuration",
             "target_subject_hint": {"entity_type": "Object", "sf_api_name": "Case_SLA__c"},
             "polarity_hint": "positive", "ac_ref": 2,
             "no_admissible_test": True, "no_admissible_test_reason": "no SLA config in org"},
        ],
    }
    assert T.validate_layer_a(T.TOOL_PROPOSE, inp).ok


def test_layer_a_ac_coverage_fields_are_optional():
    # Backward compat: a propose with none of the D-247 fields stays valid.
    assert T.validate_layer_a(T.TOOL_PROPOSE, VALID_PROPOSE).ok


def test_layer_a_rejects_bad_ac_ref():
    for bad in ("1", True, 0, -1):
        desc = dict(VALID_PROPOSE["intent_descriptor"], ac_ref=bad)
        res = T.validate_layer_a(T.TOOL_PROPOSE, {"requirement_excerpt": "x", "intent_descriptor": desc})
        assert not res.ok and "ac_ref" in res.feedback


def test_layer_a_rejects_bad_no_admissible_test():
    desc = dict(VALID_PROPOSE["intent_descriptor"], no_admissible_test="yes")
    res = T.validate_layer_a(T.TOOL_PROPOSE, {"requirement_excerpt": "x", "intent_descriptor": desc})
    assert not res.ok and "no_admissible_test" in res.feedback


def test_layer_a_rejects_bad_acceptance_criteria():
    inp = dict(VALID_PROPOSE, acceptance_criteria=[{"index": "one", "label": "x"}])
    res = T.validate_layer_a(T.TOOL_PROPOSE, inp)
    assert not res.ok and "acceptance_criteria" in res.feedback


def test_layer_a_rejects_bad_archetype():
    desc = dict(VALID_PROPOSE["intent_descriptor"], archetype_hint="data-behavior")  # hyphen invalid
    res = T.validate_layer_a(T.TOOL_PROPOSE, {"requirement_excerpt": "x", "intent_descriptor": desc})
    assert not res.ok and "archetype_hint" in res.feedback


def test_layer_a_rejects_bad_polarity():
    desc = dict(VALID_PROPOSE["intent_descriptor"], polarity_hint="maybe")
    res = T.validate_layer_a(T.TOOL_PROPOSE, {"requirement_excerpt": "x", "intent_descriptor": desc})
    assert not res.ok and "polarity_hint" in res.feedback


def test_layer_a_rejects_bad_rationale_kind():
    inp = {"candidate_refs": ["p1"],
           "selection_rationale": {"selected_path_id": "p1", "rationale_kind": "vibes"}}
    res = T.validate_layer_a(T.TOOL_SELECT, inp)
    assert not res.ok and "rationale_kind" in res.feedback


def test_layer_a_rejects_draft_without_admissibility_layer():
    inp = {"outcome_kind": "draft", "payload": {"claim_ref": {}}}
    res = T.validate_layer_a(T.TOOL_EMIT, inp)
    assert not res.ok and "admissibility_layer" in res.feedback


def test_layer_a_rejects_refusal_with_bad_kind():
    inp = {"outcome_kind": "refusal", "payload": {"refusal_kind": "made-up-kind"}}
    res = T.validate_layer_a(T.TOOL_EMIT, inp)
    assert not res.ok and "refusal_kind" in res.feedback


def test_layer_a_rejects_unknown_tool():
    res = T.validate_layer_a("not_a_tool", {})
    assert not res.ok


# ---------------------------------------------------------------------------
# Vocabulary binding — no drift from the locked sources
# ---------------------------------------------------------------------------

def test_slice0_vocabularies_bound_verbatim():
    assert T._OUTCOME_KINDS == [e.value for e in OutcomeKind]
    assert T._REFUSAL_KINDS == [e.value for e in RefusalKind]
    assert T._DISMISSAL_REASONS == [e.value for e in DismissalReason]
    assert T._ADMISSIBILITY_LAYERS == [e.value for e in AdmissibilityLayer]


def test_archetype_uses_s2_underscore_form():
    # D-095.A: bind to S2's actual ARCHETYPE_ENUM value (underscore), not
    # D-087's hyphenated prose spelling.
    assert "data_behavior" in T._ARCHETYPES
    assert "data-behavior" not in T._ARCHETYPES


def test_s2_vocabularies_match_models_db_enums():
    # Drift-guard: the archetype + claim_kind lists mirrored in tools.py must
    # equal S2's actual PG enum values (imported here, not in the runtime
    # module). If S2 evolves these, this test fails loud.
    from primeqa.test_representation.models_db import ARCHETYPE_ENUM, CLAIM_KIND_ENUM
    assert T._ARCHETYPES == list(ARCHETYPE_ENUM.enums)
    assert T._CLAIM_KINDS == list(CLAIM_KIND_ENUM.enums)

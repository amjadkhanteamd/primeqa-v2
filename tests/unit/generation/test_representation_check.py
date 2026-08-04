"""Unit tests for the D-426 representation checks (placeholder leak +
label-vs-Id) and their finalize-time gate.

Mirrors test_value_membership.py's structure: module-level checks over a
direct-constructed index, then the gate wiring through
``GovernanceCore.finalize_outcome`` with a minimal fake S1.
"""
from __future__ import annotations

import pytest

from primeqa.generation.representation_check import (
    FieldTypeIndex,
    RepresentationCheckError,
    Verdict,
)

_OWNER = "Opportunity.OwnerId"
_REASON = "PLS_BM_Deal__c.PLS_BM_Override_Reason__c"
_TIER = "PLS_FB_Order__c.PLS_FB_Tier__c"


def _index():
    return FieldTypeIndex({
        _OWNER: "reference",
        _REASON: "textarea",
        _TIER: "picklist",
        "Case.Mystery__c": None,          # resolved field, type unknown
    })


# ===========================================================================
# Check 1 — placeholder leak
# ===========================================================================

def test_structural_placeholder_invalid_on_known_text_field():
    [c] = _index().check_literal(_REASON, "<computed>")
    assert c.verdict == Verdict.INVALID
    assert c.detail == "placeholder_structural"


def test_structural_placeholder_invalid_even_on_unknown_field():
    # The one deliberate exception (D-426): a bare angle-bracketed token has
    # zero legitimate variants regardless of field knowledge.
    [c] = _index().check_literal("Never.Seen__c", "<derived from submission date>")
    assert c.verdict == Verdict.INVALID
    assert c.detail == "placeholder_structural"


def test_filler_word_invalid_on_known_free_text_field():
    # The 601e0c99 shape: the literal "provided" asserted on a textarea.
    [c] = _index().check_literal(_REASON, "provided")
    assert c.verdict == Verdict.INVALID
    assert c.detail == "placeholder_filler"


def test_filler_word_case_insensitive_full_match_only():
    [c] = _index().check_literal(_REASON, "  TBD ")
    assert c.verdict == Verdict.INVALID
    # A value merely CONTAINING a filler word is not a placeholder.
    assert _index().check_literal(_REASON, "documents provided by client") == []


def test_filler_word_on_unknown_type_cannot_validate_never_invalid():
    # D-399.1: the word could be an org-real enumerated value we cannot
    # see — a knowledge gap must never become a refusal.
    for field in ("Case.Mystery__c", "Never.Seen__c"):
        [c] = _index().check_literal(field, "provided")
        assert c.verdict == Verdict.CANNOT_VALIDATE
        assert c.detail == "unknown_field_type"


def test_enumerated_fields_are_membership_jurisdiction_no_checks():
    # An org could legitimately enumerate "provided"; D-412 membership owns
    # every literal on picklist-typed fields.
    assert _index().check_literal(_TIER, "provided") == []
    assert _index().check_literal(_TIER, "<computed>") == []


# ===========================================================================
# Check 2 — label-vs-Id
# ===========================================================================

def test_label_on_id_field_is_invalid():
    # The 0d81c6f9 shape: OwnerId = "Credit Manager".
    [c] = _index().check_literal(_OWNER, "Credit Manager")
    assert c.verdict == Verdict.INVALID
    assert c.detail == "label_on_id_field"


def test_symbolic_step_ref_is_pinned_valid():
    [c] = _index().check_literal(_OWNER, "$create-subject.id")
    assert c.verdict == Verdict.VALID
    assert c.detail == "symbolic_ref"


def test_id_shaped_values_are_valid_15_and_18():
    for v in ("005F900000ATd9A", "005F900000ATd9AIAT"):
        [c] = _index().check_literal(_OWNER, v)
        assert c.verdict == Verdict.VALID
        assert c.detail == "id_shaped"


def test_unknown_type_never_refuses_label_check():
    # Resolved-but-typeless and unresolved fields both produce NO
    # label-vs-Id INVALID (the filler tier's CANNOT_VALIDATE is the only
    # check a filler word gets; a plain label gets nothing).
    assert _index().check_literal("Case.Mystery__c", "Credit Manager") == []
    assert _index().check_literal("Never.Seen__c", "Credit Manager") == []


# ===========================================================================
# Shared discipline
# ===========================================================================

def test_non_string_literals_are_out_of_scope():
    idx = _index()
    for v in (62.5, 90, True, None,
              {"$relative_date": {"anchor": "RUN_DATE", "offset_days": 3}}):
        assert idx.check_literal(_OWNER, v) == []
        assert idx.check_literal(_REASON, v) == []


def test_rollup_invalid_dominates_then_cannot_validate():
    idx = _index()
    r = idx.validate({"steps": [{"kind": "create", "field_values": {
        _OWNER: "Credit Manager"}}]})
    assert r.verdict == Verdict.INVALID
    r2 = idx.validate({"steps": [{"kind": "create", "field_values": {
        "Never.Seen__c": "tbd"}}]})
    assert r2.verdict == Verdict.CANNOT_VALIDATE
    r3 = idx.validate({"steps": [{"kind": "create", "field_values": {
        _REASON: "a genuine business reason"}}]})
    assert r3.verdict == Verdict.VALID and r3.checks == []


def test_from_s1_wraps_read_errors_fail_loud():
    class _Exploding:
        def get_entities(self, *a, **k):
            raise RuntimeError("connection lost")
    with pytest.raises(RepresentationCheckError, match="connection lost"):
        FieldTypeIndex.from_s1(_Exploding(), 1, ["Opportunity.OwnerId"])


# ===========================================================================
# D-426 — the finalize-time gate (wiring)
# ===========================================================================

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from primeqa.generation.emission import GroundedExistence, _Endpoint
from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind, RefusalKind
from primeqa.generation.governance import ConversationContext, PresentedCandidate
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.protocol import (
    BudgetSpec, GovernanceContext, OperationalContext, SemanticContext)


class _FakeS1:
    """Resolves OwnerId (reference) + Override_Reason (textarea); anything
    else unresolved. Non-picklist types keep the membership gate inert."""

    _TYPES = {_OWNER: "reference", _REASON: "textarea"}

    def get_entities(self, entity_type, at_seq, filters=None):
        name = (filters or {}).get("sf_api_name")
        if name in self._TYPES:
            return [SimpleNamespace(id=name)]
        return []

    def get_entity_details(self, entity_id, at_seq):
        return {"field_type": self._TYPES[entity_id]}

    def get_picklist_values(self, pvs_id, at_seq):  # pragma: no cover
        return []


def _wire_ctx():
    return ConversationContext(
        request_id=uuid4(),
        requirement_ref={"key": "req-w", "text": "w"},
        requirement_text="w",
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "req-w", "text": "w"}],
            s1_version_seq=1, s1_version_name="v1"),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(budgets=BudgetSpec()))


def _wire_state():
    return SimpleNamespace(
        groundings=[GroundedExistence(
            archetype="configuration", claim_kind="existence-claim",
            version_seq=1,
            subject=_Endpoint(entity_id=uuid4(), entity_type="Field",
                              external_id=_OWNER),
            requirement_excerpt="x")],
        presented_candidates=[
            PresentedCandidate("c0", AdmissibilityLayer.LAYER_1)],
        attempted_interpretation={
            "candidate_paths": [{"path_id": "c0"}],
            "dismissed_alternatives_by_reason": {},
            "selected_path_id": None,
        },
        control_facts=None)


def _bundle(field, value):
    return SimpleNamespace(
        archetype="behavioural", claim_kind="automation-effect-claim",
        asserted_truth={"expected_effect": {"changes": {field: value}}},
        semantic_conditions={"kind": "semantic-conditions"},
        causal_initiation={"kind": "data-mutation-trigger"},
        observation_realization={"steps": [
            {"kind": "create", "field_values": {field: value}}]},
        secondary_recipes=(), boundary_recipes=(),
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=False, caveat_kind=None)


def _finalize_with(bundle, s1):
    gov = GovernanceCore(s1)
    with patch("primeqa.generation.governance_core.author_emission",
               return_value=bundle):
        return gov.finalize_outcome(outcome_input={}, ctx=_wire_ctx(),
                                    state=_wire_state())


def test_gate_declines_label_on_id_via_override_when_sole_bundle():
    ov = _finalize_with(_bundle(_OWNER, "Credit Manager"), _FakeS1())
    assert ov.override is not None and ov.outcome is None
    assert ov.override.refusal_kind == RefusalKind.UNGROUNDED_CLAIM
    p = ov.override.payload
    assert p["defer_class"] == "representation"
    [v] = p["violations"]
    assert v["field"] == _OWNER
    assert v["asserted_value"] == "Credit Manager"
    assert v["reason"] == "label_on_id_field"
    assert v["field_type"] == "reference"
    assert "human label" in p["detail"]


def test_gate_declines_placeholder_on_text_field():
    ov = _finalize_with(_bundle(_REASON, "provided"), _FakeS1())
    assert ov.override is not None
    [v] = ov.override.payload["violations"]
    assert v["reason"] == "placeholder_filler"


def test_gate_passes_id_shaped_value_untouched():
    ov = _finalize_with(_bundle(_OWNER, "005F900000ATd9AIAT"), _FakeS1())
    assert ov.override is None
    assert ov.outcome is not None and ov.outcome.outcome_kind == OutcomeKind.DRAFT
    ai = ov.outcome.attempted_interpretation.model_dump()
    assert "partial_refusals" not in ai


def test_gate_cannot_validate_passes_through_unresolved_field():
    # A filler word on a field S1 cannot type: CANNOT_VALIDATE, no decline.
    ov = _finalize_with(_bundle("Never.Seen__c", "provided"), _FakeS1())
    assert ov.override is None and ov.outcome is not None


def test_gate_mixed_batch_keeps_valid_and_declines_invalid():
    gov = GovernanceCore(_FakeS1())
    state = _wire_state()
    state.presented_candidates = [
        PresentedCandidate("c0", AdmissibilityLayer.LAYER_1),
        PresentedCandidate("c1", AdmissibilityLayer.LAYER_1)]
    kept, decls = gov._representation_gate(
        [_bundle(_OWNER, "$create-subject.id"),
         _bundle(_OWNER, "Credit Manager")],
        _wire_ctx(), state)
    assert len(kept) == 1
    [d] = decls
    assert d["path_id"] == "c1"
    assert d["refusal_kind"] == "ungrounded-claim"
    assert d["payload"]["defer_class"] == "representation"
    assert [c.path_id for c in state.presented_candidates] == ["c0"]


def test_gate_fails_loud_when_checker_errors():
    class _ExplodingS1(_FakeS1):
        def get_entities(self, *a, **k):
            raise RuntimeError("connection lost")
    with pytest.raises(Exception, match="connection lost"):
        _finalize_with(_bundle(_OWNER, "Credit Manager"), _ExplodingS1())

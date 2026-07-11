"""Control-telemetry Phase 0 — governance wiring (read-only posture).

The stash accumulates facts on the state (dedup by subject+ref); finalize
attaches ``attempted_interpretation["control_coverage"]`` when facts exist and
attaches NOTHING when they don't (byte-identical pre-telemetry outcomes). The
explanation_hash must be identical either way — the map may never re-key.

DB-free per the test_finalize_dedup precedent: ``GovernanceCore.__init__``
only stores its s1_model reference."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import control_coverage as cc
from primeqa.generation.emission import GroundedExistence, _Endpoint, author_emission
from primeqa.generation.enums import AdmissibilityLayer
from primeqa.generation.governance import ConversationContext, PresentedCandidate
from primeqa.generation.governance_core import GovernanceCore, _stash_control_facts
from primeqa.generation.protocol import (
    BudgetSpec, GovernanceContext, OperationalContext, SemanticContext)

SUBJECT = "PLS_BM_Deal__c"


def _vr_row(name: str, formula: str, message: str, active: bool = True):
    return SimpleNamespace(
        edge_type="APPLIES_TO",
        entity=SimpleNamespace(entity_type="ValidationRule",
                               sf_api_name=f"{SUBJECT}.{name}",
                               attributes={"formula_text": formula,
                                           "error_message": message,
                                           "is_active": active}))


def _subject_entity():
    return SimpleNamespace(entity_type="Object", sf_api_name=SUBJECT)


def _ctx():
    return ConversationContext(
        request_id=uuid4(),
        requirement_ref={"key": "req-t", "text": "t"},
        requirement_text="t",
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "req-t", "text": "t"}],
            s1_version_seq=1, s1_version_name="v1"),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(budgets=BudgetSpec()))


def _grounding():
    return GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Field",
                          external_id="PLS_BM_Deal__c.PLS_BM_Discount__c"),
        requirement_excerpt="x")


def _state(control_facts=None):
    return SimpleNamespace(
        groundings=[_grounding()],
        presented_candidates=[PresentedCandidate("c0", AdmissibilityLayer.LAYER_1)],
        attempted_interpretation={
            "candidate_paths": [{"path_id": "c0"}],
            "dismissed_alternatives_by_reason": {},
            "selected_path_id": None,
        },
        control_facts=control_facts)


# -- the stash ----------------------------------------------------------------

def test_stash_accumulates_and_dedupes():
    state = SimpleNamespace()
    rows = [_vr_row("VR01", "PLS_BM_Deal_Value__c <= 0", "msg1")]
    _stash_control_facts(state, _subject_entity(), rows)
    _stash_control_facts(state, _subject_entity(), rows)          # re-resolve
    _stash_control_facts(state, _subject_entity(),
                         rows + [_vr_row("VR02", "PLS_BM_Discount__c > 0.20",
                                         "msg2")])
    assert [f.control_ref for f in state.control_facts] == [
        f"{SUBJECT}.VR01", f"{SUBJECT}.VR02"]


def test_stash_tolerates_none_state():
    _stash_control_facts(None, _subject_entity(), [])             # must not raise


# -- the finalize attach --------------------------------------------------------

def _facts():
    return cc.controls_from_neighborhood(SUBJECT, [
        _vr_row("VR01", "NOT(ISBLANK(PLS_BM_Deal_Value__c)) && "
                        "PLS_BM_Deal_Value__c <= 0",
                "Deal Value must be greater than zero.")])


def test_finalize_attaches_map_when_facts_stashed():
    gov = GovernanceCore(None)
    ov = gov.finalize_outcome(outcome_input={}, ctx=_ctx(),
                              state=_state(control_facts=_facts()))
    ai = ov.outcome.attempted_interpretation.model_dump()
    cmap = ai["control_coverage"]
    assert cmap["version"] == cc.COVERAGE_VERSION
    assert cmap["counts"]["expected"] == 1
    # the existence bundle carries no rejection pattern -> EXPECTED, not EMITTED
    assert cmap["controls"][f"{SUBJECT}.VR01"]["stage"] == cc.EXPECTED


def test_finalize_without_facts_attaches_nothing():
    gov = GovernanceCore(None)
    ov = gov.finalize_outcome(outcome_input={}, ctx=_ctx(), state=_state())
    ai = ov.outcome.attempted_interpretation.model_dump()
    assert "control_coverage" not in ai


def test_finalize_hash_identical_with_and_without_map():
    gov = GovernanceCore(None)
    with_map = gov.finalize_outcome(outcome_input={}, ctx=_ctx(),
                                    state=_state(control_facts=_facts()))
    without = gov.finalize_outcome(outcome_input={}, ctx=_ctx(), state=_state())
    assert with_map.outcome.explanation_hash == without.outcome.explanation_hash


# -- the real _resolve_one path ---------------------------------------------------

def test_resolve_one_stashes_facts_even_when_the_intent_refuses(monkeypatch):
    """The stash sits at the neighborhood fetch, BEFORE admissibility — a
    refused intent must still contribute its control facts (Expected must not
    depend on emission success). Drives the REAL ``_resolve_one``: only the two
    S1 port reads are stubbed; subject resolution, Layer B, evaluate, and the
    refusal routing all run for real. The VR rides a non-grounding edge so
    evaluate dismisses -> the intent refuses -> the facts are stashed anyway."""
    from primeqa.generation.governance import NextAction

    gov = GovernanceCore(None)
    subject = SimpleNamespace(id=uuid4(), entity_type="Object",
                              sf_api_name=SUBJECT)
    vr_row = _vr_row("VR01", "PLS_BM_Deal_Value__c <= 0",
                     "Deal Value must be greater than zero.")
    vr_row.edge_type = "OBSERVED_ON"        # NOT the prohibition grounding edge
    monkeypatch.setattr(gov._admit, "resolve_subject",
                        lambda et, api, at: [subject])
    monkeypatch.setattr(gov._admit, "scoped_neighborhood",
                        lambda subj, at: [vr_row])
    state = SimpleNamespace(attempted_interpretation={"candidate_paths": []},
                            groundings=[])
    res = gov._resolve_one(
        {"intent_descriptor": {
            "archetype_hint": "data_behavior",
            "claim_kind_hint": "prohibition-claim",
            "polarity_hint": "negative",
            "target_subject_hint": {"entity_type": "Object",
                                    "sf_api_name": SUBJECT}},
         "requirement_excerpt": "Deal values must be commercially valid."},
        _ctx(), state)
    assert res.next_action == NextAction.REFUSE           # the intent refused…
    assert [f.control_ref for f in state.control_facts] == [
        f"{SUBJECT}.VR01"]                                # …the fact stashed anyway

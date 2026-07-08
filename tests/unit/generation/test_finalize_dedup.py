"""D-339 — finalize_outcome deduplicates authored bundles by canonical identity.

The D-247 coverage re-prompt can re-send the FULL intent array, so a later
propose turn re-grounds intents already grounded on an earlier turn. Each
re-grounding accumulates a duplicate entry on BOTH ``state.groundings`` and
``state.presented_candidates`` (1:1 aligned), and ``finalize_outcome`` authors
one bundle per grounding — so without dedup a requirement emits N byte-identical
claims (live outcome ab65fb0c-430a-408d-b0d9-4747988d3b00: 53 accumulated
groundings -> 28 canonical identities).

Fix (Option D): finalize collapses bundles that share the EXISTING canonical
``compute_identity_hash`` — the same fingerprint the persister dedups on — before
persistence, keeping first occurrence and its aligned presented_candidate.

These tests are DB-free: the dedup path in finalize touches neither S1 nor the
persister, and ``GovernanceCore.__init__`` only stores its s1_model reference.
Endpoints use a STABLE entity_id per external_id (uuid5) because entity_id enters
the identity hash — exactly as a real, stable S1 entity would, so an identical
re-grounding hashes identically (the real-bug shape).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from primeqa.generation import governance_core as gc
from primeqa.generation.governance_core import (
    GovernanceCore,
    _dedup_bundles_by_identity,
)
from primeqa.generation.emission import (
    GroundedAcceptance,
    GroundedExistence,
    GroundedNegative,
    _Endpoint,
    author_emission,
)
from primeqa.generation.governance import ConversationContext, PresentedCandidate
from primeqa.generation.enums import AdmissibilityLayer
from primeqa.generation.protocol import (
    BudgetSpec,
    GovernanceContext,
    OperationalContext,
    SemanticContext,
)
from primeqa.test_representation.identity_hash import compute_identity_hash


# ---------------------------------------------------------------------------
# Builders — stable ids so an identical re-grounding hashes identically
# ---------------------------------------------------------------------------

def _ep(entity_type: str, external_id: str) -> _Endpoint:
    # uuid5 => a given external_id ALWAYS maps to the same entity_id (mirrors a
    # stable S1 entity). entity_id rides the identity hash, so this is what makes
    # a re-grounded duplicate hash-identical to its first occurrence.
    return _Endpoint(entity_id=uuid5(NAMESPACE_URL, external_id),
                     entity_type=entity_type, external_id=external_id)


# Raw grounding builders (finalize_outcome authors these) ...
def _g_existence(external_id: str, excerpt: str = "x") -> GroundedExistence:
    return GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=1,
        subject=_ep("Field", external_id), requirement_excerpt=excerpt)


def _g_prohibition(vr_formulas=("Amount < 0",), excerpt: str = "x") -> GroundedNegative:
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint=None, version_seq=1, subject=_ep("Object", "Case"),
        requirement_excerpt=excerpt, vr_formulas=vr_formulas)


def _g_acceptance(value: str) -> GroundedAcceptance:
    return GroundedAcceptance(
        archetype="data_behavior", claim_kind="acceptance-claim", version_seq=1,
        subject=_ep("Object", "Case"), requirement_excerpt="x",
        conditions=(SimpleNamespace(
            field=_ep("Field", "Case.Amount"), predicate="equals", value=value),))


# ... and their authored-bundle counterparts (the pure helper takes bundles).
def _existence(external_id: str, excerpt: str = "x"):
    return author_emission(_g_existence(external_id, excerpt))


def _prohibition(vr_formulas=("Amount < 0",), excerpt: str = "x"):
    return author_emission(_g_prohibition(vr_formulas, excerpt))


def _acceptance(value: str):
    return author_emission(_g_acceptance(value))


def _ih(bundle) -> str:
    return compute_identity_hash(bundle.archetype, bundle.claim_kind,
                                 bundle.asserted_truth, bundle.semantic_conditions)


def _cands(n: int) -> list:
    return [PresentedCandidate(f"c{i}", AdmissibilityLayer.LAYER_1) for i in range(n)]


# ===========================================================================
# Layer 1 — the pure helper (_dedup_bundles_by_identity)
# ===========================================================================

def test_helper_collapses_exact_duplicates_keeping_first():
    # The real-bug shape: an identical grounding re-proposed hashes identically.
    a, b = _existence("Account.Industry"), _existence("Account.Name")
    a_dup = _existence("Account.Industry")               # re-proposal duplicate
    assert _ih(a) == _ih(a_dup) and _ih(a) != _ih(b)     # premise: dup == a, != b
    presented = _cands(3)
    kept_b, kept_p, dropped = _dedup_bundles_by_identity([a, b, a_dup], presented)
    assert dropped == 1
    assert [x.claim_kind for x in kept_b] == ["existence-claim", "existence-claim"]
    assert kept_b == [a, b]                               # first occurrence retained
    assert kept_p == [presented[0], presented[1]]         # aligned candidates retained


def test_helper_different_intent_same_identity_collapses():
    # Case (b): two DIFFERENT intents (different requirement_excerpt) that ground
    # to the SAME canonical claim collapse to one. excerpt is not in the identity.
    b1 = _existence("Account.Industry", excerpt="mandatory industry per AC1")
    b2 = _existence("Account.Industry", excerpt="industry must exist per the table")
    assert _ih(b1) == _ih(b2)                             # same canonical identity
    kept_b, kept_p, dropped = _dedup_bundles_by_identity([b1, b2], _cands(2))
    assert dropped == 1 and len(kept_b) == 1 and kept_b[0] is b1


def test_helper_same_subject_different_conditions_both_survive():
    # Case (c): same Salesforce subject, genuinely different semantic_conditions
    # (Amount == 100 vs == 200) => distinct identities => BOTH survive.
    c1, c2 = _acceptance("100"), _acceptance("200")
    assert _ih(c1) != _ih(c2)
    kept_b, kept_p, dropped = _dedup_bundles_by_identity([c1, c2], _cands(2))
    assert dropped == 0 and kept_b == [c1, c2]


def test_helper_only_new_claims_nothing_removed():
    # Case (d): a re-prompt that adds only genuinely-new claims removes nothing.
    bundles = [_existence("Account.Industry"), _existence("Account.Name"),
               _existence("Account.Rating")]
    kept_b, kept_p, dropped = _dedup_bundles_by_identity(bundles, _cands(3))
    assert dropped == 0 and kept_b == bundles


def test_helper_preserves_first_occurrence_ordering_interleaved():
    # Requirement 4: first-occurrence ordering, even when dups interleave with
    # new claims. Order of retained == order of first sighting.
    a = _existence("Account.Industry")   # c0
    b = _existence("Account.Name")        # c1
    a2 = _existence("Account.Industry")   # c2 dup of a
    d = _existence("Account.Rating")      # c3 new
    b2 = _existence("Account.Name")       # c4 dup of b
    presented = _cands(5)
    kept_b, kept_p, dropped = _dedup_bundles_by_identity(
        [a, b, a2, d, b2], presented)
    assert dropped == 2
    assert kept_b == [a, b, d]                                  # first-seen order
    assert [c.path_id for c in kept_p] == ["c0", "c1", "c3"]    # aligned path_ids


def test_helper_recipe_axis_outside_identity_collapses():
    # Defense-in-depth: two prohibition intents on the same subject that name
    # DIFFERENT VRs (a recipe-only difference — recipes are outside the identity
    # hash, Option-C) are the same canonical claim and collapse to one.
    p1 = _prohibition(vr_formulas=("Amount < 0",))
    p2 = _prohibition(vr_formulas=("Amount < 0", "Status = 'X'"))
    assert _ih(p1) == _ih(p2)
    kept_b, _, dropped = _dedup_bundles_by_identity([p1, p2], _cands(2))
    assert dropped == 1 and kept_b == [p1]


# ===========================================================================
# Layer 2 — the real finalize_outcome path
# ===========================================================================

def _ctx():
    return ConversationContext(
        request_id=uuid4(),
        requirement_ref={"key": "req-302", "text": "Home Loan Qualification"},
        requirement_text="Home Loan Qualification",
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "req-302", "text": "t"}],
            s1_version_seq=7, s1_version_name="v7"),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(budgets=BudgetSpec()))


def _state(groundings, presented):
    # Only the attrs finalize_outcome reads/writes; the runtime RequirementState
    # is a superset. attempted_interpretation mirrors the runtime default shape.
    return SimpleNamespace(
        groundings=list(groundings),
        presented_candidates=list(presented),
        attempted_interpretation={
            "candidate_paths": [{"path_id": c.path_id} for c in presented],
            "dismissed_alternatives_by_reason": {},
            "selected_path_id": None,
        })


def _finalize(groundings, presented, caplog_level=None, caplog=None):
    gov = GovernanceCore(None)   # DB-free: __init__ only stores the s1_model ref
    ctx, state = _ctx(), _state(groundings, presented)
    if caplog is not None and caplog_level is not None:
        caplog.set_level(caplog_level, logger="primeqa.generation.governance_core")
    ov = gov.finalize_outcome(outcome_input={}, ctx=ctx, state=state)
    return ov, state


def test_finalize_reprompt_collapses_dupes_keeps_new(caplog):
    # Case (a): turn-0 grounds [Industry, Name, Type]; the coverage re-prompt
    # RE-SENDS the full array plus one new intent -> accumulated groundings are
    # [Industry, Name, Type, Industry, Name, Type, Rating] with path_ids c0..c6.
    # Dedup -> 4 canonical claims, first-occurrence path_ids [c0, c1, c2, c6].
    g = [_g_existence("Opportunity.LoanType"), _g_existence("Opportunity.LoanAmount"),
         _g_existence("Opportunity.PropertyValue"),
         _g_existence("Opportunity.LoanType"), _g_existence("Opportunity.LoanAmount"),
         _g_existence("Opportunity.PropertyValue"), _g_existence("Opportunity.RiskRating")]
    ov, state = _finalize(g, _cands(7), caplog_level=logging.INFO, caplog=caplog)
    assert len(ov.emissions) == 4
    assert ov.interpretation_delta["selected_path_ids"] == ["c0", "c1", "c2", "c6"]
    assert len(state.presented_candidates) == 4        # state trimmed in lockstep
    assert ov.emission is ov.emissions[0]
    # observability: the collapse is logged
    assert any("collapsed 3 duplicate" in r.message and r.levelname == "INFO"
               for r in caplog.records)


def test_finalize_single_turn_single_bundle_unchanged():
    # Case (e): one grounding, one bundle -> byte-identical to pre-D-339
    # (single-intent shape: selected_path_id set, no selected_path_ids).
    ov, state = _finalize([_g_existence("Account.Industry")], _cands(1))
    assert len(ov.emissions) == 1
    assert ov.interpretation_delta == {"selected_path_id": "c0"}
    assert len(state.presented_candidates) == 1


def test_finalize_single_turn_multi_distinct_unchanged():
    # Case (e) variant: a single turn with 3 DISTINCT claims and no duplicates —
    # dedup is a no-op, all three survive, path_ids untouched.
    g = [_g_existence("Account.Industry"), _g_existence("Account.Name"),
         _g_existence("Account.Rating")]
    ov, state = _finalize(g, _cands(3))
    assert len(ov.emissions) == 3
    assert ov.interpretation_delta["selected_path_ids"] == ["c0", "c1", "c2"]
    assert len(state.presented_candidates) == 3


def test_finalize_collapse_to_single_uses_singular_shape():
    # Two identical groundings collapse to ONE -> finalize takes the single-bundle
    # branch (selected_path_id, not _ids), describing exactly the one claim.
    g = [_g_existence("Account.Industry"), _g_existence("Account.Industry")]
    ov, state = _finalize(g, _cands(2))
    assert len(ov.emissions) == 1
    assert ov.interpretation_delta == {"selected_path_id": "c0"}


def test_finalize_alignment_invariant_failure_preserves_and_logs(caplog):
    # Case (f): groundings has a duplicate (3 bundles, 2 distinct) but
    # presented_candidates is a DIFFERENT length (2) -> the invariant fails. We
    # must NOT trim (a blind index-trim could drop the wrong path_id): behaviour
    # is preserved (all 3 bundles emitted, the pre-D-339 result) AND the mismatch
    # is logged at ERROR so it is observable.
    g = [_g_existence("Account.Industry"), _g_existence("Account.Name"),
         _g_existence("Account.Industry")]            # 3 groundings, 2 distinct
    ov, state = _finalize(g, _cands(2),               # <-- 2 != 3: misaligned
                          caplog_level=logging.ERROR, caplog=caplog)
    assert len(ov.emissions) == 3                      # NOT deduped — safe fallback
    recs = [r for r in caplog.records
            if "misalignment" in r.message and r.levelname == "ERROR"]
    assert len(recs) == 1
    assert "bundles=3" in recs[0].message and "presented=2" in recs[0].message


def test_finalize_ab65fb0c_shape_53_groundings_to_28_identities():
    # Requirement 6: reproduce the KNOWN shape of outcome ab65fb0c conceptually —
    # 53 accumulated groundings collapse to 28 canonical identities. Build 28
    # distinct claims, then a re-proposal that re-sends the first 25 (28 + 25 =
    # 53). Dedup must yield exactly 28, first-occurrence ordered.
    distinct = [_g_existence(f"Opportunity.Field_{i}") for i in range(28)]
    duplicates = [_g_existence(f"Opportunity.Field_{i}") for i in range(25)]
    groundings = distinct + duplicates                 # 53
    assert len(groundings) == 53
    assert len({_ih(author_emission(g)) for g in groundings}) == 28  # 28 identities
    ov, state = _finalize(groundings, _cands(53))
    assert len(ov.emissions) == 28
    assert ov.interpretation_delta["selected_path_ids"] == [f"c{i}" for i in range(28)]
    # every retained bundle is a unique identity
    assert len({_ih(b) for b in ov.emissions}) == 28

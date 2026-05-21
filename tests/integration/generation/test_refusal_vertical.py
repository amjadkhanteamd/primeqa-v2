"""Refusal-vertical integration tests (D-096) — real S1 grounding, local PG.

Scripted LLM (fake tool_turn) + real ``SemanticOrgModel`` over seeded S1 data.
Drives each refusal kind end to end and asserts refusal_kind/cause, phase-
tagged dismissals, deterministic prose-free explanation_hash, FK-ordered
per-requirement persistence with partial-failure isolation, and the
operational/semantic (llm_calls vs attempted_interpretation) separation.
"""
from __future__ import annotations

import pytest

from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind, RefusalKind
from primeqa.generation.governance_core import GovernanceCore, phase_for_reason

from .conftest import (
    FakeToolTurn, TEST_TENANT_ID, intent, make_request, propose_turn,
    query_outcome_rows, rel_intent,
)


def _run(seeded, intents, *, persister=None):
    """Run the refusal vertical end to end. `intents` is one per requirement."""
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.runtime import GenerationRuntime

    req = make_request(s1_version_seq=seeded["v1"])
    req.semantic_context.requirement_refs = [
        {"key": f"R{i}", "text": "the requirement"} for i in range(len(intents))
    ]
    turns = [propose_turn(it) for it in intents]
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        result = GenerationRuntime().run(
            request=req, seam=gov, tool_turn_fn=FakeToolTurn(turns), persister=persister,
        )
    return req, result


# ---------------------------------------------------------------------------
# The four refusal kinds (+ cause distinction)
# ---------------------------------------------------------------------------

def test_no_admissible_negative_no_org_constraint(seeded):
    _, res = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                  sf_api_name="Account")])  # bare Object, no VR
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.refusal_kind == RefusalKind.NO_ADMISSIBLE_NEGATIVE_SCENARIO_FOUND
    assert o.refusals[0].payload["cause"] == "no_org_constraint"
    ai = o.attempted_interpretation
    assert "no_constraint_supports_negative" in ai.dismissed_alternatives_by_reason
    assert phase_for_reason("no_constraint_supports_negative") == "grounding"
    assert len(o.explanation_hash) == 64


def test_no_admissible_negative_ontology_gap(seeded):
    _, res = _run(seeded, [intent(claim_kind="automation-effect-claim", polarity="negative",
                                  sf_api_name="Account")])  # no Flow -> Apex (Tier-2) gap
    o = res.results[0].outcome
    assert o.refusal_kind == RefusalKind.NO_ADMISSIBLE_NEGATIVE_SCENARIO_FOUND
    payload = o.refusals[0].payload
    assert payload["cause"] == "ontology_gap"
    assert payload["what_would_unblock"]   # non-empty (substrate capability)


def test_ungrounded_claim(seeded):
    _, res = _run(seeded, [intent(claim_kind="value-claim", polarity="positive",
                                  sf_api_name="Account")])  # no Field -> ungrounded
    o = res.results[0].outcome
    assert o.refusal_kind == RefusalKind.UNGROUNDED_CLAIM
    assert "insufficient_grounding" in o.attempted_interpretation.dismissed_alternatives_by_reason


def test_ambiguous_reference(seeded):
    _, res = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                  sf_api_name="Contact")])  # two Objects named Contact
    o = res.results[0].outcome
    assert o.refusal_kind == RefusalKind.AMBIGUOUS_REFERENCE
    assert len(o.refusals[0].payload["matched"]) == 2


def test_underspecified_requirement(seeded):
    _, res = _run(seeded, [intent(claim_kind=None, polarity="negative", sf_api_name="Account")])
    o = res.results[0].outcome
    assert o.refusal_kind == RefusalKind.UNDERSPECIFIED_REQUIREMENT


# ---------------------------------------------------------------------------
# Positive control: real grounding -> PROCEED_TO_EMIT (emission stubbed)
# ---------------------------------------------------------------------------

def test_grounded_candidate_proceeds_to_emit(seeded):
    from uuid import uuid4
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.governance import ConversationContext, NextAction
    from primeqa.generation.protocol import (
        GovernanceContext, OperationalContext, SemanticContext,
    )
    from primeqa.generation.runtime import RequirementState

    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        ctx = ConversationContext(
            request_id=uuid4(), requirement_ref={}, requirement_text="x",
            semantic_context=SemanticContext(s1_version_seq=seeded["v1"]),
            governance_context=GovernanceContext(), operational_context=OperationalContext(),
            shared_context={},
        )
        state = RequirementState(request_id=ctx.request_id, requirement_ref={})
        res = gov.resolve_intent(
            intent_input=intent(claim_kind="prohibition-claim", polarity="negative",
                                sf_api_name="Case"),   # Case HAS a ValidationRule
            ctx=ctx, state=state,
        )
    assert res.next_action == NextAction.PROCEED_TO_EMIT
    assert res.grounded_candidates[0].admissibility_layer == AdmissibilityLayer.LAYER_1


# ---------------------------------------------------------------------------
# Persistence (FK order, per-requirement transaction, partial-failure)
# ---------------------------------------------------------------------------

def test_persistence_fk_order_and_success_telemetry(seeded):
    from primeqa.generation.persistence import LedgerPersister
    req, res = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                    sf_api_name="Account")],
                    persister=LedgerPersister(TEST_TENANT_ID))
    rows = query_outcome_rows()
    assert len(rows["requests"]) == 1
    assert len(rows["outcomes"]) == 1
    out = rows["outcomes"][0]
    assert str(out["request_id"]) == str(req.request_id)             # outcome FK -> request
    assert out["outcome_kind"] == "refusal"
    assert len(out["explanation_hash"]) == 64
    # llm_calls FK -> the outcome; the propose tool call recorded SUCCESS
    assert rows["llm_calls"]
    assert all(str(c["generation_outcome_id"]) == str(out["outcome_id"]) for c in rows["llm_calls"])
    assert any(c["operational_outcome"] == "success" for c in rows["llm_calls"])


def test_persistence_partial_failure_isolation(seeded):
    from primeqa.generation.persistence import LedgerPersister

    class FailingPersister(LedgerPersister):
        def __init__(self, tid):
            super().__init__(tid)
            self._n = 0

        def persist_requirement_result(self, request, result):
            self._n += 1
            if self._n == 2:
                raise RuntimeError("simulated persist failure on requirement 2")
            super().persist_requirement_result(request, result)

    two = [intent(claim_kind="prohibition-claim", polarity="negative", sf_api_name="Account")] * 2
    with pytest.raises(RuntimeError):
        _run(seeded, two, persister=FailingPersister(TEST_TENANT_ID))
    # Requirement 1 was committed in its own transaction before requirement 2
    # failed — it must NOT have rolled back.
    rows = query_outcome_rows()
    assert len(rows["outcomes"]) == 1


def test_explanation_hash_deterministic_across_runs(seeded):
    _, r1 = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                 sf_api_name="Account")])
    _, r2 = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                 sf_api_name="Account")])
    assert r1.results[0].outcome.explanation_hash == r2.results[0].outcome.explanation_hash


# ---------------------------------------------------------------------------
# Operational / semantic separation (D-087)
# ---------------------------------------------------------------------------

def test_operational_semantic_separation(seeded):
    _, res = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                  sf_api_name="Account")])
    r = res.results[0]
    # The propose tool call passed Layer A + check_refs_exist -> operational SUCCESS.
    assert [c.operational_outcome.value for c in r.llm_calls] == ["success"]
    # The semantic dismissal lives ONLY in attempted_interpretation, never llm_calls.
    ai = r.outcome.attempted_interpretation
    assert "no_constraint_supports_negative" in ai.dismissed_alternatives_by_reason
    assert all(c.raw_response is None for c in r.llm_calls)   # no operational rejection feedback


# ---------------------------------------------------------------------------
# Configuration metadata-relationship admissibility (D-098.1 — the C debut)
# ---------------------------------------------------------------------------

def test_config_metadata_relationship_present_edge_admits(seeded):
    # Direct resolve_intent (grounded -> PROCEED_TO_EMIT; emission stubbed at ★).
    # Seeded: ValidationRule "Case.RequireReason" APPLIES_TO Object "Case".
    from uuid import uuid4
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.governance import ConversationContext, NextAction
    from primeqa.generation.protocol import (
        GovernanceContext, OperationalContext, SemanticContext,
    )
    from primeqa.generation.runtime import RequirementState

    ri = rel_intent(
        edge_type="APPLIES_TO",
        source={"entity_type": "ValidationRule", "sf_api_name": "Case.RequireReason"},
        target={"entity_type": "Object", "sf_api_name": "Case"})
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        ctx = ConversationContext(
            request_id=uuid4(), requirement_ref={}, requirement_text="x",
            semantic_context=SemanticContext(s1_version_seq=seeded["v1"]),
            governance_context=GovernanceContext(), operational_context=OperationalContext(),
            shared_context={})
        assert gov.check_refs_exist(intent_input=ri, ctx=ctx).ok   # both endpoints resolve
        res = gov.resolve_intent(intent_input=ri, ctx=ctx,
                                 state=RequirementState(request_id=ctx.request_id, requirement_ref={}))
    assert res.next_action == NextAction.PROCEED_TO_EMIT
    assert res.grounded_candidates[0].admissibility_layer == AdmissibilityLayer.LAYER_1


def test_config_metadata_relationship_absent_edge_refuses(seeded):
    # The VR does NOT apply to Account -> the org lacks the assumed relationship
    # -> ungrounded refusal (no silent emit). Both endpoints exist, so this is a
    # semantic (not operational) refusal.
    _, res = _run(seeded, [rel_intent(
        edge_type="APPLIES_TO",
        source={"entity_type": "ValidationRule", "sf_api_name": "Case.RequireReason"},
        target={"entity_type": "Object", "sf_api_name": "Account"})])
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.refusal_kind == RefusalKind.UNGROUNDED_CLAIM
    assert "insufficient_grounding" in o.attempted_interpretation.dismissed_alternatives_by_reason


def test_config_metadata_relationship_bad_edge_type_refuses(seeded):
    # edge_type not in TIER_1_EDGES -> ungrounded (verbatim edge-type binding).
    _, res = _run(seeded, [rel_intent(
        edge_type="NOT_A_REAL_EDGE",
        source={"entity_type": "ValidationRule", "sf_api_name": "Case.RequireReason"},
        target={"entity_type": "Object", "sf_api_name": "Case"})])
    assert res.results[0].outcome.refusal_kind == RefusalKind.UNGROUNDED_CLAIM

"""Spine unit tests (D-095) over a mocked governance seam + a fake tool_turn.

No real LLM, no DB. Covers: the propose->select->emit happy path; select
auto-skip; all-operational Layer A (grounding-free violation + check_refs_exist
miss) -> rejected_for_correction -> structural-validation-failure; budget
exhaustion; substrate-routed refusal; no-silent-drops; and the
llm_calls-vs-attempted_interpretation (operational/semantic) separation.
"""
from __future__ import annotations

from primeqa.generation.enums import (
    AdmissibilityLayer,
    LlmCallOutcome,
    OutcomeKind,
    RefusalKind,
)
from primeqa.generation.governance import (
    IntentResolution,
    NextAction,
    PresentedCandidate,
    RefCheck,
    RefusalDirective,
)
from primeqa.generation.protocol import BudgetSpec
from primeqa.generation.runtime import GenerationRuntime
from primeqa.generation.tools import TOOL_EMIT, TOOL_PROPOSE, TOOL_SELECT

from .conftest import (
    FakeToolTurn,
    MALFORMED_PROPOSE,
    MockGovernance,
    VALID_EMIT_DRAFT,
    VALID_PROPOSE,
    VALID_SELECT,
    make_request,
    make_turn,
)

SUCCESS = LlmCallOutcome.SUCCESS
REJECTED = LlmCallOutcome.REJECTED_FOR_CORRECTION


def _outcomes(rec_list):
    return [c.operational_outcome for c in rec_list]


def _names(rec_list):
    return [c.tool_name for c in rec_list]


# ---------------------------------------------------------------------------
# Happy path + auto-skip
# ---------------------------------------------------------------------------

def test_happy_path_propose_select_emit():
    req = make_request(n=1)
    fake = FakeToolTurn([
        make_turn(TOOL_PROPOSE, VALID_PROPOSE),
        make_turn(TOOL_SELECT, VALID_SELECT),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    gov = MockGovernance()
    result = GenerationRuntime().run(request=req, seam=gov, tool_turn_fn=fake)

    assert len(result.results) == 1
    r = result.results[0]
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    assert r.outcome.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert _outcomes(r.llm_calls) == [SUCCESS, SUCCESS, SUCCESS]
    assert _names(r.llm_calls) == [TOOL_PROPOSE, TOOL_SELECT, TOOL_EMIT]
    assert gov.calls["check_refs_exist"] == 1
    assert gov.calls["resolve_intent"] == 1
    assert gov.calls["accept_selection"] == 1
    assert gov.calls["finalize_outcome"] == 1
    # Semantic provenance accumulated from seam deltas (spine stores).
    ai = r.outcome.attempted_interpretation
    assert len(ai.candidate_paths) == 2
    assert ai.selected_path_id == "p1"   # real string path_id (Flag-1 fix)
    assert ai.dismissed_alternatives_by_reason.get("lower_specificity") == ["p2"]
    assert fake.calls == 3


def test_select_autoskip_single_candidate():
    req = make_request(n=1)
    gov = MockGovernance(intent=IntentResolution(
        grounded_candidates=[PresentedCandidate("p1", AdmissibilityLayer.LAYER_1)],
        next_action=NextAction.PROCEED_TO_EMIT,
        interpretation_delta={"candidate_paths": [{"path_id": "p1"}]},
    ))
    fake = FakeToolTurn([
        make_turn(TOOL_PROPOSE, VALID_PROPOSE),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    r = GenerationRuntime().run(request=req, seam=gov, tool_turn_fn=fake).results[0]

    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    assert gov.calls["accept_selection"] == 0   # auto-skipped
    assert _names(r.llm_calls) == [TOOL_PROPOSE, TOOL_EMIT]
    assert fake.calls == 2


# ---------------------------------------------------------------------------
# All-operational Layer A (D-095.1)
# ---------------------------------------------------------------------------

def test_layer_a_grounding_free_violation_then_structural_failure():
    req = make_request(n=1)
    fake = FakeToolTurn([make_turn(TOOL_PROPOSE, MALFORMED_PROPOSE)], repeat_last=True)
    gov = MockGovernance()
    r = GenerationRuntime(max_corrections=3).run(request=req, seam=gov, tool_turn_fn=fake).results[0]

    # 3 operational rejections -> structural-validation-failure.
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert r.outcome.refusal_kind == RefusalKind.STRUCTURAL_VALIDATION_FAILURE
    assert _outcomes(r.llm_calls) == [REJECTED, REJECTED, REJECTED]
    assert [c.attempt_index for c in r.llm_calls] == [1, 2, 3]
    # Grounding-free Layer A fails before the semantic seam and before refs.
    assert gov.calls["resolve_intent"] == 0
    assert gov.calls["check_refs_exist"] == 0
    assert gov.calls["route_refusal"] == 1
    # Operational rejections never touch attempted_interpretation.
    assert r.outcome.attempted_interpretation.candidate_paths == []
    assert fake.calls == 3


def test_check_refs_exist_miss_is_operational():
    req = make_request(n=1)
    fake = FakeToolTurn([make_turn(TOOL_PROPOSE, VALID_PROPOSE)], repeat_last=True)
    gov = MockGovernance(default_ref_check=RefCheck(
        ok=False, missing_refs=["Opportunity"], feedback="not found at v7",
    ))
    r = GenerationRuntime(max_corrections=3).run(request=req, seam=gov, tool_turn_fn=fake).results[0]

    # A ref-existence miss is operational (D-095.1): rejected_for_correction,
    # never a semantic dismissal; the semantic seam is never reached.
    assert r.outcome.refusal_kind == RefusalKind.STRUCTURAL_VALIDATION_FAILURE
    assert _outcomes(r.llm_calls) == [REJECTED, REJECTED, REJECTED]
    assert gov.calls["check_refs_exist"] == 3
    assert gov.calls["resolve_intent"] == 0
    assert r.outcome.attempted_interpretation.candidate_paths == []
    assert r.outcome.attempted_interpretation.dismissed_alternatives_by_reason == {}


# ---------------------------------------------------------------------------
# Budget exhaustion (D-088c)
# ---------------------------------------------------------------------------

def test_budget_exhaustion_operational_refusal():
    req = make_request(n=1, budgets=BudgetSpec(tool_call_count=1))
    fake = FakeToolTurn([make_turn(TOOL_PROPOSE, VALID_PROPOSE)], repeat_last=True)
    gov = MockGovernance()   # 2 candidates -> await_selection
    r = GenerationRuntime().run(request=req, seam=gov, tool_turn_fn=fake).results[0]

    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert r.outcome.refusal_kind == RefusalKind.OPERATIONAL_BUDGET_EXHAUSTED
    payload = r.outcome.refusals[0].payload
    assert payload["budget_dimension"] == "tool_call_count"
    ps = payload["partial_state_at_exhaustion"]
    assert ps["candidates_proposed"] == 1
    assert ps["candidates_admissibly_grounded"] == 2
    assert ps["canonicals_selected"] == 0
    assert ps["requirements_resolved"] == 0
    assert ps["requirements_unresolved"] == 1
    assert fake.calls == 1            # one turn consumed, then exhausted
    assert _outcomes(r.llm_calls) == [SUCCESS]
    assert gov.calls["route_refusal"] == 1


# ---------------------------------------------------------------------------
# Substrate-routed refusal (D-095.3 — the LLM proposes; substrate authors)
# ---------------------------------------------------------------------------

def test_substrate_routed_refusal_no_emit_turn():
    req = make_request(n=1)
    gov = MockGovernance(intent=IntentResolution(
        grounded_candidates=[],
        next_action=NextAction.REFUSE,
        refusal=RefusalDirective(RefusalKind.NO_RELEVANT_CONTEXT, {"detail": "no grounding"}),
        interpretation_delta={"dismissed_alternatives_by_reason": {"insufficient_grounding": ["c0"]}},
    ))
    # Only a propose turn is scripted; if the spine solicited emit, the fake
    # would raise (no repeat_last) — proving the refusal is substrate-routed.
    fake = FakeToolTurn([make_turn(TOOL_PROPOSE, VALID_PROPOSE)])
    r = GenerationRuntime().run(request=req, seam=gov, tool_turn_fn=fake).results[0]

    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert r.outcome.refusal_kind == RefusalKind.NO_RELEVANT_CONTEXT
    assert _outcomes(r.llm_calls) == [SUCCESS]      # propose was operationally fine
    assert gov.calls["finalize_outcome"] == 0       # no emit solicited
    assert fake.calls == 1
    # The substrate-derived dismissal is semantic — recorded in provenance.
    ai = r.outcome.attempted_interpretation
    assert ai.dismissed_alternatives_by_reason == {"insufficient_grounding": ["c0"]}


# ---------------------------------------------------------------------------
# No-silent-drops (D-072)
# ---------------------------------------------------------------------------

def test_no_silent_drops_batch():
    req = make_request(n=3)
    gov = MockGovernance(intent=IntentResolution(
        grounded_candidates=[PresentedCandidate("p1", AdmissibilityLayer.LAYER_1)],
        next_action=NextAction.PROCEED_TO_EMIT,
        interpretation_delta={"candidate_paths": [{"path_id": "p1"}]},
    ))
    turns = []
    for _ in range(3):
        turns += [make_turn(TOOL_PROPOSE, VALID_PROPOSE), make_turn(TOOL_EMIT, VALID_EMIT_DRAFT)]
    fake = FakeToolTurn(turns)
    result = GenerationRuntime().run(request=req, seam=gov, tool_turn_fn=fake)

    assert len(result.results) == 3   # exactly one outcome per requirement
    assert all(r.outcome.outcome_kind == OutcomeKind.DRAFT for r in result.results)
    assert [r.outcome.requirement_ref["key"] for r in result.results] == ["R0", "R1", "R2"]


# ---------------------------------------------------------------------------
# Operational / semantic separation (D-087)
# ---------------------------------------------------------------------------

def test_operational_and_semantic_separation():
    req = make_request(n=1)
    # First propose: ref-miss (operational). Second propose: refs ok ->
    # semantic resolve with a dismissal. Then emit.
    gov = MockGovernance(
        ref_checks=[RefCheck(ok=False, feedback="miss"), RefCheck(ok=True)],
        intent=IntentResolution(
            grounded_candidates=[PresentedCandidate("p1", AdmissibilityLayer.LAYER_1)],
            next_action=NextAction.PROCEED_TO_EMIT,
            interpretation_delta={
                "candidate_paths": [{"path_id": "p1"}],
                "dismissed_alternatives_by_reason": {"archetype_mismatch": ["p9"]},
            },
        ),
    )
    fake = FakeToolTurn([
        make_turn(TOOL_PROPOSE, VALID_PROPOSE),   # ref-miss -> rejected
        make_turn(TOOL_PROPOSE, VALID_PROPOSE),   # refs ok -> success
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    r = GenerationRuntime().run(request=req, seam=gov, tool_turn_fn=fake).results[0]

    # Operational events live in llm_calls (one rejection + two successes),
    # with per-phase attempt_index (propose attempts 1,2; emit attempt 1).
    assert _outcomes(r.llm_calls) == [REJECTED, SUCCESS, SUCCESS]
    assert [c.attempt_index for c in r.llm_calls] == [1, 2, 1]
    assert r.llm_calls[0].raw_response == {"feedback": "miss"}
    # The semantic dismissal lives ONLY in attempted_interpretation, never in
    # llm_calls; the operational "miss" never became a dismissal.
    ai = r.outcome.attempted_interpretation
    assert ai.dismissed_alternatives_by_reason == {"archetype_mismatch": ["p9"]}
    assert "miss" not in str(ai.dismissed_alternatives_by_reason)
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT

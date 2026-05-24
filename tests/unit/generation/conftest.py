"""Unit-test harness for the substrate-3 spine (no real LLM, no DB).

Provides a fake ``tool_turn`` (scripted content blocks) and a mock
``GovernanceProvider`` so the spine is exercised over a mocked seam, plus
builders for ``GenerationRequest`` and valid/invalid tool inputs.

The mock records the canonical selection in ``selected_path_id`` as the real
string ``path_id`` (D-086/D-087), now that Slice-0's ``protocol.py`` types it
``Optional[str]`` (the Flag-1 root-cause fix).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

import pytest

from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind, RefusalKind
from primeqa.generation.governance import (
    ConversationContext,
    IntentResolution,
    NextAction,
    OutcomeVerdict,
    PresentedCandidate,
    RefCheck,
    RefusalDirective,
    SelectionVerdict,
)
from primeqa.generation.protocol import (
    AttemptedInterpretation,
    BudgetSpec,
    GenerationOutcome,
    GenerationRequest,
    GovernanceContext,
    OperationalContext,
    RefusalEntry,
    SemanticContext,
)


# ---------------------------------------------------------------------------
# Fake transport — scripted tool-use turns
# ---------------------------------------------------------------------------

@dataclass
class FakeTurn:
    content_blocks: list
    input_tokens: int = 10
    output_tokens: int = 5
    model: str = "claude-test"
    stop_reason: str = "tool_use"
    latency_ms: int = 12


def make_turn(tool_name: str, tool_input: dict, **kw) -> FakeTurn:
    block = {"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
             "name": tool_name, "input": tool_input}
    return FakeTurn(content_blocks=[block], **kw)


def make_text_turn(**kw) -> FakeTurn:
    """A turn with NO tool_use block (model replied with text)."""
    return FakeTurn(content_blocks=[{"type": "text", "text": "ok"}], stop_reason="end_turn", **kw)


class FakeToolTurn:
    """Callable matching the spine's ToolTurnFn. Returns scripted turns in
    order; ``repeat_last`` re-serves the final turn (for correction loops).
    Over-calling without ``repeat_last`` raises (catches test drift)."""

    def __init__(self, turns: list[FakeTurn], repeat_last: bool = False):
        self._turns = list(turns)
        self._repeat_last = repeat_last
        self.calls = 0

    def __call__(self, *, messages, tools, tool_choice, system):
        i = self.calls
        self.calls += 1
        if i < len(self._turns):
            return self._turns[i]
        if self._repeat_last and self._turns:
            return self._turns[-1]
        raise AssertionError(f"FakeToolTurn over-called (call #{i + 1}); script exhausted")


# ---------------------------------------------------------------------------
# Outcome builders (the seam authors outcomes; mock builds Slice-0 types)
# ---------------------------------------------------------------------------

def _attempted(state) -> AttemptedInterpretation:
    return AttemptedInterpretation(**state.attempted_interpretation)


def build_draft_outcome(ctx: ConversationContext, state) -> GenerationOutcome:
    return GenerationOutcome(
        outcome_id=uuid4(), request_id=ctx.request_id, requirement_ref=ctx.requirement_ref,
        outcome_kind=OutcomeKind.DRAFT,
        claims_written=[], recipes_written=[], equivalent_existing=[],
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        attempted_interpretation=_attempted(state),
        explanation_hash="",  # deferred (replay controller)
        dismissal_taxonomy_version=ctx.governance_context.dismissal_taxonomy_version,
    )


def build_refusal_outcome(ctx: ConversationContext, state, directive: RefusalDirective) -> GenerationOutcome:
    return GenerationOutcome(
        outcome_id=uuid4(), request_id=ctx.request_id, requirement_ref=ctx.requirement_ref,
        outcome_kind=OutcomeKind.REFUSAL,
        refusal_kind=directive.refusal_kind,
        refusal_policy_version=ctx.governance_context.refusal_policy_version,
        refusals=[RefusalEntry(refusal_kind=directive.refusal_kind, payload=directive.payload)],
        attempted_interpretation=_attempted(state),
        explanation_hash="",
        dismissal_taxonomy_version=ctx.governance_context.dismissal_taxonomy_version,
    )


# ---------------------------------------------------------------------------
# Mock governance seam
# ---------------------------------------------------------------------------

class MockGovernance:
    """Scriptable GovernanceProvider. Defaults to the 2-candidate
    await-selection happy path; override per method via constructor args."""

    def __init__(
        self, *,
        intent: Optional[IntentResolution] = None,
        selection: Optional[SelectionVerdict] = None,
        default_ref_check: Optional[RefCheck] = None,
        ref_checks: Optional[list[RefCheck]] = None,
    ):
        self._intent = intent
        self._selection = selection
        self._default_ref_check = default_ref_check or RefCheck(ok=True)
        self._ref_checks = list(ref_checks or [])
        self.calls: Counter = Counter()

    def check_refs_exist(self, *, intent_input, ctx):
        self.calls["check_refs_exist"] += 1
        if self._ref_checks:
            return self._ref_checks.pop(0)
        return self._default_ref_check

    def resolve_intent(self, *, intent_input, ctx, state):
        self.calls["resolve_intent"] += 1
        if self._intent is not None:
            return self._intent
        return IntentResolution(
            grounded_candidates=[
                PresentedCandidate("p1", AdmissibilityLayer.LAYER_1),
                PresentedCandidate("p2", AdmissibilityLayer.LAYER_2),
            ],
            next_action=NextAction.AWAIT_SELECTION,
            interpretation_delta={"candidate_paths": [{"path_id": "p1"}, {"path_id": "p2"}]},
        )

    def accept_selection(self, *, selection_input, ctx, state):
        self.calls["accept_selection"] += 1
        if self._selection is not None:
            return self._selection
        # canonical p1 chosen; p2 dismissed as lower_specificity.
        return SelectionVerdict(
            accepted=True,
            interpretation_delta={
                "selected_path_id": "p1",
                "dismissed_alternatives_by_reason": {"lower_specificity": ["p2"]},
            },
        )

    def finalize_outcome(self, *, outcome_input, ctx, state):
        self.calls["finalize_outcome"] += 1
        return OutcomeVerdict(outcome=build_draft_outcome(ctx, state), interpretation_delta={})

    def route_refusal(self, *, directive, ctx, state):
        self.calls["route_refusal"] += 1
        return build_refusal_outcome(ctx, state, directive)


# ---------------------------------------------------------------------------
# Request builder + canned tool inputs
# ---------------------------------------------------------------------------

def make_request(*, n: int = 1, budgets: Optional[BudgetSpec] = None,
                 texts: Optional[list[str]] = None) -> GenerationRequest:
    texts = texts or [f"Requirement {i}" for i in range(n)]
    refs = [{"key": f"R{i}", "text": texts[i]} for i in range(n)]
    return GenerationRequest(
        request_id=uuid4(),
        semantic_context=SemanticContext(requirement_refs=refs, s1_version_seq=7, s1_version_name="v7"),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(budgets=budgets or BudgetSpec()),
    )


VALID_PROPOSE = {
    "requirement_excerpt": "the system shall reject an Opportunity without an Account",
    "intent_descriptor": {
        "archetype_hint": "data_behavior",
        "target_subject_hint": {"entity_type": "Object", "sf_api_name": "Opportunity"},
        "polarity_hint": "negative",
    },
}
VALID_SELECT = {
    "candidate_refs": ["p1", "p2"],
    "selection_rationale": {"selected_path_id": "p1", "rationale_kind": "highest_specificity"},
}
VALID_EMIT_DRAFT = {
    "outcome_kind": "draft",
    "payload": {"claim_ref": {"test_id": str(uuid4()), "version_seq": 1}, "admissibility_layer": "layer_1"},
}
# Layer-A-malformed: missing the mandatory Guardrail-3 requirement_excerpt.
MALFORMED_PROPOSE = {
    "intent_descriptor": {
        "archetype_hint": "data_behavior",
        "target_subject_hint": {"sf_api_name": "Opportunity"},
        "polarity_hint": "negative",
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def request_factory():
    return make_request


@pytest.fixture
def turn():
    return make_turn


@pytest.fixture
def fake_tool_turn():
    return FakeToolTurn


@pytest.fixture
def governance():
    return MockGovernance

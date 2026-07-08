"""D-302 — per-intent refusal visibility (closes D-299.2 Finding 2).

A refusal directive that does NOT route must still leave a record: the
multi-intent merge writes ``partial_refusals`` entries into the merged delta
(one per refused intent, keyed by the intent's re-indexed path slot), and the
runtime stores the directive of a wholly-refused turn it declines to route
(prior turns grounded, or the D-247 re-prompt hop bypassed the route). The
entries ride ``attempted_interpretation`` (JSONB, extra='allow') onto the
outcome — no schema change. ``coverage_map`` semantics stay input-steered
(unchanged D-247 contract).
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation.enums import AdmissibilityLayer, RefusalKind
from primeqa.generation.governance import (
    IntentResolution,
    NextAction,
    PresentedCandidate,
    RefusalDirective,
)
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.runtime import GenerationRuntime
from primeqa.generation.tools import TOOL_EMIT, TOOL_PROPOSE

from .conftest import MockGovernance, VALID_EMIT_DRAFT, make_request, make_turn


# --- builders ---------------------------------------------------------------

def _intent(ac_ref=None, *, nat=False, reason=None, api="Case"):
    d = {
        "requirement_excerpt": f"excerpt for AC{ac_ref}",
        "archetype_hint": "data_behavior",
        "polarity_hint": "positive",
        "claim_kind_hint": "automation-effect-claim",
        "target_subject_hint": {"entity_type": "Object", "sf_api_name": api},
    }
    if ac_ref is not None:
        d["ac_ref"] = ac_ref
    if nat:
        d["archetype_hint"] = "configuration"
        d["claim_kind_hint"] = "existence-claim"
        d["no_admissible_test"] = True
        d["no_admissible_test_reason"] = reason or "no admissible test"
    return d


def _grounded_res(path_id="c0"):
    return IntentResolution(
        grounded_candidates=[PresentedCandidate(path_id, AdmissibilityLayer.LAYER_1)],
        next_action=NextAction.PROCEED_TO_EMIT,
        interpretation_delta={"candidate_paths": [{"path_id": path_id}],
                              "dismissed_alternatives_by_reason": {},
                              "scoped_neighborhood": []},
    )


def _refused_res(detail="automation-effect needs a verifiable effect"):
    return IntentResolution(
        grounded_candidates=[], next_action=NextAction.REFUSE,
        interpretation_delta={"candidate_paths": [
            {"path_id": "c0", "status": "admissibly_grounded"}],
            "dismissed_alternatives_by_reason": {}, "scoped_neighborhood": []},
        refusal=RefusalDirective(RefusalKind.EMISSION_DEFERRED, {"detail": detail}),
    )


def _state():
    return SimpleNamespace(attempted_interpretation={"candidate_paths": []},
                           groundings=[])


def _ai_extra(outcome, key):
    ai = outcome.attempted_interpretation
    val = getattr(ai, key, None)
    if val is None and hasattr(ai, "model_dump"):
        val = ai.model_dump().get(key)
    return val


# --- governance: the multi-intent merge --------------------------------------

def test_mixed_batch_records_the_refused_intents(monkeypatch):
    """The observed D-299.2 instance: one intent grounds, its sibling refuses
    post-admissibility — the refusal must land in the merged delta, keyed by
    the sibling's path slot, instead of vanishing."""
    gov = GovernanceCore(None)
    results = iter([_grounded_res(), _refused_res()])
    monkeypatch.setattr(gov, "_resolve_one",
                        lambda pi, ctx, state: next(results))
    inp = {"intent_descriptors": [_intent(1), _intent(2)]}
    res = gov.resolve_intent(intent_input=inp, ctx=None, state=_state())

    assert res.next_action == NextAction.PROCEED_TO_EMIT
    assert [c.path_id for c in res.grounded_candidates] == ["c0"]
    prs = res.interpretation_delta["partial_refusals"]
    assert len(prs) == 1
    pr = prs[0]
    assert pr["path_id"] == "c1"          # the refused intent's slot
    assert pr["ac_ref"] == 2
    assert pr["archetype"] == "data_behavior"
    assert pr["claim_kind"] == "automation-effect-claim"
    assert pr["refusal_kind"] == "emission-deferred"
    assert "verifiable effect" in pr["payload"]["detail"]


def test_grounded_only_batch_has_no_partial_refusals(monkeypatch):
    gov = GovernanceCore(None)
    results = iter([_grounded_res(), _grounded_res()])
    monkeypatch.setattr(gov, "_resolve_one",
                        lambda pi, ctx, state: next(results))
    res = gov.resolve_intent(
        intent_input={"intent_descriptors": [_intent(1), _intent(2)]},
        ctx=None, state=_state())
    assert "partial_refusals" not in res.interpretation_delta


def test_all_refused_batch_records_every_refusal_and_routes_first():
    """No S1 needed: two no_admissible_test intents exercise the real per-intent
    path. The first directive still routes (unchanged) but BOTH now leave a
    record — the D-073 multiplicity the merge previously dropped."""
    gov = GovernanceCore(None)
    inp = {"intent_descriptors": [
        _intent(1, nat=True, reason="r1"), _intent(2, nat=True, reason="r2")]}
    res = gov.resolve_intent(intent_input=inp, ctx=None, state=_state())

    assert res.next_action == NextAction.REFUSE
    assert res.refusal is not None and res.refusal.payload["detail"] == "r1"
    prs = res.interpretation_delta["partial_refusals"]
    assert [p["path_id"] for p in prs] == ["c0", "c1"]
    assert [p["ac_ref"] for p in prs] == [1, 2]
    assert all(p["refusal_kind"] == "no-relevant-context" for p in prs)


def test_offset_keys_partial_refusals_to_the_followup_slot(monkeypatch):
    """A follow-up propose turn (D-247 offset) keys the refused intent past the
    prior turn's paths — joinable against the merged candidate_paths."""
    gov = GovernanceCore(None)
    results = iter([_grounded_res(), _refused_res()])
    monkeypatch.setattr(gov, "_resolve_one",
                        lambda pi, ctx, state: next(results))
    state = SimpleNamespace(
        attempted_interpretation={"candidate_paths": [{"path_id": "c0"}, {"path_id": "c1"}]},
        groundings=[])
    res = gov.resolve_intent(
        intent_input={"intent_descriptors": [_intent(1), _intent(2)]},
        ctx=None, state=state)
    assert res.interpretation_delta["partial_refusals"][0]["path_id"] == "c3"


# --- runtime: the cross-turn swallow + persistence onto the outcome ----------

class _SeqGov(MockGovernance):
    """MockGovernance with per-call resolve_intent scripting."""

    def __init__(self, intents, **kw):
        super().__init__(**kw)
        self._intents = list(intents)

    def resolve_intent(self, *, intent_input, ctx, state):
        self.calls["resolve_intent"] += 1
        return self._intents.pop(0)


def _acs(n):
    return [{"index": i, "label": f"criterion {i}"} for i in range(1, n + 1)]


def test_followup_turn_refusal_lands_on_the_draft_outcome():
    """Turn 1 grounds AC1; the re-prompt turn's AC2 intent wholly refuses. The
    directive never routes (a claim still drafts) — it must land on the
    outcome's attempted_interpretation. D-340: coverage_map is grounding-aware,
    so the refused-and-never-grounded AC2 records ungrounded_after_reprompt
    (pre-D-340 tag semantics called it "covered")."""
    req = make_request(texts=["freeform requirement, no list markers"])
    gov = _SeqGov([_grounded_res("p1"), _refused_res()])
    turns = [
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(2),
                                 "intent_descriptors": [_intent(1)]}),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(2)]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ]
    r = GenerationRuntime().run(
        request=req, seam=gov,
        tool_turn_fn=lambda **kw: turns.pop(0)).results[0]

    assert r.outcome.outcome_kind.value == "draft"
    prs = _ai_extra(r.outcome, "partial_refusals")
    assert prs and len(prs) == 1
    assert prs[0]["ac_ref"] == 2
    assert prs[0]["refusal_kind"] == "emission-deferred"
    cmap = _ai_extra(r.outcome, "coverage_map")
    # D-340: proposed-but-never-grounded is not coverage — the honest verdict.
    assert cmap["2"]["status"] == "refused"
    assert cmap["2"]["reason"] == "ungrounded_after_reprompt"


def test_reprompt_bypassed_refusal_is_recorded():
    """Turn 1 wholly refuses but the coverage re-prompt bypasses the route;
    turn 2 grounds. Turn 1's directive must not vanish."""
    req = make_request(texts=["freeform requirement, no list markers"])
    gov = _SeqGov([_refused_res(), _grounded_res("p1")])
    turns = [
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(2),
                                 "intent_descriptors": [_intent(1)]}),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(2)]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ]
    r = GenerationRuntime().run(
        request=req, seam=gov,
        tool_turn_fn=lambda **kw: turns.pop(0)).results[0]

    assert r.outcome.outcome_kind.value == "draft"
    prs = _ai_extra(r.outcome, "partial_refusals")
    assert prs and prs[0]["ac_ref"] == 1


def test_governance_recorded_entries_ride_the_merge_without_duplication():
    """A mixed multi-intent turn: the governance delta carries the entry; the
    runtime must merge it (append) and NOT stash a duplicate."""
    req = make_request(texts=["freeform"])
    mixed = IntentResolution(
        grounded_candidates=[PresentedCandidate("c0", AdmissibilityLayer.LAYER_1)],
        next_action=NextAction.PROCEED_TO_EMIT,
        interpretation_delta={
            "candidate_paths": [{"path_id": "c0"},
                                {"path_id": "c1", "status": "admissibly_grounded"}],
            "partial_refusals": [{
                "path_id": "c1", "ac_ref": 2, "archetype": "data_behavior",
                "claim_kind": "automation-effect-claim",
                "refusal_kind": "emission-deferred",
                "payload": {"detail": "missing field_name/expected_value"}}],
        },
    )
    gov = _SeqGov([mixed])
    turns = [make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(1), _intent(2)]}),
             make_turn(TOOL_EMIT, VALID_EMIT_DRAFT)]
    r = GenerationRuntime().run(
        request=req, seam=gov,
        tool_turn_fn=lambda **kw: turns.pop(0)).results[0]

    prs = _ai_extra(r.outcome, "partial_refusals")
    assert len(prs) == 1
    assert prs[0]["path_id"] == "c1"


def test_single_turn_full_refusal_still_routes_unchanged():
    """Zero grounded across all turns: the refusal routes exactly as before —
    a routed directive is not a partial refusal."""
    req = make_request(texts=["freeform"])
    gov = _SeqGov([_refused_res()])
    turns = [make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(1)]})]
    r = GenerationRuntime().run(
        request=req, seam=gov,
        tool_turn_fn=lambda **kw: turns.pop(0)).results[0]

    assert r.outcome.outcome_kind.value == "refusal"
    assert r.outcome.refusal_kind == RefusalKind.EMISSION_DEFERRED
    assert not _ai_extra(r.outcome, "partial_refusals")

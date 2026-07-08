"""D-247 per-AC coverage enforcer — the runtime re-prompt loop + coverage_map,
and the governance no_admissible_test / path-offset changes.

Deterministic: a mocked governance seam + a fake tool_turn drive the loop.
Coverage is steered by the propose TOOL INPUT (declared acceptance_criteria +
ac_ref tags); the seam here returns NON-c-indexed path ids ("p1"), so D-340's
grounding attribution falls back to tag semantics — these tests pin the v1
behaviour surface under that fallback. The strict grounded-per-AC semantics
(real governance, c-indexed ids) are pinned in test_coverage_grounded.py.
No live LLM, no DB.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation.enums import AdmissibilityLayer
from primeqa.generation.governance import (
    IntentResolution,
    NextAction,
    PresentedCandidate,
)
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.runtime import GenerationRuntime, RequirementState
from primeqa.generation.tools import TOOL_EMIT, TOOL_PROPOSE

from .conftest import MockGovernance, VALID_EMIT_DRAFT, VALID_PROPOSE, make_request, make_turn


# --- builders ---------------------------------------------------------------

def _acs(n):
    return [{"index": i, "label": f"criterion {i}"} for i in range(1, n + 1)]


def _intent(ac_ref, *, nat=False, reason=None, api="Case"):
    d = {
        "requirement_excerpt": f"excerpt for AC{ac_ref}",
        "archetype_hint": "configuration",
        "polarity_hint": "positive",
        "claim_kind_hint": "existence-claim",
        "target_subject_hint": {"entity_type": "Object", "sf_api_name": api},
        "ac_ref": ac_ref,
    }
    if nat:
        d["no_admissible_test"] = True
        d["no_admissible_test_reason"] = reason or "no admissible test"
    return d


def _propose(declared_n, tagged, refused=()):
    intents = [_intent(r) for r in tagged]
    intents += [_intent(r, nat=True, reason=f"AC{r} untestable") for r in refused]
    return {"acceptance_criteria": _acs(declared_n), "intent_descriptors": intents}


def _gov():
    """A seam that always PROCEEDs to emit with one grounded candidate (so the
    coverage loop — not the SELECT path — runs)."""
    return MockGovernance(intent=IntentResolution(
        grounded_candidates=[PresentedCandidate("p1", AdmissibilityLayer.LAYER_1)],
        next_action=NextAction.PROCEED_TO_EMIT,
        interpretation_delta={"candidate_paths": [{"path_id": "p1"}]},
    ))


class _RecFake:
    """Like FakeToolTurn but records the `messages` passed each call (to inspect
    the re-prompt content)."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = 0
        self.seen = []

    def __call__(self, *, messages, tools, tool_choice, system):
        # Snapshot: the runtime mutates one `messages` list in place across turns,
        # so store a shallow copy to capture what THIS call actually saw.
        self.seen.append(list(messages))
        i = self.calls
        self.calls += 1
        return self._turns[i]


def _coverage_map(r):
    ai = r.outcome.attempted_interpretation
    cm = getattr(ai, "coverage_map", None)
    if cm is None and hasattr(ai, "model_extra"):
        cm = (ai.model_extra or {}).get("coverage_map")
    if cm is None and hasattr(ai, "model_dump"):
        cm = ai.model_dump().get("coverage_map")
    return cm


# --- the runtime loop -------------------------------------------------------

def test_reprompts_once_for_uncovered_acs():
    req = make_request(texts=["freeform requirement, no list markers"])
    turns = [
        make_turn(TOOL_PROPOSE, _propose(9, [1, 2, 3, 6, 7, 9])),       # 3 uncovered: 4,5,8
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [
            _intent(4), _intent(5), _intent(8, nat=True, reason="no SLA config in org")]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ]
    fake = _RecFake(turns)
    r = GenerationRuntime().run(request=req, seam=_gov(), tool_turn_fn=fake).results[0]

    assert fake.calls == 3                       # propose -> RE-PROMPT propose -> emit
    reprompt = str(fake.seen[1][-1])             # the 2nd call's last (re-prompt) message
    assert "AC4" in reprompt and "AC5" in reprompt and "AC8" in reprompt
    assert "AC1" not in reprompt and "AC6" not in reprompt   # covered ACs not named

    cmap = _coverage_map(r)
    assert cmap["1"]["status"] == "covered" and cmap["4"]["status"] == "covered"
    assert cmap["8"]["status"] == "refused" and "SLA" in cmap["8"]["reason"]
    assert set(cmap) == {str(i) for i in range(1, 10)}


def test_no_reprompt_when_all_acs_addressed():
    req = make_request(texts=["freeform"])
    turns = [make_turn(TOOL_PROPOSE, _propose(3, [1, 2, 3])),
             make_turn(TOOL_EMIT, VALID_EMIT_DRAFT)]
    fake = _RecFake(turns)
    r = GenerationRuntime().run(request=req, seam=_gov(), tool_turn_fn=fake).results[0]
    assert fake.calls == 2                       # no re-prompt
    assert all(v["status"] == "covered" for v in _coverage_map(r).values())


def test_freeform_requirement_is_a_no_op():
    req = make_request(texts=["freeform prose with no acceptance criteria"])
    turns = [make_turn(TOOL_PROPOSE, VALID_PROPOSE),   # no acceptance_criteria declared
             make_turn(TOOL_EMIT, VALID_EMIT_DRAFT)]
    fake = _RecFake(turns)
    r = GenerationRuntime().run(request=req, seam=_gov(), tool_turn_fn=fake).results[0]
    assert fake.calls == 2
    assert _coverage_map(r) in (None, {})


def test_single_reprompt_hop_bound():
    # turn 1 covers 6/9; turn 2 STILL leaves AC8 untagged -> no third propose.
    req = make_request(texts=["freeform"])
    turns = [
        make_turn(TOOL_PROPOSE, _propose(9, [1, 2, 3, 6, 7, 9])),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(4), _intent(5)]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ]
    fake = _RecFake(turns)
    r = GenerationRuntime().run(request=req, seam=_gov(), tool_turn_fn=fake).results[0]
    assert fake.calls == 3                       # NOT 4 — one hop only
    cmap = _coverage_map(r)
    assert cmap["8"]["status"] == "refused" and cmap["8"]["reason"] == "untagged_after_reprompt"


def test_floor_shortfall_triggers_reenumerate_reprompt():
    # 4 '#' markers => floor 4; model declares only 3 and tags all 3 -> uncovered
    # empty BUT shortfall=1 -> the computer double-check fires a re-enumerate hop.
    # (Kept under MAX_INTENTS=6 per propose call.)
    text = "".join(f"# criterion {c}\n" for c in "abcd")
    req = make_request(texts=[text])
    turns = [make_turn(TOOL_PROPOSE, _propose(3, [1, 2, 3])),
             make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(4)]}),
             make_turn(TOOL_EMIT, VALID_EMIT_DRAFT)]
    fake = _RecFake(turns)
    r = GenerationRuntime().run(request=req, seam=_gov(), tool_turn_fn=fake).results[0]
    assert fake.calls == 3
    reprompt = str(fake.seen[1][-1])
    assert "at least 4" in reprompt and "declared 3" in reprompt


# --- direct helper unit (coverage_map building) -----------------------------

def test_coverage_map_built_from_grounded_tags():
    # D-340: "covered" requires a GROUNDED intent — a tagged-but-never-grounded
    # AC records ungrounded_after_reprompt (pre-D-340 tag semantics called it
    # covered); an untagged AC keeps untagged_after_reprompt.
    rt = GenerationRuntime()
    state = RequirementState(request_id=uuid4(), requirement_ref={"key": "R0"})
    ctx = SimpleNamespace(requirement_text="freeform, no markers")
    rt._coverage_capture(state, ctx, _propose(9, [1, 2, 3, 6, 7, 9]))
    rt._coverage_tag(state, _propose(9, [1, 2, 3, 6, 7, 9]))
    rt._coverage_tag(state, {"intent_descriptors": [
        _intent(4), _intent(5), _intent(8, nat=True, reason="no SLA")]})
    # grounding landed for a subset of the tagged ACs (5 and 7 refused).
    state.coverage_grounded = {1, 2, 3, 4, 6, 9}
    rt._coverage_finalize(state)
    cm = state.coverage_map
    assert cm["1"]["status"] == "covered" and cm["4"]["status"] == "covered"
    assert cm["8"]["status"] == "refused" and cm["8"]["reason"] == "no SLA"
    assert (cm["5"]["status"] == "refused"
            and cm["5"]["reason"] == "ungrounded_after_reprompt")
    assert (cm["7"]["status"] == "refused"
            and cm["7"]["reason"] == "ungrounded_after_reprompt")
    assert set(cm) == {str(i) for i in range(1, 10)}


# --- governance: no_admissible_test + path offset (no S1) -------------------

def test_governance_no_admissible_test_records_refusal_no_grounding():
    gov = GovernanceCore(None)   # _resolve_no_admissible_test never touches S1
    desc = {
        "archetype_hint": "configuration", "claim_kind_hint": "existence-claim",
        "target_subject_hint": {"entity_type": "Object", "sf_api_name": "Case_SLA__c"},
        "no_admissible_test": True, "no_admissible_test_reason": "org has no SLA config",
    }
    res = gov._resolve_no_admissible_test(desc, "excerpt")
    assert res.grounded_candidates == []
    assert res.next_action == NextAction.REFUSE
    paths = (res.interpretation_delta or {}).get("candidate_paths") or []
    assert any(p.get("dismissal_reason") == "no_admissible_test" for p in paths)


def test_governance_resolve_intent_offsets_followup_paths():
    gov = GovernanceCore(None)
    state = SimpleNamespace(
        attempted_interpretation={"candidate_paths": [{"path_id": "c0"}, {"path_id": "c1"}]},
        groundings=[])
    inp = {"intent_descriptors": [
        dict(_intent(4, nat=True, reason="r1")),
        dict(_intent(5, nat=True, reason="r2")),
    ]}
    res = gov.resolve_intent(intent_input=inp, ctx=None, state=state)
    ids = [p["path_id"] for p in res.interpretation_delta["candidate_paths"]]
    assert ids == ["c2", "c3"]   # offset past the 2 pre-existing paths, no collision

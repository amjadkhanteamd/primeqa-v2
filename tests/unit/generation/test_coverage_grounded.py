"""D-340 — grounded-per-AC coverage: "covered" requires a GROUNDED intent.

Production lineage (the 2026-07-08 D-247 value analysis): req-302's turn 1
tagged all 10 ACs while its AC3/AC10 automation intents REFUSED grounding —
the v1 tag-at-proposal metric reported 10/10 covered, and only a FALSE
floor-shortfall re-prompt accidentally recovered the missing coverage (8 of
the 48 currently-approved req-302 claims trace to those accidental second
turns). D-340 makes the recovery intentional: the single D-247 hop now fires
for ACs with no grounded intent (UNGROUNDED) or no intent at all (UNATTEMPTED),
names only those ACs, carries the grounding-failure feedback, and asks for a
delta — while COVERED (grounded) and explicitly-REFUSED (no_admissible_test)
ACs never re-fire.

The seam here speaks the REAL D-207 contract — per-intent c-indexed path ids,
one grounded candidate per non-refusing intent, per-intent partial_refusals —
so the strict attribution path is exercised (the "p1"-style fallback surface
is pinned in test_coverage_enforcer.py). No live LLM, no DB.
"""
from __future__ import annotations

from collections import Counter

from primeqa.generation.enums import AdmissibilityLayer, RefusalKind
from primeqa.generation.governance import (
    IntentResolution,
    NextAction,
    PresentedCandidate,
    RefCheck,
    RefusalDirective,
    OutcomeVerdict,
)
from primeqa.generation.runtime import GenerationRuntime
from primeqa.generation.tools import TOOL_EMIT, TOOL_PROPOSE

from .conftest import (
    VALID_EMIT_DRAFT,
    build_draft_outcome,
    build_refusal_outcome,
    make_request,
    make_turn,
)


# --- builders ----------------------------------------------------------------

def _acs(n):
    return [{"index": i, "label": f"criterion {i}"} for i in range(1, n + 1)]


def _intent(ac_ref, *, api="Good__c", nat=False, reason=None, excerpt=None):
    d = {
        "requirement_excerpt": excerpt or f"excerpt for AC{ac_ref} via {api}",
        "archetype_hint": "data_behavior",
        "polarity_hint": "positive",
        "claim_kind_hint": "automation-effect-claim",
        "target_subject_hint": {"entity_type": "Object", "sf_api_name": api},
        "ac_ref": ac_ref,
    }
    if nat:
        d["no_admissible_test"] = True
        d["no_admissible_test_reason"] = reason or f"AC{ac_ref} untestable"
    return d


class GroundSteerGov:
    """A governance seam speaking the real D-207 resolution contract, with
    grounding steered by the intent's target api: ``Bad*`` apis REFUSE
    (ungrounded-claim + a detail), everything else grounds one c-indexed
    candidate. no_admissible_test intents never ground (a dismissed path)."""

    def __init__(self):
        self.calls = Counter()

    def check_refs_exist(self, *, intent_input, ctx):
        self.calls["check_refs_exist"] += 1
        return RefCheck(ok=True)

    def resolve_intent(self, *, intent_input, ctx, state):
        self.calls["resolve_intent"] += 1
        descs = intent_input.get("intent_descriptors")
        if not isinstance(descs, list):
            d = intent_input.get("intent_descriptor")
            descs = [d] if isinstance(d, dict) else []
        offset = len((getattr(state, "attempted_interpretation", None) or {})
                     .get("candidate_paths") or [])
        cands, paths, prs = [], [], []
        for i, d in enumerate(descs):
            pid = f"c{offset + i}"
            api = (d.get("target_subject_hint") or {}).get("sf_api_name") or ""
            if d.get("no_admissible_test"):
                paths.append({"path_id": pid, "status": "dismissed",
                              "dismissal_reason": "no_admissible_test"})
                continue
            if api.startswith("Bad"):
                paths.append({"path_id": pid, "status": "dismissed"})
                prs.append({"path_id": pid, "ac_ref": d.get("ac_ref"),
                            "archetype": d.get("archetype_hint"),
                            "claim_kind": d.get("claim_kind_hint"),
                            "refusal_kind": "ungrounded-claim",
                            "payload": {"detail": f"no S1 grounding for {api}"}})
                continue
            paths.append({"path_id": pid, "status": "admissibly_grounded"})
            cands.append(PresentedCandidate(pid, AdmissibilityLayer.LAYER_1))
        delta = {"candidate_paths": paths,
                 "dismissed_alternatives_by_reason": {}, "scoped_neighborhood": []}
        if prs:
            delta["partial_refusals"] = prs
        if cands:
            return IntentResolution(cands, NextAction.PROCEED_TO_EMIT, delta)
        return IntentResolution([], NextAction.REFUSE, delta,
                                refusal=RefusalDirective(
                                    RefusalKind.UNGROUNDED_CLAIM,
                                    {"detail": "no intent grounded"}))

    def finalize_outcome(self, *, outcome_input, ctx, state):
        self.calls["finalize_outcome"] += 1
        return OutcomeVerdict(outcome=build_draft_outcome(ctx, state),
                              interpretation_delta={})

    def route_refusal(self, *, directive, ctx, state):
        self.calls["route_refusal"] += 1
        return build_refusal_outcome(ctx, state, directive)


class _RecFake:
    """Serves scripted turns and snapshots the messages each call saw (to
    inspect the recovery re-prompt content)."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = 0
        self.seen = []

    def __call__(self, *, messages, tools, tool_choice, system):
        self.seen.append(list(messages))
        i = self.calls
        self.calls += 1
        return self._turns[i]


def _run(turns, *, text="freeform requirement, no list markers"):
    req = make_request(texts=[text])
    fake = _RecFake(turns)
    r = GenerationRuntime().run(
        request=req, seam=GroundSteerGov(), tool_turn_fn=fake).results[0]
    return r, fake


def _cmap(r):
    ai = r.outcome.attempted_interpretation
    return (ai.model_dump() if hasattr(ai, "model_dump") else dict(ai)).get(
        "coverage_map")


def _recovery(r):
    ai = r.outcome.attempted_interpretation
    return (ai.model_dump() if hasattr(ai, "model_dump") else dict(ai)).get(
        "coverage_recovery")


# --- A: everything grounds -> COVERED, no recovery ---------------------------

def test_A_all_acs_grounded_no_recovery_turn():
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(3),
                                 "intent_descriptors": [_intent(1), _intent(2), _intent(3)]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    assert fake.calls == 2                          # propose -> emit, no hop
    cm = _cmap(r)
    assert all(v["status"] == "covered" for v in cm.values())
    assert _recovery(r) is None                     # no hop -> no recovery record


# --- B: only intent for an AC fails grounding -> UNGROUNDED -> recovery ------

def test_B_ungrounded_ac_fires_recovery_naming_it_with_feedback():
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(3),
                                 "intent_descriptors": [
                                     _intent(1), _intent(2, api="Bad__c"), _intent(3)]}),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(2, api="Good2__c")]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    assert fake.calls == 3
    reprompt = str(fake.seen[1][-1])
    assert "- AC2:" in reprompt                     # the ungrounded AC is named
    assert "- AC1:" not in reprompt and "- AC3:" not in reprompt  # covered: not named
    assert "grounding failed" in reprompt           # feedback line present
    assert "no S1 grounding for Bad__c" in reprompt  # the partial_refusal detail
    assert "Return ONLY additional intents" in reprompt
    rec = _recovery(r)
    assert rec["requested_refs"] == [2]
    assert rec["ungrounded_refs"] == [2] and rec["unattempted_refs"] == []


# --- C: one intent fails, a sibling grounds -> COVERED, no recovery ----------

def test_C_ac_with_failing_and_grounding_intents_is_covered():
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(2),
                                 "intent_descriptors": [
                                     _intent(1),
                                     _intent(2, api="Bad__c"),
                                     _intent(2, api="Good2__c")]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    assert fake.calls == 2                          # no unnecessary recovery
    assert _cmap(r)["2"]["status"] == "covered"


# --- D: explicit no_admissible_test -> REFUSED, no recovery loop -------------

def test_D_explicit_refusal_never_reprompts():
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(2),
                                 "intent_descriptors": [
                                     _intent(1),
                                     _intent(2, nat=True, reason="org has no config")]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    assert fake.calls == 2
    cm = _cmap(r)
    assert cm["2"]["status"] == "refused" and cm["2"]["reason"] == "org has no config"


# --- E: no intent at all for an AC -> UNATTEMPTED -> recovery ----------------

def test_E_unattempted_ac_fires_recovery():
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(3),
                                 "intent_descriptors": [_intent(1), _intent(3)]}),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(2)]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    assert fake.calls == 3
    reprompt = str(fake.seen[1][-1])
    assert "- AC2:" in reprompt and "no intent proposed yet" in reprompt
    rec = _recovery(r)
    assert rec["unattempted_refs"] == [2] and rec["ungrounded_refs"] == []
    assert _cmap(r)["2"]["status"] == "covered"     # the delta grounded it


# --- F: recovery grounds the previously UNGROUNDED AC -> coverage closes -----

def test_F_recovery_grounding_closes_coverage():
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(2),
                                 "intent_descriptors": [
                                     _intent(1), _intent(2, api="Bad__c")]}),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(2, api="Good2__c")]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    cm = _cmap(r)
    assert cm["1"]["status"] == "covered" and cm["2"]["status"] == "covered"
    rec = _recovery(r)
    assert rec["recovery_intents"] == 1             # delta-only, not a full resend
    assert rec["recovery_grounded"] == 1
    assert rec["recovery_newly_covered"] == [2]
    assert rec["recovery_zero_progress"] is False


# --- G: recovery fails grounding again -> bounded, honest partial draft ------

def test_G_recovery_failure_terminates_single_hop_with_honest_verdict():
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(2),
                                 "intent_descriptors": [
                                     _intent(1), _intent(2, api="Bad__c")]}),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(2, api="BadAgain__c")]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    assert fake.calls == 3                          # ONE hop — no infinite retry
    assert r.outcome.outcome_kind.value == "draft"  # partial draft, not refusal
    cm = _cmap(r)
    assert (cm["2"]["status"] == "refused"
            and cm["2"]["reason"] == "ungrounded_after_reprompt")
    rec = _recovery(r)
    assert rec["recovery_zero_progress"] is True


# --- H: recovery repeats old intents -> no coverage progress -----------------

def test_H_recovery_repeating_old_intents_is_zero_progress():
    # The repeats re-ground (same api) but close nothing that was requested.
    # Their duplicate CLAIMS are collapsed downstream by D-339's canonical
    # finalize dedup (pinned in test_finalize_dedup.py) — here we pin that
    # repeats do not count as coverage progress.
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(2),
                                 "intent_descriptors": [
                                     _intent(1), _intent(2, api="Bad__c")]}),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [_intent(1)]}),  # a repeat
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    assert fake.calls == 3
    cm = _cmap(r)
    assert cm["1"]["status"] == "covered"
    assert cm["2"]["reason"] == "ungrounded_after_reprompt"
    rec = _recovery(r)
    assert rec["recovery_grounded"] == 1            # the repeat DID ground...
    assert rec["recovery_newly_covered"] == []      # ...but closed nothing asked
    assert rec["recovery_zero_progress"] is True


# --- I: repeats + one genuinely new grounding -> the new one counts ----------

def test_I_recovery_duplicates_plus_new_grounding_counts_the_new():
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(2),
                                 "intent_descriptors": [
                                     _intent(1), _intent(2, api="Bad__c")]}),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [
            _intent(1),                                  # repeat (dup claim -> D-339)
            _intent(2, api="Good2__c")]}),               # genuinely new grounding
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    cm = _cmap(r)
    assert cm["1"]["status"] == "covered" and cm["2"]["status"] == "covered"
    rec = _recovery(r)
    assert rec["recovery_intents"] == 2 and rec["recovery_grounded"] == 2
    assert rec["recovery_newly_covered"] == [2]
    assert rec["recovery_zero_progress"] is False


# --- J: one AC, multiple distinct grounded intents -> breadth preserved ------

def test_J_multiple_grounded_intents_per_ac_all_survive():
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(1),
                                 "intent_descriptors": [
                                     _intent(1, api="Good__c"),
                                     _intent(1, api="Good2__c"),
                                     _intent(1, api="Good3__c")]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    assert fake.calls == 2                          # covered -> no recovery
    assert _cmap(r)["1"]["status"] == "covered"
    # breadth: all three grounded candidates accumulate toward emission — the
    # grounded-coverage predicate is only the recovery TRIGGER, never a cap.
    assert len(r.outcome.attempted_interpretation.candidate_paths) == 3


# --- K: the production req-302 shape -----------------------------------------

def test_K_req302_shape_targeted_recovery_for_ac3_and_ac10():
    """Turn 1 tags all 10 ACs; the AC3 + AC10 automation intents fail grounding
    (the live 5be523ef/ec24ce8a shape). D-340: coverage must NOT read 10/10,
    the recovery hop names AC3 + AC10 ONLY (with grounding feedback), a
    2-intent delta closes them — no full-array regeneration."""
    t1_intents = [_intent(i) for i in (1, 2, 4, 5, 6, 7, 8, 9)] + [
        _intent(3, api="BadLTV__c"), _intent(10, api="BadTask__c")]
    r, fake = _run([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(10),
                                 "intent_descriptors": t1_intents}),
        make_turn(TOOL_PROPOSE, {"intent_descriptors": [
            _intent(3, api="LTV_Formula__c"), _intent(10, api="HL_Task__c")]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    assert fake.calls == 3
    reprompt = str(fake.seen[1][-1])
    assert "- AC3:" in reprompt and "- AC10:" in reprompt
    for i in (1, 2, 4, 5, 6, 7, 8, 9):              # grounded ACs never named
        assert f"- AC{i}:" not in reprompt
    assert "no S1 grounding for BadLTV__c" in reprompt
    rec = _recovery(r)
    assert rec["requested_refs"] == [3, 10]
    assert rec["ungrounded_refs"] == [3, 10] and rec["unattempted_refs"] == []
    assert rec["recovery_intents"] == 2             # targeted delta, not 29 intents
    assert rec["recovery_newly_covered"] == [3, 10]
    cm = _cmap(r)
    assert all(v["status"] == "covered" for v in cm.values())
    assert set(cm) == {str(i) for i in range(1, 11)}


# --- fallback: a seam without c-indexed ids degrades to tag semantics --------

def test_fallback_non_conforming_path_ids_keep_v1_tag_semantics():
    """A seam whose grounded candidates carry non-c-indexed path ids gives no
    per-intent attribution — D-340 falls back to tag semantics for the turn
    (strictly no more re-prompts than pre-D-340). Guards the compat surface
    test_coverage_enforcer.py relies on."""
    class LegacyGov(GroundSteerGov):
        def resolve_intent(self, *, intent_input, ctx, state):
            res = super().resolve_intent(intent_input=intent_input, ctx=ctx, state=state)
            for c in res.grounded_candidates:
                c.path_id = "p-legacy"              # break the c-index contract
            return res

    req = make_request(texts=["freeform"])
    fake = _RecFake([
        make_turn(TOOL_PROPOSE, {"acceptance_criteria": _acs(2),
                                 "intent_descriptors": [_intent(1), _intent(2)]}),
        make_turn(TOOL_EMIT, VALID_EMIT_DRAFT),
    ])
    r = GenerationRuntime().run(
        request=req, seam=LegacyGov(), tool_turn_fn=fake).results[0]
    assert fake.calls == 2                          # tagged => covered => no hop
    assert all(v["status"] == "covered" for v in _cmap(r).values())

"""Live-mode scorer (D-104) — ontology-coherence adjudication, exercised
deterministically against the real corpus envelopes with stubbed observations.
No PG, no LLM: invariant violation -> auto-fail; invariants-hold-but-diverges ->
drift (human-judged); benign-only difference -> neutral."""
from __future__ import annotations

from primeqa.generation.eval import ObservedOutcome, load_corpus, score_live


def _case(case_id: str):
    return next(c for c in load_corpus() if c.id == case_id)


def _obs(outcome_kind, *, archetype=None, claim_kind=None, caveat_required=False,
         refusal_kind=None):
    return ObservedOutcome(outcome_kind, archetype, claim_kind, caveat_required,
                           refusal_kind)


def _score(case_id, observed):
    return score_live(_case(case_id), observed, prompt_version="generation@v1",
                      model="test")


# --- config draft probe ---------------------------------------------------

def test_config_primary_is_neutral():
    r = _score("config-verified-draft", _obs(
        "draft", archetype="configuration",
        claim_kind="metadata-relationship-claim", caveat_required=False))
    assert r.invariant_ok and not r.drift and r.verdict == "neutral"


def test_config_collapse_to_behavioral_negative_auto_fails():
    # config -> caveated data_behavior negative = ontology collapse (D-104.3).
    r = _score("config-verified-draft", _obs(
        "draft", archetype="data_behavior", claim_kind="prohibition-claim",
        caveat_required=True))
    assert r.invariant_ok is False and r.verdict == "violation" and r.violated


def test_config_refusal_auto_fails():
    # Strongly-groundable config probe: no-refusal is INVARIANT (D-104.3 —
    # a refusal where strong grounding exists and there's no ambiguity is an
    # ontology issue). A refusal (e.g. the LLM proposing the not-yet-emittable
    # existence-claim -> capability refusal) violates must-draft -> auto-fail.
    # The acceptable claim_kind variants help a reviewer read such a fail as
    # capability-limited, not a prompt regression.
    r = _score("config-verified-draft", _obs("refusal",
               refusal_kind="underspecified-requirement"))
    assert r.invariant_ok is False and r.verdict == "violation"


# --- behaviour-incomplete negative probe (D-293) --------------------------

def test_behaviour_incomplete_refusal_is_neutral():
    # D-293: the formula-less prohibition's invariant outcome is a REFUSAL
    # (behaviour-incomplete). A refusal holds the envelope -> neutral.
    r = _score("behaviour-incomplete-negative", _obs(
        "refusal", refusal_kind="behaviour-incomplete"))
    assert r.invariant_ok and not r.drift and r.verdict == "neutral"


def test_behaviour_incomplete_degraded_to_draft_auto_fails():
    # D-293's central guarantee: emitting ANY draft for the non-derivable
    # prohibition is the degrade the decision forbids -> the envelope's
    # outcome_kind=refusal invariant is violated -> auto-fail. (This is exactly
    # the pre-D-293 caveated-inspection degradation, now a scorer violation.)
    r = _score("behaviour-incomplete-negative", _obs(
        "draft", archetype="data_behavior", claim_kind="prohibition-claim",
        caveat_required=True))
    assert r.invariant_ok is False and r.verdict == "violation"


def test_behaviour_incomplete_collapse_to_config_auto_fails():
    # a config metadata-relationship draft is also a non-refusal -> violation.
    r = _score("behaviour-incomplete-negative", _obs(
        "draft", archetype="configuration",
        claim_kind="metadata-relationship-claim", caveat_required=False))
    assert r.invariant_ok is False and r.verdict == "violation"


# --- refusal probe (no-org-constraint) ------------------------------------

def test_refusal_probe_draft_auto_fails():
    # a grounded draft where the bare org can ground nothing = hallucination.
    r = _score("refuse-no-org-constraint", _obs(
        "draft", archetype="data_behavior", claim_kind="prohibition-claim"))
    assert r.invariant_ok is False and r.verdict == "violation"


def test_refusal_probe_matching_refusal_is_neutral():
    r = _score("refuse-no-org-constraint", _obs(
        "refusal", refusal_kind="no-admissible-negative-scenario-found"))
    assert r.invariant_ok and not r.drift and r.verdict == "neutral"


def test_refusal_probe_alternate_refusal_kind_is_drift():
    # refusal_kind refinement is a coherent alternate -> drift, not auto-fail.
    r = _score("refuse-no-org-constraint", _obs(
        "refusal", refusal_kind="ungrounded-claim"))
    assert r.invariant_ok is True and r.drift is True


# --- underspecified probe (loose: refusal_kind benign) --------------------

def test_underspecified_benign_refusal_kind_ignored():
    # invariant = must-refuse; refusal_kind is benign (loose) -> a different
    # refusal_kind is ignored, not drift.
    r = _score("refuse-underspecified-requirement", _obs(
        "refusal", refusal_kind="ungrounded-claim"))
    assert r.invariant_ok and not r.drift and r.verdict == "neutral"


def test_underspecified_draft_auto_fails():
    r = _score("refuse-underspecified-requirement", _obs(
        "draft", archetype="data_behavior", claim_kind="prohibition-claim"))
    assert r.invariant_ok is False

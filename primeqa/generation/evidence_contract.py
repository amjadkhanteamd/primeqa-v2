"""Claim-strength ↔ evidence-strength contract (req-315 named abstraction, D-345).

The tactical guards (D-342) each stopped ONE way an emitted test can be
evidentially weak (existence standing in for behaviour, a rejection that never
fires). This module names the general principle behind them:

> **Evidence strength must satisfy claim strength — and, for a behavioural
> claim, the evidence must be ATTRIBUTED (the intended control caused the
> outcome), not merely an OUTCOME (something happened).**

For a prohibition, "the save was rejected" (OUTCOME) is NOT the same as "the
save was rejected *because the intended validation rule fired*" (ATTRIBUTED).
The latter is what makes a negative test trustworthy in a heavily-automated org
(a duplicate rule / Apex exception / the wrong VR can also reject). The
attribution signal already exists per emission — the recipe's
``error_message_pattern`` (D-297) pins the specific rule — this module reads it
uniformly and scores it against a per-claim-kind requirement.

Three tiers (ordered): ``STRUCTURAL`` < ``OUTCOME`` < ``ATTRIBUTED``.
This is a READ-ONLY classifier over the persisted claim + recipe bodies — it
originates nothing and changes no emission behaviour; it makes trustworthiness
MEASURABLE (the benchmark three-number report) and is the seam a later slice can
turn into emission-time enforcement.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Any, Iterable, Optional


class EvidenceTier(IntEnum):
    """How strong the evidence a test provides is (ordered, comparable)."""
    NONE = 0
    STRUCTURAL = 1     # metadata existence — the config is present
    OUTCOME = 2        # a run-time outcome happened (accept/reject) — but not why/what
    ATTRIBUTED = 3     # the INTENDED control/effect is confirmed (attributed)


# The minimum evidence a claim_kind must provide to be trustworthy for the
# behaviour it asserts (the claim-strength ↔ evidence-strength contract). A
# configuration existence-claim legitimately needs only STRUCTURAL; every
# data_behavior claim asserts a behaviour and needs ATTRIBUTED evidence.
_REQUIRED: dict[str, EvidenceTier] = {
    "existence-claim": EvidenceTier.STRUCTURAL,
    "property-claim": EvidenceTier.STRUCTURAL,
    "metadata-relationship-claim": EvidenceTier.STRUCTURAL,
    "capability-claim": EvidenceTier.STRUCTURAL,
    "layout-claim": EvidenceTier.STRUCTURAL,
    # 3A-2: conformance-claim evidence is the browser-plane ENGINE
    # OBSERVATION class — structurally captured; its verdict semantics are
    # owned by the result processor (3A-4), not the S4 org-evidence ladder.
    # Declared STRUCTURAL deliberately (the unknown-kind default of
    # ATTRIBUTED would demand org-causal evidence this kind cannot produce).
    "conformance-claim": EvidenceTier.STRUCTURAL,
    "value-claim": EvidenceTier.ATTRIBUTED,          # write then read-back the value
    "acceptance-claim": EvidenceTier.ATTRIBUTED,     # accept + resulting state
    "prohibition-claim": EvidenceTier.ATTRIBUTED,    # reject + WHICH rule (message)
    "state-transition-claim": EvidenceTier.ATTRIBUTED,   # resulting state read-back
    "automation-effect-claim": EvidenceTier.ATTRIBUTED,  # effect + causal attribution
}


def required_evidence(claim_kind: str) -> EvidenceTier:
    """The minimum evidence tier ``claim_kind`` must provide. Unknown kinds
    default to ATTRIBUTED — fail toward demanding the strongest evidence rather
    than silently accepting a weak test."""
    return _REQUIRED.get(claim_kind, EvidenceTier.ATTRIBUTED)


def _steps(recipe_body: Optional[dict]) -> list[dict]:
    if not isinstance(recipe_body, dict):
        return []
    steps = recipe_body.get("steps")
    return steps if isinstance(steps, list) else []


def _has_readback_assert(steps: list[dict]) -> bool:
    """A read of a created/updated record followed by a value/exists assertion —
    the resulting-state confirmation an acceptance / value / transition needs."""
    kinds = {s.get("kind") for s in steps}
    return "assert" in kinds and ("read" in kinds or "read_metadata" in kinds)


def provided_evidence(claim_kind: str, recipe_bodies: Iterable[dict]) -> EvidenceTier:
    """The evidence tier the EMITTED recipes actually provide for this claim.

    - a rejection whose ``error_message_pattern`` pins the rule → ATTRIBUTED;
      a rejection with only the generic code → OUTCOME (rejected, not attributed);
    - an accepted create/update WITH a resulting-state read-back → ATTRIBUTED;
      accepted without a read-back → OUTCOME;
    - a value-claim create + read-back assert → ATTRIBUTED;
    - a metadata read + exists assert (no data mutation) → STRUCTURAL;
    - nothing recognizable → NONE.
    Read-only over the persisted ``observation_realization`` bodies."""
    all_steps: list[dict] = []
    data_mutation = False
    for rb in recipe_bodies:
        st = _steps(rb)
        all_steps += st
        for s in st:
            if s.get("kind") in ("create", "update", "delete"):
                data_mutation = True

    # Prohibition / any rejection-bearing recipe.
    rejections = [s.get("expect_rejection") for s in all_steps
                  if s.get("expect_rejection")]
    if rejections:
        if any((r or {}).get("error_message_pattern") for r in rejections):
            return EvidenceTier.ATTRIBUTED          # rejected + WHICH rule (D-297)
        return EvidenceTier.OUTCOME                 # rejected, but not attributed

    # Acceptance / value / state-transition — accepted + resulting state.
    accepted = any(s.get("expect_acceptance") for s in all_steps)
    readback = _has_readback_assert(all_steps)
    if data_mutation and readback:
        return EvidenceTier.ATTRIBUTED
    if accepted:
        return EvidenceTier.OUTCOME

    # Pure metadata inspection (no data mutation) — structural existence only.
    if all_steps and not data_mutation:
        return EvidenceTier.STRUCTURAL
    return EvidenceTier.NONE


def meets_contract(claim_kind: str, recipe_bodies: Iterable[dict]) -> bool:
    """Whether the emission's evidence satisfies the claim's required strength —
    the trustworthiness test the benchmark scorer counts. A behavioural claim
    backed only by STRUCTURAL/OUTCOME evidence FAILS (the T6/T7 wrong-green and
    the un-attributed-rejection classes)."""
    recipe_bodies = list(recipe_bodies)
    return provided_evidence(claim_kind, recipe_bodies) >= required_evidence(claim_kind)

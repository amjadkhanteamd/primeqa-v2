"""S6 deeper attribution — the differentiating *why* for failed behavioral
verdicts (SPEC §4 slice 2, D-111.1).

`attribute_run(interpretation, evidence, *, s1) → Interpretation` enriches **only**
the two *failed* behavioral verdicts (`prohibition_not_enforced`,
`rejected_unasserted_reason`) with a structured :class:`Cause`, derived
**deterministically** from S1's validation-rule metadata. Pass-through for every
other verdict. It reads S1 read-only and **never re-judges the outcome** (S4 owns
it) — it only deepens `attribution` and attaches `cause`.

S6 reads S1 through the :class:`S1VrReader` port (D-111.1 / S6-3): the
inter-substrate read-through pattern. Slice 2a defines the **port** (and is
tested with a stub); slice 2b provides the production reader that delegates to
S1's query interface.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Protocol

from primeqa.execution_engine.evidence import CreateAttemptEvidence, RunEvidence
from primeqa.interpretation.model import Cause, Interpretation
from primeqa.semantic.formula import NonEvaluable, evaluate, parse

# The generic validation-rule rejection code (S3 / emission.py — the D-101.2
# honest floor). A rejection carrying it is a VR firing; anything else is a
# platform constraint.
_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"

_ENRICHED = ("prohibition_not_enforced", "rejected_unasserted_reason")


@dataclass(frozen=True)
class VrMeta:
    """The slice of a validation rule's S1 metadata S6 needs for attribution."""

    name: str
    is_active: bool
    formula_text: Optional[str] = None
    error_message: Optional[str] = None


class S1VrReader(Protocol):
    """Read-port for S1's validation-rule metadata (D-111.1 / S6-3).

    Production delegates to S1's query interface; tests inject a stub. Returns
    the VRs that apply to ``subject_external_id`` (the `APPLIES_TO` edge)."""

    def vrs_for_object(self, subject_external_id: str) -> tuple[VrMeta, ...]:
        ...


def attribute_run(
    interpretation: Interpretation, evidence: RunEvidence, *, s1: S1VrReader,
) -> Interpretation:
    """Enrich a failed behavioral interpretation with a structured cause from
    S1. Pass-through (unchanged) for any other verdict. Never mutates the
    carried outcome."""
    if interpretation.verdict not in _ENRICHED:
        return interpretation
    create = _create_step(evidence)
    if create is None:
        return interpretation

    vrs = s1.vrs_for_object(create.sobject)
    if interpretation.verdict == "prohibition_not_enforced":
        cause = _attribute_not_enforced(create, vrs)
    else:
        cause = _attribute_unasserted(create, vrs)

    return replace(
        interpretation,
        cause=cause,
        attribution=f"{interpretation.attribution} {_prose(cause)}",
    )


# ---------------------------------------------------------------------------
# prohibition_not_enforced — (a) inactive / (b) drift / (c) enforcement gap /
# (d) indeterminate. Each active/inactive VR's CURRENT formula is evaluated
# against the create's payload via the neutral `formula.evaluate` primitive
# (D-114 — the shared sibling of S3's `derive`; S6 ↛ S8). Three-valued:
# True = the payload violates the current formula; False = it does not
# (evaluable); NonEvaluable = the current formula left the single-object subset
# (org-state / unset fields) so violation can't be computed.
# ---------------------------------------------------------------------------

def _attribute_not_enforced(create: CreateAttemptEvidence, vrs) -> Cause:
    violated_active, violated_inactive, indeterminate = [], [], []
    active_not_violated = False
    for vr in vrs:
        if not vr.formula_text:
            continue
        result = evaluate(parse(vr.formula_text), create.field_values)
        if result is True:
            (violated_active if vr.is_active else violated_inactive).append(vr)
        elif isinstance(result, NonEvaluable):
            indeterminate.append(vr)
        elif vr.is_active:
            active_not_violated = True   # active rule, formula evaluable + not violated
        # inactive + not violated → no bucket (an inactive rule doesn't enforce anyway).

    # A confirmed violation wins (it fixes the loosened-still-violating false-drift:
    # `99` violates a current `Amount < 200`, so this is an enforcement gap, not drift).
    if violated_active:
        vr = violated_active[0]
        return Cause("enforcement_gap", vr_name=vr.name,
                     detail="the VR is active and its current formula is violated by "
                            "the create payload, yet the create succeeded — a real "
                            "enforcement gap")
    if violated_inactive:
        vr = violated_inactive[0]
        return Cause("vr_inactive", vr_name=vr.name,
                     detail="the grounding validation rule is inactive (disabled)")
    # Nothing violated. Don't guess: if any VR's current formula was non-evaluable,
    # whether it should have fired is indeterminate (the old NotDerivable→drift
    # collapse is fixed here).
    if indeterminate:
        vr = indeterminate[0]
        return Cause("vr_formula_indeterminate", vr_name=vr.name,
                     detail="the VR's current formula could not be evaluated on the "
                            "create payload (it references org-state or unset fields) — "
                            "whether it should have fired is indeterminate; the rule may "
                            "have been edited since generation")
    # An active VR is evaluable and not violated → confirmed drift (the rule was edited
    # so the payload no longer trips it).
    if active_not_violated:
        return Cause("vr_formula_drift",
                     detail="an active VR's current formula is evaluable but not "
                            "violated by the create payload — the rule was edited "
                            "since generation")
    # The residual: no active VR enforces the prohibition (removed / deactivated, and
    # no matching inactive rule). The old code mis-labeled this as drift; closed here,
    # matching S8's `no_active_vr`.
    return Cause("no_active_vr",
                 detail="no active validation rule on the object enforces the "
                        "prohibition (it was removed or deactivated)")


# ---------------------------------------------------------------------------
# rejected_unasserted_reason — other VR fired / platform constraint
# ---------------------------------------------------------------------------

def _attribute_unasserted(create: CreateAttemptEvidence, vrs) -> Cause:
    errors = [e for e in create.rejection_body if isinstance(e, dict)]
    vr_msgs = [e.get("message") for e in errors if e.get("errorCode") == _VR_CODE]
    if vr_msgs:
        matched = _match_vr_by_message(vr_msgs, vrs)
        return Cause("other_vr_fired", vr_name=(matched.name if matched else None),
                     detail=f"a different validation rule rejected the create: {vr_msgs}")
    codes = [e.get("errorCode") for e in errors]
    return Cause("platform_constraint",
                 detail=f"a platform constraint (not a validation rule) rejected "
                        f"the create: {codes}")


def _match_vr_by_message(messages, vrs) -> Optional[VrMeta]:
    for msg in messages:
        if not msg:
            continue
        for vr in vrs:
            if vr.error_message and vr.error_message.strip() == msg.strip():
                return vr
    return None


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def _create_step(evidence: RunEvidence):
    for s in evidence.steps:
        if isinstance(s, CreateAttemptEvidence):
            return s
    return None


def _prose(cause: Cause) -> str:
    where = f" (VR {cause.vr_name})" if cause.vr_name else ""
    return f"Cause: {cause.cause_kind}{where} — {cause.detail}."

"""S6 deeper attribution — the differentiating *why* for failed behavioral
verdicts (SPEC §4 slice 2, D-111.1).

`attribute_run(interpretation, evidence, *, s1) → Interpretation` enriches **only**
the two *failed* behavioral verdicts (`prohibition_not_enforced`,
`rejected_unasserted_reason`) with a structured :class:`Cause`, derived
**deterministically** from S1's validation-rule metadata. Pass-through for every
other verdict. It reads S1 read-only and **never re-judges the outcome** (S4 owns
it) — it only deepens `attribution` and attaches `cause`.

The graded step is the rejection-bearing one: a 2-step negative's update/delete
(D-203 — formulas evaluate against the *effective* state, setup payload +
field_changes; a delete has no state and passes through cause-less), else the
flagged create (D-110.2).

S6 reads S1 through the :class:`S1VrReader` port (D-111.1 / S6-3): the
inter-substrate read-through pattern. Slice 2a defines the **port** (and is
tested with a stub); slice 2b provides the production reader that delegates to
S1's query interface.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Protocol

from primeqa.execution_engine.evidence import (
    CreateAttemptEvidence,
    DeleteAttemptEvidence,
    RunEvidence,
    UpdateAttemptEvidence,
)
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
    # The graded step: the rejection-bearing mutation of a 2-step negative
    # (D-203) when present, else the flagged create (D-110.2).
    step = _mutation_step(evidence) or _create_step(evidence)
    if step is None:
        return interpretation

    vrs = s1.vrs_for_object(step.sobject)
    if interpretation.verdict == "prohibition_not_enforced":
        cause = _attribute_not_enforced(step, vrs, evidence)
    else:
        cause = _attribute_unasserted(step, vrs)
    if cause is None:
        # Delete not-enforced: VRs cannot enforce delete prohibitions, so a
        # formula-derived cause would be fabricated — honest pass-through
        # (D-203; repair.py handles cause-less verdicts with verdict-level
        # defaults).
        return interpretation

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

def _attribute_not_enforced(step, vrs, evidence) -> Optional[Cause]:
    state = _effective_state(step, evidence)
    if state is None:
        # A delete leaves no field state to evaluate a formula against — and
        # VRs cannot enforce delete prohibitions anyway. No fabricated cause.
        return None
    violated_active, violated_inactive, indeterminate = [], [], []
    active_not_violated = False
    for vr in vrs:
        if not vr.formula_text:
            continue
        result = evaluate(parse(vr.formula_text), state)
        if result is True:
            (violated_active if vr.is_active else violated_inactive).append(vr)
        elif isinstance(result, NonEvaluable):
            indeterminate.append(vr)
        elif vr.is_active:
            active_not_violated = True   # active rule, formula evaluable + not violated
        # inactive + not violated → no bucket (an inactive rule doesn't enforce anyway).

    op = step.kind
    # A confirmed violation wins (it fixes the loosened-still-violating false-drift:
    # `99` violates a current `Amount < 200`, so this is an enforcement gap, not drift).
    if violated_active:
        vr = violated_active[0]
        return Cause("enforcement_gap", vr_name=vr.name,
                     detail=f"the VR is active and its current formula is violated by "
                            f"the {op} payload, yet the {op} succeeded — a real "
                            f"enforcement gap")
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
                     detail=f"the VR's current formula could not be evaluated on the "
                            f"{op} payload (it references org-state or unset fields) — "
                            f"whether it should have fired is indeterminate; the rule may "
                            f"have been edited since generation")
    # An active VR is evaluable and not violated → confirmed drift (the rule was edited
    # so the payload no longer trips it).
    if active_not_violated:
        return Cause("vr_formula_drift",
                     detail=f"an active VR's current formula is evaluable but not "
                            f"violated by the {op} payload — the rule was edited "
                            f"since generation")
    # The residual: no active VR enforces the prohibition (removed / deactivated, and
    # no matching inactive rule). The old code mis-labeled this as drift; closed here,
    # matching S8's `no_active_vr`.
    return Cause("no_active_vr",
                 detail="no active validation rule on the object enforces the "
                        "prohibition (it was removed or deactivated)")


# ---------------------------------------------------------------------------
# rejected_unasserted_reason — other VR fired / platform constraint
# ---------------------------------------------------------------------------

# D-225: the SF access/FLS rejection codes — attributed DELIBERATELY (the
# cause detail names the codes + blocked fields) instead of the generic
# platform-constraint wording. cause_kind stays platform_constraint (no
# vocabulary change; clustering unaffected).
_ACCESS_ERROR_CODES = frozenset({
    "INSUFFICIENT_FIELD_ACCESS",
    "INSUFFICIENT_ACCESS_OR_READONLY",
    "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY",
    "INVALID_FIELD_FOR_INSERT_UPDATE",
})


def _attribute_unasserted(step, vrs) -> Cause:
    op = step.kind
    errors = [e for e in step.rejection_body if isinstance(e, dict)]
    vr_msgs = [e.get("message") for e in errors if e.get("errorCode") == _VR_CODE]
    if vr_msgs:
        matched = _match_vr_by_message(vr_msgs, vrs)
        return Cause("other_vr_fired", vr_name=(matched.name if matched else None),
                     detail=f"a different validation rule rejected the {op}: {vr_msgs}")
    codes = [e.get("errorCode") for e in errors]
    access = [c for c in codes if c in _ACCESS_ERROR_CODES]
    if access:
        fields: list = []
        for e in errors:
            for f in (e.get("fields") or ()):
                if f and f not in fields:
                    fields.append(f)
        on = f" on field(s) {', '.join(fields)}" if fields else ""
        return Cause("platform_constraint",
                     detail=f"access denied ({', '.join(access)}){on} — the "
                            f"integration user lacks the permission the {op} "
                            f"needs (FLS / object access), not a validation rule")
    return Cause("platform_constraint",
                 detail=f"a platform constraint (not a validation rule) rejected "
                        f"the {op}: {codes}")


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


def _mutation_step(evidence: RunEvidence):
    """The rejection-bearing update/delete attempt of a 2-step negative
    (D-203), if present."""
    for s in evidence.steps:
        if isinstance(s, (UpdateAttemptEvidence, DeleteAttemptEvidence)):
            return s
    return None


def _effective_state(step, evidence: RunEvidence) -> Optional[dict]:
    """The field state a VR formula is evaluated against (D-203):

      - create: the attempted payload as-is;
      - update: the setup create's posted payload overlaid with the attempted
        ``field_changes`` — the record state the org evaluated at update time
        (both are bare-named posted payloads, matching formula field names);
      - delete: ``None`` — no field state; the caller passes through.
    """
    if isinstance(step, CreateAttemptEvidence):
        return step.field_values
    if isinstance(step, UpdateAttemptEvidence):
        setup = _create_step(evidence)
        state = dict(setup.field_values) if setup is not None else {}
        state.update(step.field_changes)
        return state
    return None


def _prose(cause: Cause) -> str:
    where = f" (VR {cause.vr_name})" if cause.vr_name else ""
    return f"Cause: {cause.cause_kind}{where} — {cause.detail}."

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
from primeqa.semantic.formula import parse
from primeqa.generation.verified_negative import VerifiedNegative, derive

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
# prohibition_not_enforced — (a) inactive / (b) drift / (c) enforcement gap
# ---------------------------------------------------------------------------

def _attribute_not_enforced(create: CreateAttemptEvidence, vrs) -> Cause:
    matching_active, matching_inactive = [], []
    for vr in vrs:
        if not vr.formula_text:
            continue
        d = derive(parse(vr.formula_text))
        if isinstance(d, VerifiedNegative) and _payload_violates(
                d.violating_payload, create.field_values):
            (matching_active if vr.is_active else matching_inactive).append(vr)

    if matching_active:
        vr = matching_active[0]
        return Cause("enforcement_gap", vr_name=vr.name,
                     detail="the VR is active and its condition was violated, yet "
                            "the create succeeded — a real enforcement gap")
    if matching_inactive:
        vr = matching_inactive[0]
        return Cause("vr_inactive", vr_name=vr.name,
                     detail="the grounding validation rule is inactive (disabled)")
    return Cause("vr_formula_drift",
                 detail="no active VR's current formula is violated by the create "
                        "payload — the rule was likely edited since generation")


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

def _payload_violates(derived: dict, field_values: dict) -> bool:
    """Whether the create's ``field_values`` carry the VR's derived violating
    assignment (subset match — robust to extra required-field population)."""
    return all(k in field_values and field_values[k] == v
               for k, v in derived.items())


def _create_step(evidence: RunEvidence):
    for s in evidence.steps:
        if isinstance(s, CreateAttemptEvidence):
            return s
    return None


def _prose(cause: Cause) -> str:
    where = f" (VR {cause.vr_name})" if cause.vr_name else ""
    return f"Cause: {cause.cause_kind}{where} — {cause.detail}."

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
    DataReadEvidence,
    DeleteAttemptEvidence,
    RunEvidence,
    UpdateAttemptEvidence,
)
from primeqa.interpretation.model import Cause, Interpretation
from primeqa.semantic.formula import FieldRef, NonEvaluable, evaluate, parse, walk

# The generic validation-rule rejection code (S3 / emission.py — the D-101.2
# honest floor). A rejection carrying it is a VR firing; anything else is a
# platform constraint.
_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"

# The failed BEHAVIORAL-NEGATIVE verdicts attributed from S1's VR metadata.
_NEGATIVE_ENRICHED = ("prohibition_not_enforced", "rejected_unasserted_reason")
# D-229: the failed POSITIVE-vertical verdicts — automation/state attributed
# from S1 Flow metadata, value-claim from S1 field-CRUD metadata.
_POSITIVE_ENRICHED = (
    "automation_not_triggered", "state_not_transitioned", "value_not_persisted")


@dataclass(frozen=True)
class VrMeta:
    """The slice of a validation rule's S1 metadata S6 needs for attribution."""

    name: str
    is_active: bool
    formula_text: Optional[str] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class FlowMeta:
    """The slice of a Flow's S1 metadata S6 needs for positive-vertical
    attribution (D-229): a Flow that `TRIGGERS_ON` the subject + its active
    state. A deactivated grounding Flow is the high-value `automation_inactive`
    cause (the dogfood P1 capture)."""

    name: str
    is_active: bool


@dataclass(frozen=True)
class FieldMeta:
    """The slice of a Field's S1 metadata S6 needs for `value_not_persisted`
    attribution (D-229): is the asserted field createable? SF silently drops a
    non-createable field on insert, so the posted value cannot persist."""

    name: str
    is_createable: bool


class S1AttributionReader(Protocol):
    """Read-port for the S1 metadata S6 attribution needs (D-111.1 / D-229).

    Production delegates to S1's query interface; tests inject a stub. The
    negative path uses ``vrs_for_object`` only; the positive path (D-229) adds
    ``flows_for_object`` (Flows that `TRIGGERS_ON` the subject) and
    ``field_meta`` (a single field's CRUD slice). Structural/duck-typed — a
    reader satisfying only the methods a given verdict needs is sufficient."""

    def vrs_for_object(self, subject_external_id: str) -> tuple[VrMeta, ...]:
        ...

    def flows_for_object(self, subject_external_id: str) -> tuple[FlowMeta, ...]:
        ...

    def field_meta(
        self, object_external_id: str, field_external_id: str,
    ) -> Optional[FieldMeta]:
        ...


# Back-compat alias: the original narrow port name (negative path only).
S1VrReader = S1AttributionReader


def attribute_run(
    interpretation: Interpretation, evidence: RunEvidence, *,
    s1: S1AttributionReader,
) -> Interpretation:
    """Enrich a FAILED behavioral interpretation with a structured cause from
    S1. Negative verdicts read VR metadata; positive-vertical failures (D-229)
    read Flow / field metadata. Pass-through (unchanged) for any other verdict.
    Never mutates the carried outcome. The S1 read self-limits to the verdicts
    that need it (no query for a passing or inspection verdict)."""
    if interpretation.verdict in _NEGATIVE_ENRICHED:
        cause = _attribute_negative(interpretation.verdict, evidence, s1)
    elif interpretation.verdict in _POSITIVE_ENRICHED:
        cause = _attribute_positive(interpretation.verdict, evidence, s1)
    else:
        return interpretation
    if cause is None:
        # Honest pass-through — no S1 signal yields the cause (e.g. a delete
        # not-enforced, a value-claim with no read step, repair.py's
        # verdict-level defaults cover cause=None).
        return interpretation
    return replace(
        interpretation,
        cause=cause,
        attribution=f"{interpretation.attribution} {_prose(cause)}",
    )


def _attribute_negative(verdict, evidence, s1) -> Optional[Cause]:
    # The graded step: the rejection-bearing mutation of a 2-step negative
    # (D-203) when present, else the flagged create (D-110.2).
    step = _mutation_step(evidence) or _create_step(evidence)
    if step is None:
        return None
    vrs = s1.vrs_for_object(step.sobject)
    if verdict == "prohibition_not_enforced":
        return _attribute_not_enforced(step, vrs, evidence)
    return _attribute_unasserted(step, vrs)


# ---------------------------------------------------------------------------
# Positive-vertical failures (D-229) — automation/state (Flow) + value (field)
# ---------------------------------------------------------------------------

def _attribute_positive(verdict, evidence, s1) -> Optional[Cause]:
    """The positive create-and-verify family's failures. The created record is
    the *trigger* (its `sobject`); automation/state grounds on a Flow that
    triggers on it, a value-claim on the createability of the asserted field."""
    create = _create_step(evidence)
    if create is None:
        return None
    if verdict == "value_not_persisted":
        return _attribute_value_not_persisted(create, evidence, s1)
    return _attribute_automation_absent(create, s1)


def _attribute_automation_absent(create, s1) -> Optional[Cause]:
    """`automation_not_triggered` / `state_not_transitioned`: discriminate on
    whether any ACTIVE Flow triggers on the created record's object."""
    flows = s1.flows_for_object(create.sobject)
    active = [f for f in flows if f.is_active]
    if not active:
        return Cause(
            "automation_inactive",
            detail=(f"no active Flow triggers on {create.sobject} — the grounding "
                    f"automation was deactivated or removed since generation, so "
                    f"the asserted effect could not fire"))
    return Cause(
        "automation_effect_absent",
        detail=(f"an active Flow ({active[0].name}) triggers on {create.sobject}, "
                f"but the asserted effect was not observed — an entry condition "
                f"may be unmet, or the Flow's logic changed since generation"))


def _attribute_value_not_persisted(create, evidence, s1) -> Optional[Cause]:
    """`value_not_persisted`: if an asserted (read-back) field on the created
    object is not createable in current S1, SF dropped the posted value on
    insert. Else honest pass-through — a value not persisting has many causes
    S1 cannot determine (a before-save automation overwrote it, a default)."""
    read = _data_read_step(evidence)
    for field in (read.fields_captured if read is not None else ()):
        meta = s1.field_meta(create.sobject, field)
        if meta is not None and not meta.is_createable:
            return Cause(
                "field_not_createable",
                detail=(f"the field {field} on {create.sobject} is not createable — "
                        f"Salesforce silently dropped the posted value on insert, so "
                        f"it could not persist"))
    return None


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
    # Finding 2 (D-229): only VRs whose CURRENT formula references >=1 field
    # present in the payload are *relevant* to THIS claim's failure. An
    # unrelated rule (e.g. `CloseDate > TODAY()` on an Amount claim) is
    # `NonEvaluable` and would otherwise land in `indeterminate` and OUTRANK
    # the grounding VR's real drift — masking the precise cause. Filtering by
    # field-overlap up front removes that noise from every bucket below.
    payload_fields = set(state.keys())
    relevant = [vr for vr in vrs
                if vr.formula_text
                and (_formula_fields(vr.formula_text) & payload_fields)]
    violated_active, violated_inactive, indeterminate = [], [], []
    active_not_violated = False
    for vr in relevant:
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

def _formula_fields(formula_text: str) -> set:
    """The set of BARE field names a VR formula references (Finding 2, D-229) —
    the relevance signal that a rule concerns this claim's single-object
    payload. Dotted refs (`Account.Industry`, cross-object) and unparseable
    formulas contribute nothing: neither can match a bare-named payload key, so
    the rule is treated as irrelevant rather than guessed-at."""
    try:
        ast = parse(formula_text)
    except Exception:
        return set()
    return {n.path[0] for n in walk(ast)
            if isinstance(n, FieldRef) and len(n.path) == 1}


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


def _data_read_step(evidence: RunEvidence):
    """The positive vertical's data read-back (D-115/D-229) — carries the
    asserted ``fields_captured``. Distinct from the metadata ``ReadEvidence``."""
    for s in evidence.steps:
        if isinstance(s, DataReadEvidence):
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

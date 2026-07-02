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
from primeqa.semantic.formula import NonEvaluable, evaluate, parse

import re

# Lenient field extraction (D-229, review #3): a VR's relevance signal must work
# even when the WHOLE formula does not parse (e.g. `CloseDate > TODAY()` — TODAY
# is not a recognized function, so the AST is NotParsed and an AST walk yields no
# fields). Strip string literals, then take identifier tokens NOT immediately
# followed by `(` (those are function names), keeping each token's bare last
# dotted segment so a self-object ref `Opportunity.Amount` matches payload key
# `Amount`. Imprecise by design — over-inclusion only risks KEEPING a VR as
# indeterminate (the safe direction), never dropping a relevant one.
_STRLIT_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_IDENT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*(\()?")
_FORMULA_KEYWORDS = frozenset({"TRUE", "FALSE", "NULL", "AND", "OR", "NOT"})

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
# D-306: the refused-mutation verdicts on POSITIVE claims (a rejected
# acceptance create/update, or a rejected staging create under
# expect_acceptance) — the same VR-message matching names WHICH rule refused.
_ACCEPTANCE_ENRICHED = ("creation_rejected", "change_rejected")


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
    cause (the dogfood P1 capture). ``trigger_type`` (D-241: BeforeSave /
    AfterSave / None) distinguishes a before-save Flow that can overwrite a
    posted value on insert from an after-save one that cannot."""

    name: str
    is_active: bool
    trigger_type: Optional[str] = None


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
    elif interpretation.verdict in _ACCEPTANCE_ENRICHED:
        cause = _attribute_acceptance_rejected(evidence, s1)
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


def _attribute_acceptance_rejected(evidence, s1) -> Optional[Cause]:
    """`creation_rejected` / `change_rejected` (D-306): the refused mutation of
    a POSITIVE claim — the same VR-message matching as the negative's
    unasserted path names WHICH rule refused (high-value: it points a human
    straight at the gating rule), worded without the 'different rule'
    presumption (no rule was asserted here)."""
    step = _mutation_step(evidence) or _create_step(evidence)
    if step is None:
        return None
    vrs = s1.vrs_for_object(step.sobject)
    return _attribute_unasserted(step, vrs, refused=True)


# ---------------------------------------------------------------------------
# Positive-vertical failures (D-229) — automation/state (Flow) + value (field)
# ---------------------------------------------------------------------------

def _attribute_positive(verdict, evidence, s1) -> Optional[Cause]:
    """The positive create-and-verify family's failures (incl. D-205 N-create
    chains). automation/state grounds on the Flow that triggers on the TRIGGER
    record (the LAST create — per D-205 forward-ref ordering the trigger is
    created after the record it acts on); value-claim grounds on the
    createability of the asserted field on the READ-BACK object."""
    if verdict == "value_not_persisted":
        return _attribute_value_not_persisted(evidence, s1)
    trigger = _last_create_step(evidence)
    if trigger is None:
        return None
    return _attribute_automation_absent(trigger, s1)


def _attribute_automation_absent(trigger, s1) -> Optional[Cause]:
    """`automation_not_triggered` / `state_not_transitioned`: discriminate on
    whether any ACTIVE Flow triggers on the TRIGGER record's object."""
    flows = s1.flows_for_object(trigger.sobject)
    active = [f for f in flows if f.is_active]
    if not active:
        return Cause(
            "automation_inactive",
            detail=(f"no active Flow triggers on {trigger.sobject} — the grounding "
                    f"automation was deactivated or removed since generation, so "
                    f"the asserted effect could not fire"))
    return Cause(
        "automation_effect_absent",
        detail=(f"an active Flow ({active[0].name}) triggers on {trigger.sobject}, "
                f"but the asserted effect was not observed — an entry condition "
                f"may be unmet, or the Flow's logic changed since generation"))


def _attribute_value_not_persisted(evidence, s1) -> Optional[Cause]:
    """`value_not_persisted`, keyed off the READ's object (where the value should
    have persisted) — robust to multi-create chains. Two structured causes:

    1. `field_not_createable` (D-229) — an asserted field is not createable in
       current S1, so SF silently dropped the posted value on insert.
    2. `before_save_automation_overwrote` (D-241) — every asserted field IS
       createable, yet the value didn't persist, and an ACTIVE before-save Flow
       triggers on the object: it runs before insert and is the canonical
       overwrite mechanism.

    Else honest pass-through (None) — a field default (S1 doesn't capture
    defaults yet) or a cause S1 can't see."""
    read = _data_read_step(evidence)
    if read is None:
        return None
    for field in read.fields_captured:
        meta = s1.field_meta(read.sobject, field)
        if meta is not None and not meta.is_createable:
            return Cause(
                "field_not_createable",
                detail=(f"the field {field} on {read.sobject} is not createable — "
                        f"Salesforce silently dropped the posted value on insert, so "
                        f"it could not persist"))
    # D-241: every asserted field is createable — a before-save Flow on the
    # object is the canonical "overwrote the posted value" mechanism.
    before_save = [f for f in s1.flows_for_object(read.sobject)
                   if f.is_active and (f.trigger_type or "").lower() == "beforesave"]
    if before_save:
        return Cause(
            "before_save_automation_overwrote",
            detail=(f"an active before-save Flow ({before_save[0].name}) triggers on "
                    f"{read.sobject} — it runs before insert and likely overwrote the "
                    f"posted value, so it did not persist as asserted"))
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
    # Finding 2 (D-229): bucket on the EVALUATION RESULT, and apply the
    # relevance filter ONLY to the `indeterminate` (NonEvaluable) bucket — that
    # is the sole place an unrelated rule can mask a real cause. A VR whose
    # formula evaluates True (violation) or evaluable-False (drift) is relevant
    # BY CONSTRUCTION — it touched the payload enough to be evaluated — and must
    # never be dropped (the bare-field-overlap proxy diverges from evaluate()'s
    # own field-presence semantics, e.g. ISBLANK fires on an ABSENT field).
    payload_fields = set(state.keys())
    violated_active, violated_inactive, indeterminate = [], [], []
    active_not_violated = False
    for vr in vrs:
        if not vr.formula_text:
            continue
        result = evaluate(parse(vr.formula_text), state)
        if result is True:
            (violated_active if vr.is_active else violated_inactive).append(vr)
        elif isinstance(result, NonEvaluable):
            # A NonEvaluable VR is *indeterminate for THIS claim* only if it
            # could concern the payload. Drop it ONLY when PROVABLY irrelevant:
            # its formula names bare fields and NONE is in the payload (the P3
            # masking shape — `CloseDate > TODAY()` on an Amount claim). A
            # NonEvaluable formula with no extractable bare fields (dotted /
            # cross-object / unparseable) stays indeterminate — we cannot prove
            # irrelevance, and dropping it would fabricate a cause from the rest.
            fields = _formula_fields(vr.formula_text)
            if fields and not (fields & payload_fields):
                continue                     # provably irrelevant — drop
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


def _attribute_unasserted(step, vrs, *, refused: bool = False) -> Cause:
    op = step.kind
    errors = [e for e in step.rejection_body if isinstance(e, dict)]
    vr_msgs = [e.get("message") for e in errors if e.get("errorCode") == _VR_CODE]
    if vr_msgs:
        matched = _match_vr_by_message(vr_msgs, vrs)
        # D-306 `refused`: on a positive claim no rule was asserted, so the
        # "different rule" wording would be false — a rule simply refused it.
        which = "a" if refused else "a different"
        return Cause("other_vr_fired", vr_name=(matched.name if matched else None),
                     detail=f"{which} validation rule rejected the {op}: {vr_msgs}")
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
    """The set of bare field names a VR formula references (Finding 2, D-229) —
    the relevance signal that a rule concerns this claim's single-object
    payload. Lenient + parse-independent (review #3): the masking rule
    `CloseDate > TODAY()` is NotParsed (TODAY is unrecognized), so an AST walk
    yields nothing and the noise would slip through. Strip string literals, take
    identifier tokens NOT followed by `(` (function names), drop formula
    keywords, and keep each token's bare last dotted segment so a self-object
    ref (`Opportunity.Amount`) matches payload key `Amount`."""
    text = _STRLIT_RE.sub(" ", formula_text)
    out = set()
    for m in _IDENT_RE.finditer(text):
        ident, is_call = m.group(1), m.group(2)
        if is_call:                          # a function call — not a field
            continue
        bare = ident.split(".")[-1]
        if not bare or bare[:1].isdigit() or bare.upper() in _FORMULA_KEYWORDS:
            continue
        out.add(bare)
    return out


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


def _last_create_step(evidence: RunEvidence):
    """The LAST create attempt — the TRIGGER record of a positive automation /
    state run (D-205/D-229). A 2-create chain creates the observed record first,
    then the trigger that acts on it (forward-ref order), so the Flow's trigger
    object is the last create's ``sobject``; a single-create run's last == first."""
    last = None
    for s in evidence.steps:
        if isinstance(s, CreateAttemptEvidence):
            last = s
    return last


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

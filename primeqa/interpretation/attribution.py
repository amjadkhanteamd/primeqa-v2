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
from datetime import date
from typing import Optional, Protocol

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CreateAttemptEvidence,
    DataReadEvidence,
    DeleteAttemptEvidence,
    RunEvidence,
    UpdateAttemptEvidence,
)
from primeqa.interpretation.model import Cause, Interpretation
from primeqa.semantic.formula import (
    EvalContext, NonEvaluable, evaluate, parse)

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
# D-427: the ABSENCE MIRROR — the D-307 absence claim's red (a correlated
# record observed where the claim asserts none should exist). Its own
# dispatch: NEVER routed into _attribute_automation_absent, whose entire
# vocabulary asserts a MISSING effect and is factually false here.
_ABSENCE_ENRICHED = ("automation_fired_unexpectedly",)


@dataclass(frozen=True)
class VrMeta:
    """The slice of a validation rule's S1 metadata S6 needs for attribution.

    D-382 (SUB-4): ``is_active`` is ``Optional`` — ``None`` means the detail
    row is MISSING in S1 and the state is genuinely unknown. Consumers compare
    ``is True`` / ``is False`` explicitly; truthiness on ``None`` fabricated
    an inactive state (the invented-metadata defect)."""

    name: str
    is_active: Optional[bool]
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
    is_active: Optional[bool]          # D-382: None = unknown, never invented
    trigger_type: Optional[str] = None


@dataclass(frozen=True)
class FieldMeta:
    """The slice of a Field's S1 metadata S6 needs for `value_not_persisted`
    attribution (D-229): is the asserted field createable? SF silently drops a
    non-createable field on insert, so the posted value cannot persist."""

    name: str
    is_createable: Optional[bool]      # D-382: None = unknown, never invented


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
    s1: S1AttributionReader, claim_automation: Optional[dict] = None,
) -> Interpretation:
    """Enrich a FAILED behavioral interpretation with a structured cause from
    S1. Negative verdicts read VR metadata; positive-vertical failures (D-229)
    read Flow / field metadata. Pass-through (unchanged) for any other verdict.
    Never mutates the carried outcome. The S1 read self-limits to the verdicts
    that need it (no query for a passing or inspection verdict).

    ``claim_automation`` (``{name, primitive}`` from the automation-effect
    claim body, or None) grounds the automation-absent cause on the automation
    the CLAIM binds — without it the attribution can only scan S1 and cannot
    know WHICH automation the assertion is about."""
    if interpretation.verdict in _NEGATIVE_ENRICHED:
        cause = _attribute_negative(interpretation.verdict, evidence, s1)
    elif interpretation.verdict in _POSITIVE_ENRICHED:
        cause = _attribute_positive(interpretation.verdict, evidence, s1,
                                    claim_automation=claim_automation)
    elif interpretation.verdict in _ACCEPTANCE_ENRICHED:
        cause = _attribute_acceptance_rejected(evidence, s1)
    elif interpretation.verdict in _ABSENCE_ENRICHED:
        cause = _attribute_unexpected_presence(evidence, s1,
                                               claim_automation=claim_automation)
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
        return _attribute_not_enforced(
            step, vrs, evidence, ctx=_eval_context(step, evidence, s1))
    return _attribute_unasserted(step, vrs)


def _eval_context(step, evidence: RunEvidence, s1) -> EvalContext:
    """The D-439 evaluation context for the graded step.

    * ORG-STATE pair: graded create → ``is_create=True`` (no prior state);
      graded update → ``is_create=False`` + the ORDERED-FOLD prior state
      (D-448): the same-record setup create's posted payload folded with
      every SUCCEEDED same-record mutation that precedes the graded step,
      in evidence order — a rejected intermediate changed nothing in the
      org and contributes nothing. Where the fold cannot be sound (no or
      multiple candidate setup creates, a preceding delete, an
      unknown-outcome intermediate) it refuses and the org-state functions
      stay NonEvaluable — the D-441 guard surviving for exactly the cases
      the fold cannot resolve.
    * RecordType resolver: taken from the S1 reader when it offers one
      (duck-typed — a narrow test stub without the method simply leaves the
      RecordType family NonEvaluable).
    """
    run_date, run_fb = _run_clock(evidence)
    resolver = getattr(s1, "record_type_developer_name", None)
    # D-442: bare-field -> S1 field_type, memoized per attribution (feeds
    # the ISNULL nullable-type guard + the percent fraction conversion).
    type_reader = getattr(s1, "field_type", None)
    field_type_of = None
    if type_reader is not None and getattr(step, "sobject", None):
        _cache: dict = {}
        def field_type_of(field, _sobj=step.sobject):    # noqa: E306
            if field not in _cache:
                try:
                    _cache[field] = type_reader(_sobj, field)
                except Exception:                        # noqa: BLE001
                    _cache[field] = None                 # unknown, never raise
            return _cache[field]
    if isinstance(step, CreateAttemptEvidence):
        return EvalContext(is_create=True,
                           record_type_developer_name=resolver,
                           field_type_of=field_type_of,
                           run_date=run_date,
                           run_date_is_fallback=run_fb)
    if isinstance(step, UpdateAttemptEvidence):
        prior, refusal = _ordered_fold(evidence, step)
        if refusal is not None or prior is None:
            return EvalContext(record_type_developer_name=resolver,
                               field_type_of=field_type_of,
                           run_date=run_date,
                           run_date_is_fallback=run_fb)
        return EvalContext(prior_state=prior,
                           is_create=False,
                           record_type_developer_name=resolver,
                           field_type_of=field_type_of,
                           run_date=run_date,
                           run_date_is_fallback=run_fb)
    return EvalContext(record_type_developer_name=resolver,
                       field_type_of=field_type_of,
                           run_date=run_date,
                           run_date_is_fallback=run_fb)


def _run_clock(evidence: RunEvidence):
    """D-450: the TODAY evaluation clock — the run's PERSISTED reference
    date (the D-449 envelope), else evidence ``started_at``'s date flagged
    as a fallback (pre-D-449 rows). Never the wall clock: attribution time
    must not move a verdict."""
    ref = getattr(evidence, "temporal_reference", None)
    if isinstance(ref, dict) and ref.get("reference_date"):
        try:
            return date.fromisoformat(str(ref["reference_date"])), False
        except ValueError:
            pass
    started = getattr(evidence, "started_at", None)
    if started is not None:
        return started.date(), True
    return None, False


def _staged_values(step) -> dict:
    """D-450: the CREATE state the org evaluated — the realized transport
    payload where captured (D-449), else the symbolic field_values."""
    return getattr(step, "field_values_realized", None) or step.field_values


def _staged_changes(step) -> dict:
    """D-450: the UPDATE changes the org evaluated — realized where
    captured, else the symbolic field_changes."""
    return (getattr(step, "field_changes_realized", None)
            or step.field_changes)


def _ordered_fold(evidence: RunEvidence, graded):
    """D-448: ``(prior_state, refusal_reason)`` for a graded UPDATE — the
    same-record setup create's payload folded with every SUCCEEDED
    same-record mutation that PRECEDES the graded step, in evidence order.
    Deterministic over the evidence tuple; no timestamps, no guessing.

    Steps pair by ``record_id`` when both sides carry one, else by
    ``sobject``. A REJECTED intermediate (clean rejection, no transport
    error) changed nothing in the org and is SKIPPED — pinned. Refusals
    (``prior_state=None`` + the reason) are the D-441 guard's survivors:
    the graded step missing from the evidence, no or multiple candidate
    setup creates, a successfully-deleted subject, and any preceding
    same-record mutation whose org effect is unknown (a transport error —
    the request may or may not have applied)."""
    steps = list(evidence.steps)
    gi = next((i for i, s in enumerate(steps) if s is graded), None)
    if gi is None:
        return None, "graded step not present in the evidence steps"

    def _same_record(s):
        rid_g = getattr(graded, "record_id", None)
        rid_s = getattr(s, "record_id", None)
        if isinstance(s, CreateAttemptEvidence):
            rid_s = getattr(getattr(s, "cleanup", None), "record_id", None) \
                or rid_s
        if rid_g and rid_s:
            return rid_g == rid_s
        return getattr(s, "sobject", None) == graded.sobject

    creates = [s for s in steps[:gi]
               if isinstance(s, CreateAttemptEvidence) and _same_record(s)]
    if not creates:
        return None, "no same-record setup create precedes the graded step"
    if len(creates) > 1:
        return None, ("multiple candidate setup creates precede the graded "
                      "step — the fold's base record is ambiguous")
    base = creates[0]
    state = dict(_staged_values(base))
    for s in steps[steps.index(base) + 1:gi]:
        if isinstance(s, DeleteAttemptEvidence) and _same_record(s):
            if getattr(s, "error", None) is not None:
                return None, ("a preceding same-record delete has an "
                              "unknown org effect (transport error)")
            if s.success:
                return None, ("a preceding delete removed the record the "
                              "fold describes")
            continue                     # rejected delete: org unchanged
        if not isinstance(s, UpdateAttemptEvidence) or not _same_record(s):
            continue
        if getattr(s, "error", None) is not None:
            return None, ("a preceding same-record mutation has an unknown "
                          "org effect (transport error)")
        if s.success is True:
            state.update(_staged_changes(s))
        elif s.success is False:
            continue      # rejected: the org state did not change (pinned)
        else:
            return None, ("a preceding same-record mutation has an unknown "
                          "outcome")
    return state, None


def _attribute_acceptance_rejected(evidence, s1) -> Optional[Cause]:
    """`creation_rejected` / `change_rejected` (D-306): the refused mutation of
    a POSITIVE claim — the same VR-message matching as the negative's
    unasserted path names WHICH rule refused (high-value: it points a human
    straight at the gating rule), worded without the 'different rule'
    presumption (no rule was asserted here). Attributes ONLY a step that
    actually FAILED (D-306.1, review): selecting by kind alone picked a
    SUCCEEDED mutation on other shapes and fabricated 'a platform constraint
    rejected the update: []' over a 2xx — honest pass-through instead."""
    step = next(
        (s for s in evidence.steps
         if isinstance(s, (UpdateAttemptEvidence, DeleteAttemptEvidence,
                           CreateAttemptEvidence))
         and not s.success), None)
    if step is None:
        return None
    vrs = s1.vrs_for_object(step.sobject)
    return _attribute_unasserted(step, vrs, refused=True)


# ---------------------------------------------------------------------------
# Positive-vertical failures (D-229) — automation/state (Flow) + value (field)
# ---------------------------------------------------------------------------

def _attribute_positive(verdict, evidence, s1,
                        claim_automation=None) -> Optional[Cause]:
    """The positive create-and-verify family's failures (incl. D-205 N-create
    chains). automation/state grounds on the automation the CLAIM binds when
    the body carries one, else on the Flow(s) that trigger on the TRIGGER
    record (the LAST create — per D-205 forward-ref ordering the trigger is
    created after the record it acts on); value-claim grounds on the
    createability of the asserted field on the READ-BACK object."""
    if verdict == "value_not_persisted":
        return _attribute_value_not_persisted(evidence, s1)
    trigger = _last_create_step(evidence)
    if trigger is None:
        return None
    # D-425: the assert step's value envelope, when present, splits the
    # automation_effect_absent hedge; None (pre-D-424 evidence) leaves every
    # cause exactly as before.
    observation = _classify_effect_observation(evidence)
    return _attribute_automation_absent(trigger, s1, claim_automation,
                                        observation=observation)


# ---------------------------------------------------------------------------
# D-425 — the value-aware effect observation (reads the D-424 assert envelope)
# ---------------------------------------------------------------------------

# A Salesforce record Id: 15 or 18 alphanumeric characters. Deliberately
# narrow (D-425): a text field legitimately holding a pure-15/18-alphanumeric
# value could collide, in which case the safe fallback is the divergent cause
# — refinement, never fabrication.
_SF_ID_RE = re.compile(r"^[a-zA-Z0-9]{15}(?:[a-zA-Z0-9]{3})?$")


@dataclass(frozen=True)
class _EffectObservation:
    """What the graded assert's D-424 value envelope shows about the asserted
    effect — the discriminator that splits ``automation_effect_absent``
    (D-425). Produced by :func:`_classify_effect_observation`; ``None`` means
    the evidence predates D-424 (no envelope), and attribution behaves
    exactly as before — the absence law."""

    kind: str          # record_absent | value_absent | divergent | representation_mismatch
    asserted_field: Optional[str]
    asserted_value: object
    observed_value: object


def _classify_effect_observation(evidence) -> Optional[_EffectObservation]:
    """Classify a FAILED positive-vertical run's assert envelope (D-425).

    - ``no_row`` (and its ``exists``-predicate twin: ``row_count`` observed 0)
      → the effect RECORD was never produced. The producer guarantees 0-row
      equality reads occur only on side-effect reads (D-227.7), so "never
      produced" never misdescribes a vanished subject record.
    - ``field_value`` observed None → the effect VALUE is absent. STILL
      ambiguous between never-written and written-blank — the producer's
      ``rows[0].get(field)`` returns None identically for JSON-null and
      absent-key, and a true null is reachable by both. Never a firing claim.
    - ``field_value`` observed non-null ≠ asserted → a DIVERGENT value;
      Id-shaped observed vs non-Id-shaped asserted string → the
      representation-mismatch claim-authoring class (the 0d81c6f9 specimen).
    - No assert step / no envelope → ``None`` (pre-D-424 — unchanged hedge).
    """
    a = next((s for s in evidence.steps if isinstance(s, AssertEvidence)), None)
    if a is None or a.observed_kind is None:
        return None
    if a.observed_kind == "no_row":
        return _EffectObservation(
            "record_absent", a.asserted_field, a.asserted_value, None)
    if a.observed_kind == "row_count":
        if a.predicate == "exists" and not a.observed_value:
            return _EffectObservation(
                "record_absent", a.asserted_field, a.asserted_value, 0)
        if a.predicate == "count_equals":
            return _EffectObservation(
                "divergent", a.asserted_field, a.asserted_value,
                a.observed_value)
        return None
    if a.observed_kind == "field_value":
        if a.observed_value is None:
            return _EffectObservation(
                "value_absent", a.asserted_field, a.asserted_value, None)
        if (isinstance(a.observed_value, str)
                and _SF_ID_RE.match(a.observed_value)
                and isinstance(a.asserted_value, str)
                and not _SF_ID_RE.match(a.asserted_value)):
            return _EffectObservation(
                "representation_mismatch", a.asserted_field,
                a.asserted_value, a.observed_value)
        return _EffectObservation(
            "divergent", a.asserted_field, a.asserted_value, a.observed_value)
    return None


def _refined_effect_cause(observation, *, automation_phrase, sobject,
                          other_writers) -> Optional[Cause]:
    """The D-425 value-aware refinement of ``automation_effect_absent``.

    ``None`` when there is no envelope (pre-D-424 evidence) — the caller then
    falls back to the exact pre-D-425 hedge (the absence law). Each refined
    cause states only what the envelope proves: record-absent decides WHAT
    (never WHY — S1 carries no entry criteria); divergent decides THAT a
    different value was written (never WHO — candidate writers are enumerated
    and Apex triggers are named as uncaptured); value-absent stays honest
    about never-written vs written-blank and never claims firing either way.
    """
    if observation is None:
        return None
    field = observation.asserted_field
    asserted = observation.asserted_value
    observed = observation.observed_value
    if observation.kind == "record_absent":
        return Cause(
            "automation_effect_record_absent",
            detail=(f"{automation_phrase}, but the asserted effect record was "
                    f"never produced — the read-back found no row. Why (an "
                    f"entry condition unmet vs the logic changed) is not "
                    f"determinable from the run: S1 does not capture entry "
                    f"criteria"))
    if observation.kind == "value_absent":
        return Cause(
            "automation_effect_value_absent",
            detail=(f"{automation_phrase}; the record was observed but the "
                    f"asserted field {field} holds no value — the asserted "
                    f"effect value is absent. Whether the field was never "
                    f"written or was written blank is not decidable from a "
                    f"single post-state read"))
    if observation.kind == "representation_mismatch":
        return Cause(
            "representation_mismatch",
            detail=(f"the claim asserts {field} equals {asserted!r}, but the "
                    f"org holds the identifier {observed!r} — the asserted "
                    f"value is a human label where the field carries a "
                    f"Salesforce Id. A claim-authoring defect (generation "
                    f"emitted a display label where the field requires an "
                    f"identifier), not org behaviour"))
    # divergent — a different value (or count) was observed.
    what = (f"the asserted count was {asserted!r} but {observed!r} row(s) "
            f"matched" if field is None
            else f"{field} was asserted {asserted!r} but the org holds "
                 f"{observed!r}")
    writers = (f"other active Flows on {sobject}: {other_writers}"
               if other_writers else
               f"no other active Flow is captured on {sobject}")
    return Cause(
        "automation_effect_divergent",
        detail=(f"{automation_phrase}; a different value was observed — "
                f"{what}. Something wrote a value other than the asserted "
                f"one: the grounding automation under different logic, or "
                f"another writer ({writers}; Apex triggers are not captured "
                f"in the org model, so candidate writers cannot be "
                f"exhaustively enumerated)"))


# The primitive's display noun (D-053 sub-discriminators; D-304 formula,
# D-308 approval_process). Unknown/legacy primitives fall back to the
# neutral "automation" — never assert "Flow" for a non-flow mechanism.
_PRIMITIVE_NOUN = {
    "flow": "Flow",
    "process_builder": "process",
    "apex_trigger": "Apex trigger",
    "validation_rule": "validation rule",
    "approval_process": "approval process",
    "formula": "formula field",
}


def _attribute_automation_absent(trigger, s1, claim_automation=None,
                                 observation=None) -> Optional[Cause]:
    """`automation_not_triggered` / `state_not_transitioned`.

    With the claim's automation binding (name + primitive), the cause names
    THAT automation — the pre-fix scan named ``active[0]`` of the object's
    Flows, blaming an unrelated Flow on multi-flow objects (run 4b8cbe84
    blamed HL_Auto_Risk_Rating for an HL_High_Value_Loan approval claim).
    A bound FLOW is additionally checked against S1 for activity (the
    inactive/removed discrimination, now by ITS name). Non-flow primitives
    have no S1 activity port — their cause stays evidence-honest (no
    "active"/"ran" assertion). Without a binding (legacy bodies,
    state-transition claims) the S1 flow-scan survives as the fallback,
    phrased as the scan it is — never presenting one scanned Flow as THE
    grounding automation."""
    if claim_automation and claim_automation.get("name"):
        name = claim_automation["name"]
        primitive = claim_automation.get("primitive")
        noun = _PRIMITIVE_NOUN.get(primitive or "", "automation")
        if primitive == "flow":
            flows = s1.flows_for_object(trigger.sobject)
            match = next((f for f in flows if f.name == name), None)
            if match is None:
                return Cause(
                    "automation_inactive",
                    detail=(f"the Flow {name} this test grounds on no longer "
                            f"triggers on {trigger.sobject} — removed or "
                            f"retargeted since generation, so the asserted "
                            f"effect could not fire"))
            if match.is_active is False:
                return Cause(
                    "automation_inactive",
                    detail=(f"the Flow {name} this test grounds on is inactive "
                            f"— deactivated since generation, so the asserted "
                            f"effect could not fire"))
            if match.is_active is None:
                # D-382 (SUB-4): the deciding metadata is missing — say so
                # instead of fabricating an active/inactive verdict.
                return Cause(
                    "grounding_incomplete",
                    detail=(f"the Flow {name}'s active state is UNKNOWN in "
                            f"the org model (no detail row at this version) "
                            f"— attribution cannot distinguish deactivated "
                            f"from entry-condition-unmet"))
            others = ", ".join(f.name for f in flows
                               if f.is_active is True and f.name != name)
            refined = _refined_effect_cause(
                observation,
                automation_phrase=f"the Flow {name} is active on "
                                  f"{trigger.sobject}",
                sobject=trigger.sobject, other_writers=others)
            if refined is not None:
                return refined
            return Cause(
                "automation_effect_absent",
                detail=(f"the Flow {name} is active on {trigger.sobject}, but "
                        f"the asserted effect was not observed — an entry "
                        f"condition may be unmet, or its logic changed since "
                        f"generation"))
        if primitive == "approval_process":
            refined = _refined_effect_cause(
                observation,
                automation_phrase=f"the {noun} {name} was expected to submit "
                                  f"this record",
                sobject=trigger.sobject, other_writers="")
            if refined is not None:
                return refined
            return Cause(
                "automation_effect_absent",
                detail=(f"the {noun} {name} did not submit this record — no "
                        f"approval request was created; an entry condition of "
                        f"the submitting automation may be unmet, or the "
                        f"process no longer fires on this path"))
        refined = _refined_effect_cause(
            observation,
            automation_phrase=f"the {noun} {name} on {trigger.sobject}",
            sobject=trigger.sobject, other_writers="")
        if refined is not None:
            return refined
        return Cause(
            "automation_effect_absent",
            detail=(f"the {noun} {name} did not produce its asserted effect on "
                    f"{trigger.sobject} — an entry condition may be unmet, or "
                    f"its logic changed since generation"))
    flows = s1.flows_for_object(trigger.sobject)
    active = [f for f in flows if f.is_active is True]
    unknown = [f for f in flows if f.is_active is None]
    if not active and unknown:
        # D-382 (SUB-4): no provably-active flow, but some have UNKNOWN
        # state — "all inactive" would be fabricated.
        return Cause(
            "grounding_incomplete",
            detail=(f"no provably-active Flow triggers on {trigger.sobject} "
                    f"and {len(unknown)} flow(s) have UNKNOWN active state "
                    f"(missing detail rows) — attribution cannot conclude "
                    f"the automation was deactivated"))
    if not active:
        return Cause(
            "automation_inactive",
            detail=(f"no active Flow triggers on {trigger.sobject} — the grounding "
                    f"automation was deactivated or removed since generation, so "
                    f"the asserted effect could not fire"))
    names = ", ".join(f.name for f in active[:3])
    more = "…" if len(active) > 3 else ""
    scan_phrase = (f"active automation exists on {trigger.sobject} "
                   f"({len(active)} active Flow"
                   f"{'s' if len(active) != 1 else ''}: {names}{more})")
    refined = _refined_effect_cause(
        observation, automation_phrase=scan_phrase,
        sobject=trigger.sobject, other_writers=f"{names}{more}")
    if refined is not None:
        return refined
    return Cause(
        "automation_effect_absent",
        detail=(f"{scan_phrase}, but the asserted effect was not observed — "
                f"an entry condition may be unmet, or the logic changed since "
                f"generation"))


def _attribute_unexpected_presence(evidence, s1,
                                   claim_automation=None) -> Optional[Cause]:
    """`automation_fired_unexpectedly` (D-427) — the D-307 absence claim's
    red: the `not_exists` assert did not hold, so a correlated record WAS
    observed where the claim asserts none should exist. Direction-correct by
    construction: this function never words a cause as a missing effect.

    The decidable sub-cause: a bound Flow that is INACTIVE (or no longer
    triggers on the object) cannot have produced the observed record —
    `other_writer_produced_record`, with candidate writers enumerated from S1
    and the standing caveat that Apex triggers are uncaptured, so the
    enumeration cannot be exhaustive. A bound ACTIVE Flow leaves WHO open
    (`automation_effect_record_present` — WHAT is decided, authorship is
    not); UNKNOWN activity is `grounding_incomplete` (D-382 — never
    fabricate).

    Evidence: the verdict itself already implies an observed row; the D-424
    envelope adds the count when present. Pre-envelope evidence still
    enriches — there is no prior behaviour to preserve (the verdict had no
    cause before D-427, and zero corpus occurrences exist)."""
    trigger = _last_create_step(evidence)
    if trigger is None:
        return None
    a = next((s for s in evidence.steps if isinstance(s, AssertEvidence)),
             None)
    n = (a.observed_value if a is not None
         and a.observed_kind == "row_count"
         and isinstance(a.observed_value, int) else None)
    read = _data_read_step(evidence)
    where = (f"on {read.sobject} " if read is not None else "")
    seen = (f"{n} correlated record{'s were' if n != 1 else ' was'} "
            f"observed {where}"
            if n is not None else f"a correlated record was observed {where}")
    apex = ("Apex triggers are not captured in the org model, so candidate "
            "writers cannot be exhaustively enumerated")

    flows = s1.flows_for_object(trigger.sobject)
    name = (claim_automation or {}).get("name")
    primitive = (claim_automation or {}).get("primitive")
    others = ", ".join(f.name for f in flows
                       if f.is_active is True and f.name != name)
    candidates = (f"other active Flows on {trigger.sobject}: {others}"
                  if others else
                  f"no other active Flow is captured on {trigger.sobject}")

    if name and primitive == "flow":
        match = next((f for f in flows if f.name == name), None)
        if match is None:
            return Cause(
                "other_writer_produced_record",
                detail=(f"the Flow {name} this test grounds on no longer "
                        f"triggers on {trigger.sobject} — removed or "
                        f"retargeted — yet {seen}where the claim asserts "
                        f"none should exist. Another writer produced it "
                        f"({candidates}; {apex})"))
        if match.is_active is False:
            return Cause(
                "other_writer_produced_record",
                detail=(f"the Flow {name} is inactive, so it cannot have "
                        f"produced the record — yet {seen}where the claim "
                        f"asserts none should exist. Another writer produced "
                        f"it ({candidates}; {apex})"))
        if match.is_active is None:
            return Cause(
                "grounding_incomplete",
                detail=(f"{seen}where the claim asserts none should exist, "
                        f"but the Flow {name}'s active state is UNKNOWN in "
                        f"the org model (no detail row at this version) — "
                        f"whether {name} could have produced it cannot be "
                        f"determined"))
        return Cause(
            "automation_effect_record_present",
            detail=(f"{seen}where the claim asserts none should exist. The "
                    f"Flow {name} is active — whether {name} fired against "
                    f"the asserted suppression, or another writer produced "
                    f"the record, is not decidable from the run "
                    f"({candidates}; {apex})"))
    if name:
        noun = _PRIMITIVE_NOUN.get(primitive or "", "automation")
        return Cause(
            "automation_effect_record_present",
            detail=(f"{seen}where the claim asserts none should exist. "
                    f"Whether the {noun} {name} produced it, or another "
                    f"writer did, is not decidable from the run ({apex})"))
    # Unbound (legacy bodies): the honest scan, direction-correct.
    active = [f for f in flows if f.is_active is True]
    unknown = [f for f in flows if f.is_active is None]
    if not active and unknown:
        return Cause(
            "grounding_incomplete",
            detail=(f"{seen}where the claim asserts none should exist; no "
                    f"provably-active Flow triggers on {trigger.sobject} and "
                    f"{len(unknown)} flow(s) have UNKNOWN active state — the "
                    f"writer cannot be determined"))
    if not active:
        return Cause(
            "other_writer_produced_record",
            detail=(f"{seen}where the claim asserts none should exist, and "
                    f"no active captured Flow triggers on {trigger.sobject} "
                    f"— the writer is outside the captured automation "
                    f"surface ({apex})"))
    names = ", ".join(f.name for f in active[:3])
    more = "…" if len(active) > 3 else ""
    return Cause(
        "automation_effect_record_present",
        detail=(f"{seen}where the claim asserts none should exist. "
                f"{len(active)} active Flow{'s' if len(active) != 1 else ''} "
                f"trigger{'' if len(active) != 1 else 's'} on "
                f"{trigger.sobject} ({names}{more}) — which of them, or what "
                f"other writer, produced the record is not decidable from "
                f"the run ({apex})"))


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
        # D-382: is_createable None (unknown) never ACCUSES — honest
        # pass-through preserves this function's existing contract.
        if meta is not None and meta.is_createable is False:
            return Cause(
                "field_not_createable",
                detail=(f"the field {field} on {read.sobject} is not createable — "
                        f"Salesforce silently dropped the posted value on insert, so "
                        f"it could not persist"))
    # D-241: every asserted field is createable — a before-save Flow on the
    # object is the canonical "overwrote the posted value" mechanism.
    before_save = [f for f in s1.flows_for_object(read.sobject)
                   if f.is_active is True     # D-382: only provably-active accuse
                   and (f.trigger_type or "").lower() == "beforesave"]
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

def _attribute_not_enforced(step, vrs, evidence, *,
                            ctx: Optional[EvalContext] = None,
                            ) -> Optional[Cause]:
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
        result = evaluate(parse(vr.formula_text), state, context=ctx)
        if result is True:
            # D-382: an UNKNOWN active-state VR whose formula violates is
            # indeterminate — bucketing it "inactive" fabricated a state.
            if vr.is_active is True:
                violated_active.append(vr)
            elif vr.is_active is False:
                violated_inactive.append(vr)
            else:
                indeterminate.append(vr)
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
    """The rejection-GRADED update/delete of a negative (D-203/D-448): the
    step carrying the expectation marker — the executor sets ``matched``
    (True/False) only where an ``expect_rejection`` was graded, while
    acceptance/setup mutations carry ``matched=None`` (measured on the
    3-step specimen, run 495707b6). Last-marked wins; no marked mutation →
    the first mutation, the byte-old 2-step behaviour."""
    marked = [s for s in evidence.steps
              if isinstance(s, (UpdateAttemptEvidence, DeleteAttemptEvidence))
              and s.matched is not None]
    if marked:
        return marked[-1]
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
      - update: the D-448 ordered-fold prior (setup create ⊕ preceding
        SUCCEEDED same-record mutations, in evidence order) overlaid with
        the attempted ``field_changes`` — the record state the org
        evaluated at update time. Where the fold refuses, the pre-D-448
        naive shape (first create ⊕ changes) stands — no new refusal path
        at the value level (documented posture, the org-state pair already
        refuses through ``_eval_context``);
      - delete: ``None`` — no field state; the caller passes through.
    """
    if isinstance(step, CreateAttemptEvidence):
        return _staged_values(step)
    if isinstance(step, UpdateAttemptEvidence):
        prior, _refusal = _ordered_fold(evidence, step)
        if prior is None:
            setup = _create_step(evidence)
            prior = dict(_staged_values(setup)) if setup is not None else {}
        state = dict(prior)
        state.update(_staged_changes(step))
        return state
    return None


def _prose(cause: Cause) -> str:
    where = f" (VR {cause.vr_name})" if cause.vr_name else ""
    return f"Cause: {cause.cause_kind}{where} — {cause.detail}."

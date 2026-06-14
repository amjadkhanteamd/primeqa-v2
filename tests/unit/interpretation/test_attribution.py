"""Unit tests for S6 deeper attribution (D-111.1 slice 2a) — offline, stub
S1VrReader.

`attribute_run` enriches the two failed behavioral verdicts with a structured
`Cause` derived from S1's VR metadata (one per `cause_kind`), pass-through for
the rest, and never mutates the carried outcome.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CleanupRecord,
    CreateAttemptEvidence,
    ReadEvidence,
    RunEvidence,
)
from primeqa.interpretation import (
    VrMeta,
    attribute_run,
    interpret_run,
)

_T = datetime(2026, 5, 27, tzinfo=timezone.utc)
_VR = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


# ---------------------------------------------------------------------------
# Stub S1 reader + evidence builders
# ---------------------------------------------------------------------------

class _StubS1:
    def __init__(self, vrs, *, flows=(), fields=None):
        self._vrs = tuple(vrs)
        self._flows = tuple(flows)              # D-229: FlowMeta tuple
        self._fields = dict(fields or {})       # D-229: {field_name: FieldMeta}
        self.calls = []
        self.flow_calls = []
        self.field_calls = []

    def vrs_for_object(self, subject_external_id):
        self.calls.append(subject_external_id)
        return self._vrs

    def flows_for_object(self, subject_external_id):
        self.flow_calls.append(subject_external_id)
        return self._flows

    def field_meta(self, object_external_id, field_external_id):
        self.field_calls.append((object_external_id, field_external_id))
        return self._fields.get(field_external_id)


def _run(*, outcome, create):
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=7,
        api_choice="rest", outcome=outcome, started_at=_T, finished_at=_T,
        steps=(create,))


def _create(*, success, matched, http_status, body, field_values=None, record_id=None):
    return CreateAttemptEvidence(
        step_id="create-violating", ordinal=0, sobject="Lead",
        field_values=field_values if field_values is not None else {"Reason__c": None},
        http_status=http_status, success=success,
        error_code=(body[0]["errorCode"] if body else None),
        message=(body[0].get("message") if body else None),
        rejection_body=tuple(body), matched=matched,
        cleanup=CleanupRecord(attempted=bool(record_id),
                              succeeded=True if record_id else None, record_id=record_id),
        started_at=_T, finished_at=_T, duration_ms=1)


def _interp(evidence, s1):
    """interpret (slice 1) then attribute (slice 2)."""
    return attribute_run(interpret_run(evidence), evidence, s1=s1)


# ---------------------------------------------------------------------------
# prohibition_not_enforced — (a) inactive / (b) drift / (c) enforcement gap
# ---------------------------------------------------------------------------

def test_vr_inactive():
    # the create's payload violates the VR's formula, but the VR is INACTIVE.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z"))
    s1 = _StubS1([VrMeta(name="RequireReason", is_active=False,
                         formula_text="ISBLANK(Reason__c)", error_message="reason required")])
    interp = _interp(ev, s1)
    assert interp.verdict == "prohibition_not_enforced"
    assert interp.cause.cause_kind == "vr_inactive"
    assert interp.cause.vr_name == "RequireReason"
    assert "inactive" in interp.attribution


def test_enforcement_gap():
    # the VR is ACTIVE and violated, yet the create succeeded — the real defect.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z"))
    s1 = _StubS1([VrMeta(name="RequireReason", is_active=True,
                         formula_text="ISBLANK(Reason__c)", error_message="reason required")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "enforcement_gap"
    assert interp.cause.vr_name == "RequireReason"


def test_vr_formula_drift():
    # the active VR's current formula is EVALUABLE on the payload but NOT violated
    # → confirmed drift (the rule was edited). Adjusted (D-114): the old absent-field
    # formula `Amount__c = 99` is `NonEvaluable` under `evaluate` (not confirmed-False)
    # — that case is now `vr_formula_indeterminate` (see below). A genuinely-evaluable
    # not-violated formula tests *confirmed* drift.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Status__c": "Final"}))
    s1 = _StubS1([VrMeta(name="RequireDraft", is_active=True,
                         formula_text='ISPICKVAL(Status__c, "Draft")', error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "vr_formula_drift"
    assert interp.cause.vr_name is None
    assert "edited since generation" in interp.attribution


def test_enforcement_gap_loosened_still_violating():
    # THE false-drift fix (D-114): the stored payload (Amount=99) STILL violates a
    # *loosened* current formula (Amount < 200), and the active VR didn't fire →
    # enforcement_gap, not drift. The old `derive` + subset-match proxy re-derived
    # `199`, missed the match, and mis-reported this as `vr_formula_drift`.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Amount": 99}))
    s1 = _StubS1([VrMeta(name="AmountCap", is_active=True,
                         formula_text="Amount < 200", error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "enforcement_gap"
    assert interp.cause.vr_name == "AmountCap"


def test_vr_formula_indeterminate():
    # the active VR's current formula left the single-object evaluable subset
    # (org-state ISCHANGED) → violation can't be computed → indeterminate, NOT a
    # guessed drift (the old NotDerivable → drift collapse — D-114 fix). Carries the VR.
    # D-229: the grounding VR references the SAME field the payload sets
    # (ISCHANGED(Status__c) on a Status__c claim) — a coherent grounding, so the
    # Finding-2 relevance filter keeps it.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Status__c": "Final"}))
    s1 = _StubS1([VrMeta(name="ChangedGuard", is_active=True,
                         formula_text="ISCHANGED(Status__c)", error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "vr_formula_indeterminate"
    assert interp.cause.vr_name == "ChangedGuard"
    assert "indeterminate" in interp.attribution


# ---------------------------------------------------------------------------
# Finding 2 (D-229): the relevance filter — an unrelated NonEvaluable VR must
# not mask the grounding VR's drift. (dogfood log P3)
# ---------------------------------------------------------------------------

def test_unrelated_indeterminate_vr_does_not_mask_drift():
    # The P3 capture: the claim grounds on an Amount VR that DRIFTED (current
    # `Amount > 999999`, payload Amount=50000 → evaluable, not violated). An
    # UNRELATED `CloseDate > TODAY()` VR on the same object is NonEvaluable. Pre
    # D-229 the unrelated rule landed in `indeterminate` and outranked the drift;
    # the relevance filter (CloseDate ∉ payload) drops it, so the real drift surfaces.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Amount": 50000}))
    s1 = _StubS1([
        VrMeta(name="Discount_Guard", is_active=True,
               formula_text="Amount > 999999", error_message="too big"),
        VrMeta(name="Close_Date_Cannot_Be_Future", is_active=True,
               formula_text="CloseDate > TODAY()", error_message="no future close"),
    ])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "vr_formula_drift"
    assert "edited since generation" in interp.attribution


def test_only_irrelevant_vrs_falls_through_to_no_active_vr():
    # Every VR on the object references a field the payload doesn't set → none is
    # relevant to this claim → no fabricated indeterminate/drift; honest no_active_vr.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Amount": 50000}))
    s1 = _StubS1([VrMeta(name="Close_Date_Cannot_Be_Future", is_active=True,
                         formula_text="CloseDate > TODAY()", error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "no_active_vr"


def test_isblank_grounding_vr_with_field_omitted_is_enforcement_gap():
    # Review finding #1: ISBLANK(Reason__c) fires True when Reason__c is OMITTED
    # from the payload (not just None). The filter must bucket on the EVALUATION
    # result — a confirmed violation is never dropped — so an active ISBLANK
    # grounding rule whose field the recipe omitted still reads enforcement_gap,
    # not the wrong no_active_vr.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Company": "Acme"}))             # Reason__c OMITTED
    s1 = _StubS1([VrMeta(name="RequireReason", is_active=True,
                         formula_text="ISBLANK(Reason__c)", error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "enforcement_gap"
    assert interp.cause.vr_name == "RequireReason"


def test_isblank_grounding_vr_omitted_field_inactive_is_vr_inactive():
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Company": "Acme"}))
    s1 = _StubS1([VrMeta(name="RequireReason", is_active=False,
                         formula_text="ISBLANK(Reason__c)", error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "vr_inactive"


def test_notparsed_vr_referencing_a_payload_field_stays_indeterminate():
    # Review finding #3: a VR whose WHOLE formula does not parse (an unrecognized
    # function) but whose lenient tokens include a PAYLOAD field is not provably
    # irrelevant — it stays indeterminate (with the VR name), not dropped. The
    # lenient extractor finds Amount even though parse() returns NotParsed.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Amount": 5}))
    s1 = _StubS1([VrMeta(name="WeirdGuard", is_active=True,
                         formula_text="Amount > 1000 && CUSTOMFN(Amount)",
                         error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "vr_formula_indeterminate"
    assert interp.cause.vr_name == "WeirdGuard"


def test_cross_object_only_vr_is_dropped_as_irrelevant():
    # A NonEvaluable VR whose only fields are cross-object (Account.Industry) has
    # no bare overlap with a single-object Amount payload → provably irrelevant →
    # dropped (a purely-cross-object formula can't be the grounding rule of a
    # derivable single-object behavioral negative). Falls through to no_active_vr.
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Amount": 5}))
    s1 = _StubS1([VrMeta(name="CrossObjGuard", is_active=True,
                         formula_text='Account.Industry = "Tech"', error_message="x")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "no_active_vr"


def test_no_active_vr():
    # no active VR enforces the prohibition (none on the object) → the residual the
    # old code mis-labeled as drift is now closed → no_active_vr (matches S8's leg).
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z",
        field_values={"Reason__c": None}))
    interp = _interp(ev, _StubS1([]))                          # no VRs at all
    assert interp.cause.cause_kind == "no_active_vr"
    assert "deactivated" in interp.attribution


# ---------------------------------------------------------------------------
# rejected_unasserted_reason — other VR / platform constraint
# ---------------------------------------------------------------------------

def test_other_vr_fired():
    # rejected by a *different* VR (same code, message matches another VR).
    ev = _run(outcome="failed", create=_create(
        success=False, matched=False, http_status=400,
        body=[{"errorCode": _VR, "message": "You must select an Order Type."}]))
    s1 = _StubS1([
        VrMeta(name="RequireReason", is_active=True, formula_text="ISBLANK(Reason__c)",
               error_message="reason required"),
        VrMeta(name="OrderTypeRequired", is_active=True, formula_text="ISBLANK(Type__c)",
               error_message="You must select an Order Type."),
    ])
    interp = _interp(ev, s1)
    assert interp.verdict == "rejected_unasserted_reason"
    assert interp.cause.cause_kind == "other_vr_fired"
    assert interp.cause.vr_name == "OrderTypeRequired"


def test_platform_constraint():
    # rejected by a non-VR platform code (REQUIRED_FIELD_MISSING).
    ev = _run(outcome="failed", create=_create(
        success=False, matched=False, http_status=400,
        body=[{"errorCode": "REQUIRED_FIELD_MISSING", "message": "Required fields missing"}]))
    interp = _interp(ev, _StubS1([]))
    assert interp.cause.cause_kind == "platform_constraint"
    assert "REQUIRED_FIELD_MISSING" in interp.cause.detail


def test_access_denied_is_attributed_deliberately():
    # D-225: an FLS/access code keeps cause_kind=platform_constraint but the
    # detail names the code AND the blocked field(s) — the CF-1 dogfood shape.
    ev = _run(outcome="failed", create=_create(
        success=False, matched=False, http_status=400,
        body=[{"errorCode": "INSUFFICIENT_ACCESS_OR_READONLY",
               "message": "insufficient access rights",
               "fields": ["Last_Escalation_Date__c"]}]))
    interp = _interp(ev, _StubS1([]))
    assert interp.cause.cause_kind == "platform_constraint"
    assert "access denied" in interp.cause.detail
    assert "INSUFFICIENT_ACCESS_OR_READONLY" in interp.cause.detail
    assert "Last_Escalation_Date__c" in interp.cause.detail


def test_access_denied_without_fields_still_names_the_code():
    ev = _run(outcome="failed", create=_create(
        success=False, matched=False, http_status=400,
        body=[{"errorCode": "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY",
               "message": "no access to the referenced record"}]))
    interp = _interp(ev, _StubS1([]))
    assert interp.cause.cause_kind == "platform_constraint"
    assert "access denied" in interp.cause.detail
    assert "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY" in interp.cause.detail
    assert "field(s)" not in interp.cause.detail


# ---------------------------------------------------------------------------
# Pass-through + discipline
# ---------------------------------------------------------------------------

def test_passed_is_passthrough_unchanged():
    ev = _run(outcome="passed", create=_create(
        success=False, matched=True, http_status=400,
        body=[{"errorCode": _VR, "message": "reason required"}]))
    base = interpret_run(ev)
    enriched = attribute_run(base, ev, s1=_StubS1([VrMeta("x", True)]))
    assert enriched == base                  # unchanged
    assert enriched.cause is None


def test_inspection_is_passthrough():
    read = ReadEvidence(
        step_id="r", ordinal=0, query="q", sobject="ValidationRule", edge="APPLIES_TO",
        subject_entity_type="Object", subject_external_id="Lead", row_count=0,
        rows=(), started_at=_T, finished_at=_T, duration_ms=1)
    assertion = AssertEvidence(
        step_id="a", ordinal=1, predicate="exists", subject_ref="r",
        evaluated_row_count=0, held=False, started_at=_T, finished_at=_T, duration_ms=0)
    ev = RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, environment_id=7, api_choice="metadata_api",
        outcome="failed", started_at=_T, finished_at=_T, steps=(read, assertion))
    base = interpret_run(ev)
    enriched = attribute_run(base, ev, s1=_StubS1([]))
    assert enriched == base and enriched.cause is None


def test_outcome_never_mutated_and_deterministic():
    ev = _run(outcome="failed", create=_create(
        success=True, matched=False, http_status=201, body=[], record_id="001Z"))
    s1 = _StubS1([VrMeta("RequireReason", True, "ISBLANK(Reason__c)", "x")])
    a, b = _interp(ev, s1), _interp(ev, s1)
    assert a.outcome == "failed"             # carried, never recomputed
    assert a == b                            # deterministic


# ---------------------------------------------------------------------------
# D-203 — the 2-step negative: effective-state evaluation (update) +
# cause-less pass-through (delete)
# ---------------------------------------------------------------------------

from primeqa.execution_engine.evidence import (  # noqa: E402
    DeleteAttemptEvidence,
    UpdateAttemptEvidence,
)


def _setup(field_values=None):
    return CreateAttemptEvidence(
        step_id="create-setup", ordinal=0, sobject="Opportunity",
        field_values=field_values if field_values is not None else
        {"Amount": 500, "Name": "PQA"},
        http_status=201, success=True, error_code=None, message=None,
        rejection_body=(), matched=None,
        cleanup=CleanupRecord(attempted=True, succeeded=True, record_id="006A"),
        started_at=_T, finished_at=_T, duration_ms=1)


def _mutation(kind, *, success, matched, http_status, body, field_changes=None):
    common = dict(
        step_id=f"{kind}-violating", ordinal=1, sobject="Opportunity",
        record_id="006A", http_status=http_status, success=success,
        error_code=(body[0]["errorCode"] if body else None),
        message=(body[0].get("message") if body else None),
        rejection_body=tuple(body), matched=matched,
        started_at=_T, finished_at=_T, duration_ms=1)
    if kind == "update":
        return UpdateAttemptEvidence(
            field_changes=field_changes or {"Amount": 2000000}, **common)
    return DeleteAttemptEvidence(**common)


def _run2(*, outcome, steps):
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=7,
        api_choice="rest", outcome=outcome, started_at=_T, finished_at=_T,
        steps=tuple(steps))


def test_update_not_enforced_evaluates_the_effective_state():
    # Setup Amount=500 (non-violating); the update pushed Amount=2000000 —
    # the EFFECTIVE state violates `Amount > 1000000`, so a successful update
    # is a real enforcement gap. Evaluating the setup payload alone (500)
    # would have mis-read this as drift.
    ev = _run2(outcome="failed", steps=[
        _setup(),
        _mutation("update", success=True, matched=False, http_status=204, body=[])])
    s1 = _StubS1([VrMeta("Discount_Guard", True, "Amount > 1000000", "too big")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "enforcement_gap"
    assert interp.cause.vr_name == "Discount_Guard"
    assert "update" in interp.cause.detail
    assert s1.calls == ["Opportunity"]       # the MUTATION step's sobject


def test_update_not_enforced_ischanged_formula_is_indeterminate():
    # An org-state formula (ISCHANGED) is NonEvaluable on a flat payload —
    # honest indeterminate, never a guessed drift. D-229: the update changes the
    # SAME field the VR watches (StageName), so the Finding-2 relevance filter
    # keeps the grounding rule.
    ev = _run2(outcome="failed", steps=[
        _setup(),
        _mutation("update", success=True, matched=False, http_status=204, body=[],
                  field_changes={"StageName": "Closed"})])
    s1 = _StubS1([VrMeta("NoStageMove", True,
                         "ISCHANGED(StageName)", "no stage moves")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "vr_formula_indeterminate"


def test_update_unasserted_other_vr_fired():
    ev = _run2(outcome="failed", steps=[
        _setup(),
        _mutation("update", success=False, matched=False, http_status=400,
                  body=[{"errorCode": _VR, "message": "other rule"}])])
    s1 = _StubS1([VrMeta("OtherRule", True, "Amount > 5", "other rule")])
    interp = _interp(ev, s1)
    assert interp.cause.cause_kind == "other_vr_fired"
    assert interp.cause.vr_name == "OtherRule"
    assert "update" in interp.cause.detail


def test_delete_not_enforced_passes_through_causeless():
    # VRs cannot enforce delete prohibitions — a formula-derived cause would
    # be fabricated. Verdict survives; cause stays None (repair.py handles it).
    ev = _run2(outcome="failed", steps=[
        _setup(),
        _mutation("delete", success=True, matched=False, http_status=204, body=[])])
    s1 = _StubS1([VrMeta("Discount_Guard", True, "Amount > 1000000", "too big")])
    interp = _interp(ev, s1)
    assert interp.verdict == "prohibition_not_enforced"
    assert interp.cause is None


def test_delete_unasserted_still_attributes_from_the_rejection_body():
    # The unasserted path is rejection-body-driven — it works for deletes
    # (e.g. a platform restriction fired instead of the asserted signal).
    ev = _run2(outcome="failed", steps=[
        _setup(),
        _mutation("delete", success=False, matched=False, http_status=400,
                  body=[{"errorCode": "DELETE_FAILED", "message": "ref"}])])
    interp = _interp(ev, _StubS1([]))
    assert interp.cause.cause_kind == "platform_constraint"
    assert "delete" in interp.cause.detail


# ---------------------------------------------------------------------------
# D-229 — positive-vertical failure attribution (automation/state Flow +
# value-claim field createability)
# ---------------------------------------------------------------------------

from primeqa.execution_engine.evidence import DataReadEvidence       # noqa: E402
from primeqa.interpretation import FieldMeta, FlowMeta               # noqa: E402


def _positive(*, sobject, claim_kind, captured=("Status__c",), outcome="failed"):
    """A positive create-and-verify run: create succeeds, read-back observed,
    the value/effect assert does NOT hold (outcome=failed)."""
    create = CreateAttemptEvidence(
        step_id="create", ordinal=0, sobject=sobject,
        field_values={"Subject": "x"}, http_status=201, success=True,
        error_code=None, message=None, rejection_body=(), matched=None,
        cleanup=CleanupRecord(attempted=False), started_at=_T, finished_at=_T,
        duration_ms=1)
    read = DataReadEvidence(
        step_id="read", ordinal=1, soql=f"SELECT {','.join(captured)} FROM {sobject}",
        sobject=sobject, fields_captured=tuple(captured), row_count=1,
        rows=({c: None for c in captured},), started_at=_T, finished_at=_T,
        duration_ms=1)
    assertion = AssertEvidence(
        step_id="assert", ordinal=2, predicate="equals", subject_ref="read",
        evaluated_row_count=1, held=(outcome == "passed"),
        started_at=_T, finished_at=_T, duration_ms=0)
    ev = RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, environment_id=7, api_choice="rest", outcome=outcome,
        started_at=_T, finished_at=_T, steps=(create, read, assertion))
    return ev, claim_kind


def _interp_pos(ev_kind, s1):
    ev, claim_kind = ev_kind
    return attribute_run(interpret_run(ev, claim_kind=claim_kind), ev, s1=s1)


def test_automation_not_triggered_inactive_flow():
    # The dogfood P1 capture: the grounding Flow was deactivated → no active
    # Flow triggers on the object → automation_inactive.
    ek = _positive(sobject="Case", claim_kind="automation-effect-claim")
    s1 = _StubS1([], flows=[FlowMeta("SQ205_Create_Case_SLA", is_active=False)])
    interp = _interp_pos(ek, s1)
    assert interp.verdict == "automation_not_triggered"
    assert interp.cause.cause_kind == "automation_inactive"
    assert "deactivated or removed" in interp.attribution
    assert s1.flow_calls == ["Case"]


def test_automation_not_triggered_no_flow_at_all_is_inactive():
    # No Flow triggers on the object at all (deleted, not just disabled) — same
    # honest signal: no active automation could fire.
    ek = _positive(sobject="Case", claim_kind="automation-effect-claim")
    interp = _interp_pos(ek, _StubS1([], flows=[]))
    assert interp.cause.cause_kind == "automation_inactive"


def test_automation_effect_absent_active_flow_present():
    # An active Flow IS present, yet the effect wasn't observed — distinct from
    # inactive: the automation exists but didn't produce the effect.
    ek = _positive(sobject="Case", claim_kind="automation-effect-claim")
    s1 = _StubS1([], flows=[FlowMeta("SQ205_Create_Case_SLA", is_active=True)])
    interp = _interp_pos(ek, s1)
    assert interp.cause.cause_kind == "automation_effect_absent"
    assert "SQ205_Create_Case_SLA" in interp.cause.detail


def test_state_not_transitioned_uses_the_same_flow_logic():
    ek = _positive(sobject="Order__c", claim_kind="state-transition-claim")
    interp = _interp_pos(ek, _StubS1([], flows=[FlowMeta("Stamp", is_active=False)]))
    assert interp.verdict == "state_not_transitioned"
    assert interp.cause.cause_kind == "automation_inactive"


def test_value_not_persisted_field_not_createable():
    # The asserted field is not createable → SF dropped the posted value on insert.
    ek = _positive(sobject="Invoice__c", claim_kind="value-claim",
                   captured=("Amount__c",))
    s1 = _StubS1([], fields={"Amount__c": FieldMeta("Amount__c", is_createable=False)})
    interp = _interp_pos(ek, s1)
    assert interp.verdict == "value_not_persisted"
    assert interp.cause.cause_kind == "field_not_createable"
    assert "Amount__c" in interp.cause.detail
    assert s1.field_calls == [("Invoice__c", "Amount__c")]


def test_value_not_persisted_createable_field_no_flow_passes_through():
    # The field IS createable AND no before-save Flow on the object → pass-through
    # (a field default S1 can't see, or another cause) — no fabricated cause.
    ek = _positive(sobject="Invoice__c", claim_kind="value-claim",
                   captured=("Amount__c",))
    s1 = _StubS1([], fields={"Amount__c": FieldMeta("Amount__c", is_createable=True)})
    interp = _interp_pos(ek, s1)
    assert interp.verdict == "value_not_persisted"
    assert interp.cause is None


def test_value_not_persisted_active_before_save_flow_attributes():
    # D-241: createable field, value still didn't persist, an ACTIVE before-save
    # Flow triggers on the object → it overwrote the posted value on insert.
    ek = _positive(sobject="Invoice__c", claim_kind="value-claim",
                   captured=("Amount__c",))
    s1 = _StubS1([], fields={"Amount__c": FieldMeta("Amount__c", is_createable=True)},
                 flows=[FlowMeta("Recalc_Amount", is_active=True,
                                 trigger_type="BeforeSave")])
    interp = _interp_pos(ek, s1)
    assert interp.verdict == "value_not_persisted"
    assert interp.cause.cause_kind == "before_save_automation_overwrote"
    assert "Recalc_Amount" in interp.cause.detail


def test_value_not_persisted_after_save_flow_passes_through():
    # D-241: an AFTER-save Flow runs after insert — it cannot have overwritten the
    # posted value on insert, so it is NOT the cause. Honest pass-through.
    ek = _positive(sobject="Invoice__c", claim_kind="value-claim",
                   captured=("Amount__c",))
    s1 = _StubS1([], fields={"Amount__c": FieldMeta("Amount__c", is_createable=True)},
                 flows=[FlowMeta("Notify", is_active=True, trigger_type="AfterSave")])
    interp = _interp_pos(ek, s1)
    assert interp.cause is None


def test_value_not_persisted_inactive_before_save_passes_through():
    # D-241: an INACTIVE before-save Flow can't fire — not the cause.
    ek = _positive(sobject="Invoice__c", claim_kind="value-claim",
                   captured=("Amount__c",))
    s1 = _StubS1([], fields={"Amount__c": FieldMeta("Amount__c", is_createable=True)},
                 flows=[FlowMeta("Recalc_Amount", is_active=False,
                                 trigger_type="BeforeSave")])
    interp = _interp_pos(ek, s1)
    assert interp.cause is None


def test_flowmeta_trigger_type_defaults_none():
    # Back-compat: FlowMeta without trigger_type (the D-229 shape) is treated as
    # not-before-save, so it never spuriously attributes the new cause.
    assert FlowMeta("X", is_active=True).trigger_type is None


def test_positive_passed_is_passthrough():
    # A passing positive run is never attributed (no failure to explain).
    ek = _positive(sobject="Case", claim_kind="automation-effect-claim",
                   outcome="passed")
    s1 = _StubS1([], flows=[FlowMeta("X", is_active=False)])
    interp = _interp_pos(ek, s1)
    assert interp.verdict == "automation_triggered"
    assert interp.cause is None
    assert s1.flow_calls == []                  # self-limiting: no S1 read on pass


def test_state_transition_2create_chain_queries_the_trigger_object():
    # Review finding #5: a 2-create state-transition chain creates the OBSERVED
    # record first (Case) then the TRIGGER (Escalation__c, the Flow's trigger).
    # Attribution must query the LAST create's object (Escalation__c), not the
    # first (Case) — querying Case would find no flow and wrongly say inactive.
    case_create = CreateAttemptEvidence(
        step_id="create-case", ordinal=0, sobject="Case",
        field_values={"Subject": "x"}, http_status=201, success=True,
        error_code=None, message=None, rejection_body=(), matched=None,
        cleanup=CleanupRecord(attempted=False), started_at=_T, finished_at=_T,
        duration_ms=1)
    esc_create = CreateAttemptEvidence(
        step_id="create-escalation", ordinal=1, sobject="Escalation__c",
        field_values={"Case__c": "$create-case.id"}, http_status=201, success=True,
        error_code=None, message=None, rejection_body=(), matched=None,
        cleanup=CleanupRecord(attempted=False), started_at=_T, finished_at=_T,
        duration_ms=1)
    read = DataReadEvidence(
        step_id="read", ordinal=2, soql="SELECT Status FROM Case", sobject="Case",
        fields_captured=("Status",), row_count=1, rows=({"Status": "New"},),
        started_at=_T, finished_at=_T, duration_ms=1)
    assertion = AssertEvidence(
        step_id="assert", ordinal=3, predicate="equals", subject_ref="read",
        evaluated_row_count=1, held=False, started_at=_T, finished_at=_T, duration_ms=0)
    ev = RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, environment_id=7, api_choice="rest", outcome="failed",
        started_at=_T, finished_at=_T, steps=(case_create, esc_create, read, assertion))
    # only the Escalation__c (trigger) object has the flow; Case does not.
    s1 = _StubS1([], flows=[FlowMeta("EscFlow", is_active=False)])
    interp = attribute_run(
        interpret_run(ev, claim_kind="state-transition-claim"), ev, s1=s1)
    assert interp.verdict == "state_not_transitioned"
    assert interp.cause.cause_kind == "automation_inactive"
    assert s1.flow_calls == ["Escalation__c"]   # the TRIGGER object, not Case

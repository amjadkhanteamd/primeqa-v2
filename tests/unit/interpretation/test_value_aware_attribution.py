"""Unit tests for D-425 value-aware attribution — the D-424 assert envelope
splitting `automation_effect_absent`.

One test per refined cause branch (record-absent / divergent /
representation-mismatch / ambiguous-null), the exists-predicate twin, the
Id-vs-Id non-collision, the fallback-scan path, and the absence law: a
pre-D-424 run (no envelope) attributes BYTE-IDENTICALLY to the old hedge.
Offline, stub S1 reader.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CleanupRecord,
    CreateAttemptEvidence,
    DataReadEvidence,
    RunEvidence,
)
from primeqa.interpretation import FlowMeta, attribute_run, interpret_run

_T = datetime(2026, 8, 1, tzinfo=timezone.utc)


class _StubS1:
    def __init__(self, flows=()):
        self._flows = tuple(flows)

    def vrs_for_object(self, subject_external_id):
        return ()

    def flows_for_object(self, subject_external_id):
        return self._flows

    def field_meta(self, object_external_id, field_external_id):
        return None


def _evidence(*, assert_kwargs, sobject="Opportunity", outcome="failed"):
    """A failed positive create-and-verify run whose assert carries the given
    D-424 envelope fields (or none — the pre-D-424 shape)."""
    create = CreateAttemptEvidence(
        step_id="create", ordinal=0, sobject=sobject,
        field_values={"Loan_Type__c": "Home"}, http_status=201, success=True,
        error_code=None, message=None, rejection_body=(), matched=None,
        cleanup=CleanupRecord(attempted=False), started_at=_T, finished_at=_T,
        duration_ms=1)
    read = DataReadEvidence(
        step_id="read", ordinal=1, soql=f"SELECT x FROM {sobject}",
        sobject=sobject, fields_captured=("Risk_Rating__c",), row_count=1,
        rows=({"Risk_Rating__c": None},), started_at=_T, finished_at=_T,
        duration_ms=1)
    assertion = AssertEvidence(
        step_id="assert", ordinal=2, subject_ref="read.Risk_Rating__c",
        evaluated_row_count=1, held=False, started_at=_T, finished_at=_T,
        duration_ms=0, **assert_kwargs)
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=59,
        api_choice="rest", outcome=outcome, started_at=_T, finished_at=_T,
        steps=(create, read, assertion))


def _attributed(ev, *, flows, claim_automation, claim_kind="automation-effect-claim"):
    return attribute_run(
        interpret_run(ev, claim_kind=claim_kind), ev,
        s1=_StubS1(flows), claim_automation=claim_automation)


_BOUND = {"name": "HL_Auto_Risk_Rating", "primitive": "flow"}
_ACTIVE = FlowMeta("HL_Auto_Risk_Rating", is_active=True)


# ---------------------------------------------------------------------------
# (a) record absent — no_row and its exists-predicate twin
# ---------------------------------------------------------------------------

def test_no_row_is_record_absent():
    ev = _evidence(assert_kwargs=dict(
        predicate="equals", asserted_field="Risk_Rating__c",
        asserted_value="Low", observed_value=None, observed_kind="no_row"))
    interp = _attributed(ev, flows=[_ACTIVE], claim_automation=_BOUND)
    assert interp.cause.cause_kind == "automation_effect_record_absent"
    assert "never produced" in interp.cause.detail
    # WHAT is decided; WHY stays honestly open.
    assert "not determinable" in interp.cause.detail


def test_exists_zero_rows_is_record_absent_for_the_approval_binding():
    # The d49719e2 shape: an approval-effect claim's exists assert over the
    # side-effect read found nothing.
    ev = _evidence(assert_kwargs=dict(
        predicate="exists", asserted_field=None, asserted_value=None,
        observed_value=0, observed_kind="row_count"))
    interp = _attributed(
        ev, flows=[], claim_automation={"name": "HL_High_Value_Loan",
                                        "primitive": "approval_process"})
    assert interp.cause.cause_kind == "automation_effect_record_absent"
    assert "HL_High_Value_Loan" in interp.cause.detail


# ---------------------------------------------------------------------------
# (c) ambiguous null — sharper cause, never a firing claim, never a
#     decidable branch
# ---------------------------------------------------------------------------

def test_null_observed_is_value_absent_and_never_a_firing_claim():
    # The 9ba2d3d2 shape: asserted "Low", record observed, field null.
    ev = _evidence(assert_kwargs=dict(
        predicate="equals", asserted_field="Risk_Rating__c",
        asserted_value="Low", observed_value=None,
        observed_kind="field_value"))
    interp = _attributed(ev, flows=[_ACTIVE], claim_automation=_BOUND)
    assert interp.cause.cause_kind == "automation_effect_value_absent"
    assert "not decidable" in interp.cause.detail
    # It must not collapse into either decidable branch nor claim firing.
    lowered = interp.cause.detail.lower()
    assert "fired" not in lowered and "did not fire" not in lowered
    assert "never produced" not in lowered


# ---------------------------------------------------------------------------
# (b) divergent value — names the observed value, enumerates writers,
#     states the Apex gap
# ---------------------------------------------------------------------------

def test_divergent_names_value_writers_and_the_apex_gap():
    ev = _evidence(assert_kwargs=dict(
        predicate="equals", asserted_field="Risk_Rating__c",
        asserted_value="Low", observed_value="High",
        observed_kind="field_value"))
    interp = _attributed(
        ev, flows=[_ACTIVE, FlowMeta("HL_High_Risk_Task", is_active=True)],
        claim_automation=_BOUND)
    assert interp.cause.cause_kind == "automation_effect_divergent"
    assert "'High'" in interp.cause.detail and "'Low'" in interp.cause.detail
    # Never asserts WHO wrote it; enumerates candidates + the capture gap.
    assert "HL_High_Risk_Task" in interp.cause.detail
    assert "Apex triggers are not captured" in interp.cause.detail


def test_count_mismatch_is_divergent_with_counts():
    ev = _evidence(assert_kwargs=dict(
        predicate="count_equals", asserted_field=None, asserted_value=2,
        observed_value=1, observed_kind="row_count"))
    interp = _attributed(ev, flows=[_ACTIVE], claim_automation=_BOUND)
    assert interp.cause.cause_kind == "automation_effect_divergent"
    assert "2" in interp.cause.detail and "1" in interp.cause.detail


# ---------------------------------------------------------------------------
# representation mismatch — the 0d81c6f9 label-vs-Id specimen
# ---------------------------------------------------------------------------

def test_label_vs_id_is_representation_mismatch():
    ev = _evidence(assert_kwargs=dict(
        predicate="equals", asserted_field="OwnerId",
        asserted_value="Credit Manager",
        observed_value="005F900000ATd9AIAT", observed_kind="field_value"))
    interp = _attributed(
        ev, flows=[FlowMeta("HL_High_Risk_Task", is_active=True)],
        claim_automation={"name": "HL_High_Risk_Task", "primitive": "flow"})
    assert interp.cause.cause_kind == "representation_mismatch"
    assert "'Credit Manager'" in interp.cause.detail
    assert "'005F900000ATd9AIAT'" in interp.cause.detail
    assert "claim-authoring defect" in interp.cause.detail


def test_id_vs_id_stays_divergent_not_representation_mismatch():
    # Both values Id-shaped → the narrow detector must NOT fire; the safe
    # fallback is divergent (refinement, never fabrication — D-425).
    ev = _evidence(assert_kwargs=dict(
        predicate="equals", asserted_field="OwnerId",
        asserted_value="005F900000AAAAAAAA",
        observed_value="005F900000ATd9AIAT", observed_kind="field_value"))
    interp = _attributed(ev, flows=[_ACTIVE], claim_automation=_BOUND)
    assert interp.cause.cause_kind == "automation_effect_divergent"


# ---------------------------------------------------------------------------
# the fallback-scan path (no binding — state-transition claims)
# ---------------------------------------------------------------------------

def test_state_transition_scan_path_refines_too():
    ev = _evidence(assert_kwargs=dict(
        predicate="equals", asserted_field="PLS_FB_Reopened__c",
        asserted_value=True, observed_value=None,
        observed_kind="field_value"))
    interp = _attributed(ev, flows=[FlowMeta("FL09", is_active=True)],
                         claim_automation=None,
                         claim_kind="state-transition-claim")
    assert interp.verdict == "state_not_transitioned"
    assert interp.cause.cause_kind == "automation_effect_value_absent"


# ---------------------------------------------------------------------------
# the absence law — pre-D-424 evidence attributes byte-identically
# ---------------------------------------------------------------------------

def test_pre_d424_run_attributes_byte_identically_to_the_hedge():
    # No envelope at all (observed_kind None) — the exact pre-D-425 cause,
    # wording included.
    ev = _evidence(assert_kwargs=dict(predicate="equals"))
    interp = _attributed(ev, flows=[_ACTIVE], claim_automation=_BOUND)
    assert interp.cause.cause_kind == "automation_effect_absent"
    assert interp.cause.detail == (
        "the Flow HL_Auto_Risk_Rating is active on Opportunity, but the "
        "asserted effect was not observed — an entry condition may be "
        "unmet, or its logic changed since generation")

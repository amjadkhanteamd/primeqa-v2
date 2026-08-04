"""Unit tests for the D-427 absence-mirror enrichment —
`automation_fired_unexpectedly` gets its own direction-correct attribution
arm (`_attribute_unexpected_presence`), never routed into the
absence-directional `_attribute_automation_absent`.

Pins: the decidable sub-cause (inactive/retargeted bound flow PROVES another
writer, candidates enumerated, Apex caveat), the honest bound-active case
(WHAT decided, WHO open), the D-382 unknown-activity refusal, and — across
EVERY branch — that the prose never claims the automation failed to fire.
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

_T = datetime(2026, 8, 4, tzinfo=timezone.utc)


class _StubS1:
    def __init__(self, flows=()):
        self._flows = tuple(flows)

    def vrs_for_object(self, subject_external_id):
        return ()

    def flows_for_object(self, subject_external_id):
        return self._flows

    def field_meta(self, object_external_id, field_external_id):
        return None


def _absence_run(*, outcome="failed", rows=1, with_envelope=True):
    """A D-307 absence run: create the trigger record, read the correlated
    side-effect object, assert not_exists. ``rows`` observed → failed."""
    create = CreateAttemptEvidence(
        step_id="create-record", ordinal=0, sobject="Opportunity",
        field_values={"Risk_Rating__c": "Low"}, http_status=201, success=True,
        error_code=None, message=None, rejection_body=(), matched=None,
        cleanup=CleanupRecord(attempted=False), started_at=_T, finished_at=_T,
        duration_ms=1)
    read = DataReadEvidence(
        step_id="read-effect", ordinal=1,
        soql="SELECT Id FROM Task WHERE WhatId = '$create-record.id'",
        sobject="Task", fields_captured=("Id",), row_count=rows,
        rows=tuple({"Id": f"00T{i:015d}"} for i in range(rows)),
        started_at=_T, finished_at=_T, duration_ms=1)
    env = (dict(observed_kind="row_count", observed_value=rows)
           if with_envelope else {})
    assertion = AssertEvidence(
        step_id="assert-absence", ordinal=2, predicate="not_exists",
        subject_ref="read-effect", evaluated_row_count=rows,
        held=(outcome == "passed"), started_at=_T, finished_at=_T,
        duration_ms=0, **env)
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=59,
        api_choice="rest", outcome=outcome, started_at=_T, finished_at=_T,
        steps=(create, read, assertion))


def _attributed(ev, *, flows, claim_automation):
    return attribute_run(
        interpret_run(ev, claim_kind="automation-effect-claim"), ev,
        s1=_StubS1(flows), claim_automation=claim_automation)


_BOUND = {"name": "HL_Auto_Risk_Rating", "primitive": "flow"}

# The direction pin: none of these absence-vocabulary phrases may ever
# appear in an unexpected-presence cause.
_FORBIDDEN = ("not observed", "could not fire", "did not produce",
              "may be unmet", "was never produced", "did not fire")


def _assert_direction_correct(detail: str):
    lowered = detail.lower()
    for phrase in _FORBIDDEN:
        assert phrase not in lowered, (phrase, detail)


def test_verdict_is_the_absence_mirror():
    ev = _absence_run()
    interp = interpret_run(ev, claim_kind="automation-effect-claim")
    assert interp.verdict == "automation_fired_unexpectedly"


def test_inactive_bound_flow_proves_another_writer():
    ev = _absence_run()
    interp = _attributed(
        ev, flows=[FlowMeta("HL_Auto_Risk_Rating", is_active=False),
                   FlowMeta("HL_High_Risk_Task", is_active=True)],
        claim_automation=_BOUND)
    assert interp.cause.cause_kind == "other_writer_produced_record"
    d = interp.cause.detail
    assert "inactive" in d and "cannot have produced" in d
    assert "HL_High_Risk_Task" in d              # candidates enumerated
    assert "Apex triggers are not captured" in d  # never exhaustive
    _assert_direction_correct(d)


def test_retargeted_bound_flow_proves_another_writer():
    ev = _absence_run()
    interp = _attributed(
        ev, flows=[FlowMeta("Different_Flow", is_active=True)],
        claim_automation=_BOUND)
    assert interp.cause.cause_kind == "other_writer_produced_record"
    assert "removed or retargeted" in interp.cause.detail
    _assert_direction_correct(interp.cause.detail)


def test_active_bound_flow_is_honest_about_authorship():
    # WHAT is decided (a record exists where none should); WHO stays open.
    ev = _absence_run()
    interp = _attributed(
        ev, flows=[FlowMeta("HL_Auto_Risk_Rating", is_active=True)],
        claim_automation=_BOUND)
    assert interp.cause.cause_kind == "automation_effect_record_present"
    d = interp.cause.detail
    assert "asserts none should exist" in d
    assert "not decidable" in d
    _assert_direction_correct(d)


def test_unknown_activity_refuses_via_grounding_incomplete():
    ev = _absence_run()
    interp = _attributed(
        ev, flows=[FlowMeta("HL_Auto_Risk_Rating", is_active=None)],
        claim_automation=_BOUND)
    assert interp.cause.cause_kind == "grounding_incomplete"
    assert "UNKNOWN" in interp.cause.detail
    _assert_direction_correct(interp.cause.detail)


def test_unbound_scan_with_no_active_flow_is_other_writer():
    ev = _absence_run()
    interp = _attributed(ev, flows=[FlowMeta("Off", is_active=False)],
                         claim_automation=None)
    assert interp.cause.cause_kind == "other_writer_produced_record"
    assert "outside the captured automation surface" in interp.cause.detail
    _assert_direction_correct(interp.cause.detail)


def test_envelope_count_rides_the_prose_and_absence_still_enriches():
    ev = _absence_run(rows=3)
    interp = _attributed(
        ev, flows=[FlowMeta("HL_Auto_Risk_Rating", is_active=True)],
        claim_automation=_BOUND)
    assert "3 correlated records were observed" in interp.cause.detail
    # Pre-D-424 evidence (no envelope) still enriches — there is no prior
    # behaviour to preserve (the verdict never had a cause before D-427).
    ev2 = _absence_run(with_envelope=False)
    interp2 = _attributed(
        ev2, flows=[FlowMeta("HL_Auto_Risk_Rating", is_active=False)],
        claim_automation=_BOUND)
    assert interp2.cause.cause_kind == "other_writer_produced_record"


def test_passed_absence_run_stays_causeless_passthrough():
    ev = _absence_run(outcome="passed", rows=0)
    interp = _attributed(
        ev, flows=[FlowMeta("HL_Auto_Risk_Rating", is_active=True)],
        claim_automation=_BOUND)
    assert interp.verdict == "automation_absence_confirmed"
    assert interp.cause is None

"""Unit tests for finalize_run (D-108.3 slice 4) — spy coordinator, no DB.

finalize_run persists the evidence then reports posture to S2 on the same
session. These tests use a spy coordinator + a fake session to verify the exact
report_run_outcome kwargs (the field mapping) and the shared-session contract —
no DB, so the slice-2/3 no-DB boundary holds here too.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    ReadEvidence,
    RunEvidence,
)
from primeqa.execution_engine.finalize import finalize_run

_T0 = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 5, 27, 12, 0, 1, tzinfo=timezone.utc)


class _FakeSession:
    def __init__(self):
        self.added = []
        self.flush_count = 0

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flush_count += 1


class _SpyCoordinator:
    """Records the report_run_outcome call (session + kwargs)."""

    def __init__(self):
        self.calls = []

    def report_run_outcome(self, session, **kwargs):
        self.calls.append((session, kwargs))
        return {"runtime_state": "stub"}     # stand-in RecipeRuntimeState


def _evidence(*, outcome="passed"):
    read = ReadEvidence(
        step_id="read-subject", ordinal=0, query="SELECT ...",
        sobject="ValidationRule", edge="APPLIES_TO",
        subject_entity_type="Object", subject_external_id="Lead",
        row_count=1, rows=({"Id": "1"},), started_at=_T0, finished_at=_T1,
        duration_ms=1000)
    assertion = AssertEvidence(
        step_id="assert-edge", ordinal=1, predicate="exists",
        subject_ref="read-subject", evaluated_row_count=1, held=True,
        started_at=_T1, finished_at=_T1, duration_ms=0)
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=5,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=7,
        api_choice="metadata_api", outcome=outcome,
        started_at=_T0, finished_at=_T1, steps=(read, assertion))


def test_reports_outcome_with_exact_mapped_kwargs():
    ev = _evidence(outcome="passed")
    session, coord = _FakeSession(), _SpyCoordinator()
    finalize_run(session, ev, coordinator=coord)

    assert len(coord.calls) == 1
    call_session, kwargs = coord.calls[0]
    # same session object — persist + posture share one transaction.
    assert call_session is session
    assert kwargs["actor"] == "s4"
    assert kwargs["recipe_id"] == ev.recipe_id
    assert kwargs["last_run_id"] == ev.run_id                       # run_id -> last_run_id
    assert kwargs["last_run_at"] == ev.finished_at                  # finished_at -> last_run_at
    assert kwargs["last_run_outcome"] == "passed"                   # outcome -> last_run_outcome
    assert kwargs["last_run_recipe_version_seq"] == 5               # recipe_version_seq -> ...


def test_persists_run_on_the_same_session_before_posture():
    ev = _evidence()
    session, coord = _FakeSession(), _SpyCoordinator()
    finalize_run(session, ev, coordinator=coord)

    # persist_run_evidence added the s4_execution_runs row to THIS session.
    assert len(session.added) == 1
    assert session.added[0].run_id == ev.run_id
    # both persist + posture flushed on the same session (persister flushes once;
    # the spy coordinator doesn't flush, so flush_count == 1 from the persister).
    assert session.flush_count == 1


def test_returns_coordinator_runtime_state():
    result = finalize_run(_FakeSession(), _evidence(), coordinator=_SpyCoordinator())
    assert result == {"runtime_state": "stub"}


def test_outcome_passthrough_for_failed_and_errored():
    for oc in ("failed", "errored"):
        coord = _SpyCoordinator()
        finalize_run(_FakeSession(), _evidence(outcome=oc), coordinator=coord)
        assert coord.calls[0][1]["last_run_outcome"] == oc

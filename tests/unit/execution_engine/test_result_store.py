"""Unit tests for persist_run_evidence mapping (D-108.2 slice 3) — fake session,
no real DB.

Verifies the RunEvidence -> s4_execution_runs mapping: typed columns carry
identity/outcome; the evidence JSONB carries the per-step trace + api_choice +
error surface, JSONB-safe (ISO timestamps, no tuples). A fake session records
the added row + asserts flush() is called and run_id returned.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    ErrorSurface,
    ReadEvidence,
    RunEvidence,
)
from primeqa.execution_engine.result_store import (
    S4ExecutionRun,
    persist_run_evidence,
)

_T0 = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 5, 27, 12, 0, 1, tzinfo=timezone.utc)


class _FakeSession:
    def __init__(self):
        self.added = []
        self.flushed = False

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushed = True


def _read_ev(row_count=1):
    return ReadEvidence(
        step_id="read-subject", ordinal=0,
        query="SELECT Id, ValidationName FROM ValidationRule WHERE ...",
        sobject="ValidationRule", edge="APPLIES_TO",
        subject_entity_type="Object", subject_external_id="Lead",
        row_count=row_count, rows=tuple({"Id": str(i)} for i in range(row_count)),
        started_at=_T0, finished_at=_T1, duration_ms=1000)


def _assert_ev(held=True):
    return AssertEvidence(
        step_id="assert-edge", ordinal=1, predicate="exists",
        subject_ref="read-subject", evaluated_row_count=1, held=held,
        started_at=_T0, finished_at=_T1, duration_ms=0)


def _run_ev(*, outcome="passed", steps=None, error=None, claim_version_seq=None):
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=4,
        claim_test_id=uuid4(), claim_version_seq=claim_version_seq,
        environment_id=7, api_choice="metadata_api", outcome=outcome,
        started_at=_T0, finished_at=_T1,
        steps=steps if steps is not None else (_read_ev(), _assert_ev()),
        error=error)


def test_maps_identity_and_outcome_to_typed_columns():
    ev = _run_ev()
    session = _FakeSession()
    returned = persist_run_evidence(session, ev)

    assert returned == ev.run_id
    assert session.flushed is True
    assert len(session.added) == 1
    row = session.added[0]
    assert isinstance(row, S4ExecutionRun)
    assert row.run_id == ev.run_id
    assert row.recipe_id == ev.recipe_id
    assert row.recipe_version_seq == 4
    assert row.claim_test_id == ev.claim_test_id
    assert row.claim_version_seq is None
    assert row.environment_id == 7
    assert row.outcome == "passed"
    assert row.started_at == _T0 and row.finished_at == _T1


def test_duration_ms_computed_from_run_window():
    row = _persist(_run_ev())
    assert row.duration_ms == 1000      # _T1 - _T0 = 1s


def test_evidence_jsonb_carries_trace_and_is_json_safe():
    ev = _run_ev()
    row = _persist(ev)
    trace = row.evidence
    # round-trips through JSON (proves no datetime/tuple/UUID leaked in).
    json.dumps(trace)
    assert trace["api_choice"] == "metadata_api"
    assert len(trace["steps"]) == 2
    read = trace["steps"][0]
    assert read["kind"] == "read"
    assert read["edge"] == "APPLIES_TO"
    assert read["subject_external_id"] == "Lead"
    assert read["started_at"] == _T0.isoformat()   # datetime -> ISO string
    assert isinstance(read["rows"], list)          # tuple -> list
    assert trace["steps"][1]["kind"] == "assert"
    assert trace["error"] is None


def test_evidence_jsonb_captures_error_surface():
    ev = _run_ev(
        outcome="errored",
        steps=(ReadEvidence(
            step_id="read-subject", ordinal=0, query="SELECT ...",
            sobject="ValidationRule", edge="APPLIES_TO",
            subject_entity_type="Object", subject_external_id="Lead",
            row_count=0, rows=(), started_at=_T0, finished_at=_T1,
            duration_ms=1000,
            error=ErrorSurface(phase="read", error_type="SFRequestError",
                               message="boom")),),
        error=ErrorSurface(phase="read", error_type="SFRequestError", message="boom"))
    row = _persist(ev)
    assert row.outcome == "errored"
    assert row.evidence["error"]["error_type"] == "SFRequestError"
    assert row.evidence["steps"][0]["error"]["phase"] == "read"


def test_claim_version_seq_preserved_when_present():
    row = _persist(_run_ev(claim_version_seq=3))
    assert row.claim_version_seq == 3


# --- D-275 Slice 3.2: run-all batch correlation columns ----------------------

def test_batch_columns_omitted_when_not_passed():
    """Deploy-safety: a single run never sets batch_id/source, so the attributes
    stay UNSET → SQLAlchemy omits them from the INSERT → the new columns are never
    referenced (safe pre/post the additive migration). They read back as None."""
    row = _persist(_run_ev())
    assert "batch_id" not in row.__dict__
    assert "source" not in row.__dict__
    assert row.batch_id is None
    assert row.source is None


def test_batch_columns_stamped_when_passed():
    bid = uuid4()
    session = _FakeSession()
    persist_run_evidence(session, _run_ev(),
                         batch_id=bid, source="runall_probe")
    row = session.added[0]
    assert row.batch_id == bid
    assert row.source == "runall_probe"


def _persist(ev):
    session = _FakeSession()
    persist_run_evidence(session, ev)
    return session.added[0]

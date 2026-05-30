"""Integration: S4 result store persist -> read-back (D-108.2 slice 3).

Per-tenant ``s4_execution_runs`` (the alembic tenant branch creates it in the
tenant_1 schema). Reuses this package's ``session`` fixture (transactional
rollback against the migrated substrate test DB) — the same fixture the S2
boundary tests use, because ``s4_execution_runs`` lives in the same tenant
schema and slice 4 will share a session with ``report_run_outcome``.

Verifies the persister writes a real row with the right typed columns + JSONB
trace, and that the row reads back by ``run_id`` (the slice-4 boundary key).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    ReadEvidence,
    RunEvidence,
)
from primeqa.execution_engine.result_store import (
    S4ExecutionRun,
    persist_run_evidence,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evidence(*, outcome="passed", row_count=1):
    t0 = _now()
    t1 = t0 + timedelta(milliseconds=1200)
    read = ReadEvidence(
        step_id="read-subject", ordinal=0,
        query="SELECT Id, ValidationName FROM ValidationRule WHERE "
              "EntityDefinition.QualifiedApiName = 'Lead'",
        sobject="ValidationRule", edge="APPLIES_TO",
        subject_entity_type="Object", subject_external_id="Lead",
        row_count=row_count,
        rows=tuple({"Id": f"03d{i}"} for i in range(row_count)),
        started_at=t0, finished_at=t1, duration_ms=1200)
    assertion = AssertEvidence(
        step_id="assert-edge", ordinal=1, predicate="exists",
        subject_ref="read-subject", evaluated_row_count=row_count,
        held=row_count > 0, started_at=t1, finished_at=t1, duration_ms=0)
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=2,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=7,
        api_choice="metadata_api", outcome=outcome,
        started_at=t0, finished_at=t1, steps=(read, assertion))


def test_persist_then_read_back_by_run_id(session):
    ev = _evidence(outcome="passed", row_count=3)
    run_id = persist_run_evidence(session, ev)
    assert run_id == ev.run_id

    row = (session.query(S4ExecutionRun)
           .filter(S4ExecutionRun.run_id == run_id).one())
    assert row.recipe_id == ev.recipe_id
    assert row.recipe_version_seq == 2
    assert row.claim_test_id == ev.claim_test_id
    assert row.environment_id == 7
    assert row.outcome == "passed"
    assert row.duration_ms == 1200
    # JSONB trace survives the round-trip to PG and back.
    assert row.evidence["api_choice"] == "metadata_api"
    assert len(row.evidence["steps"]) == 2
    assert row.evidence["steps"][0]["edge"] == "APPLIES_TO"
    assert row.evidence["steps"][0]["row_count"] == 3
    assert row.evidence["steps"][1]["held"] is True


def test_failed_run_persists_with_zero_rows(session):
    ev = _evidence(outcome="failed", row_count=0)
    persist_run_evidence(session, ev)
    row = (session.query(S4ExecutionRun)
           .filter(S4ExecutionRun.run_id == ev.run_id).one())
    assert row.outcome == "failed"
    # the query + filter are recoverable from the trace (S6 tells absent-object
    # from present-but-no-VR — S4 only records).
    assert "EntityDefinition.QualifiedApiName = 'Lead'" in row.evidence["steps"][0]["query"]
    assert row.evidence["steps"][0]["row_count"] == 0


def test_outcome_column_uses_run_outcome_enum(session):
    # The reused run_outcome enum accepts all four values at the DB level.
    for oc in ("passed", "failed", "errored", "skipped"):
        ev = _evidence(outcome="passed")
        # rebuild with the target outcome (RunEvidence.outcome is Literal but
        # the column is the enum; persist whatever the run produced).
        ev = RunEvidence(
            run_id=uuid4(), recipe_id=ev.recipe_id,
            recipe_version_seq=ev.recipe_version_seq,
            claim_test_id=ev.claim_test_id, claim_version_seq=None,
            environment_id=ev.environment_id, api_choice="metadata_api",
            outcome=oc, started_at=ev.started_at, finished_at=ev.finished_at,
            steps=ev.steps)
        persist_run_evidence(session, ev)
        got = session.execute(
            text("SELECT outcome FROM s4_execution_runs WHERE run_id = :rid"),
            {"rid": str(ev.run_id)}).scalar_one()
        assert got == oc

"""Integration: S4 finalize_run — persist + posture, atomic on one session
(D-108.3 slice 4).

Reuses this package's ``session`` fixture (transactional rollback, the migrated
substrate test DB). Seeds a real approved claim + recipe via the
``test_representation`` fixtures so ``report_run_outcome`` attaches runtime state
to a real recipe, then finalizes a run and asserts BOTH the
``s4_execution_runs`` row and the ``test_recipe_runtime_state`` row land in one
transaction.

Note on idempotency: ``finalize_run`` persists first, and ``s4_execution_runs``
has a ``run_id`` PK — so a true *re-finalize of the same run_id* is rejected at
persist (runs are never silently duplicated; the executor mints a fresh run_id
per run). The idempotency the design relies on lives at the *posture* layer:
``report_run_outcome`` is first-write-wins on ``last_run_id``. Both are covered
below.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    ReadEvidence,
    RunEvidence,
)
from primeqa.execution_engine.finalize import finalize_run
from primeqa.execution_engine.result_store import S4ExecutionRun, persist_run_evidence
from primeqa.test_representation import SemanticTransactionCoordinator

from ._fixtures import arrange_approved_test_with_recipes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evidence_for(recipe_id, *, outcome="passed", row_count=1, finished_at=None):
    t0 = _now()
    t1 = finished_at or (t0 + timedelta(milliseconds=900))
    read = ReadEvidence(
        step_id="read-subject", ordinal=0,
        query="SELECT Id FROM ValidationRule WHERE "
              "EntityDefinition.QualifiedApiName = 'Lead'",
        sobject="ValidationRule", edge="APPLIES_TO",
        subject_entity_type="Object", subject_external_id="Lead",
        row_count=row_count, rows=tuple({"Id": f"0{i}"} for i in range(row_count)),
        started_at=t0, finished_at=t1, duration_ms=900)
    assertion = AssertEvidence(
        step_id="assert-edge", ordinal=1, predicate="exists",
        subject_ref="read-subject", evaluated_row_count=row_count,
        held=row_count > 0, started_at=t1, finished_at=t1, duration_ms=0)
    return RunEvidence(
        run_id=uuid4(), recipe_id=recipe_id, recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=7,
        api_choice="metadata_api", outcome=outcome,
        started_at=t0, finished_at=t1, steps=(read, assertion))


def _seed_recipe(session):
    coord = SemanticTransactionCoordinator()
    _test_id, recipe_ids = arrange_approved_test_with_recipes(
        session, coord, num_recipes=1)
    return recipe_ids[0], coord


def test_finalize_writes_both_rows_atomically(session):
    recipe_id, coord = _seed_recipe(session)
    ev = _evidence_for(recipe_id, outcome="passed", row_count=2)

    state = finalize_run(session, ev)

    # 1) the S4 result-store row landed.
    run_row = (session.query(S4ExecutionRun)
               .filter(S4ExecutionRun.run_id == ev.run_id).one())
    assert run_row.recipe_id == recipe_id
    assert run_row.outcome == "passed"
    assert run_row.evidence["steps"][0]["row_count"] == 2

    # 2) the S2 runtime-state row landed — the mapped fields.
    assert state.recipe_id == recipe_id
    assert state.last_run_id == ev.run_id                  # run_id -> last_run_id
    assert state.last_run_outcome == "passed"              # outcome -> last_run_outcome
    assert state.last_run_at == ev.finished_at             # finished_at -> last_run_at
    assert state.last_run_recipe_version_seq == 1
    assert state.last_pass_at == ev.finished_at            # accumulated on pass
    assert state.last_failure_at is None

    # 3) same row visible via the coordinator read surface.
    read_back = coord.get_recipe_runtime_state(session, recipe_id)
    assert read_back.last_run_id == ev.run_id


def test_runtime_state_advances_across_runs(session):
    recipe_id, coord = _seed_recipe(session)
    ev_pass = _evidence_for(recipe_id, outcome="passed")
    ev_fail = _evidence_for(
        recipe_id, outcome="failed", row_count=0,
        finished_at=ev_pass.finished_at + timedelta(seconds=5))

    finalize_run(session, ev_pass)
    state = finalize_run(session, ev_fail)

    # latest run wins; pass/failure timestamps accumulate independently.
    assert state.last_run_id == ev_fail.run_id
    assert state.last_run_outcome == "failed"
    assert state.last_pass_at == ev_pass.finished_at       # preserved from run 1
    assert state.last_failure_at == ev_fail.finished_at    # set by run 2
    # both result-store rows persist (distinct run_ids).
    assert session.query(S4ExecutionRun).filter(
        S4ExecutionRun.recipe_id == recipe_id).count() == 2


def test_posture_idempotent_on_same_run_id(session):
    # report_run_outcome is first-write-wins on last_run_id: a posture-only
    # retry of the same run does NOT overwrite. (finalize_run persists first, so
    # re-finalizing is guarded by the PK — see the next test; here we exercise
    # the posture layer's idempotency directly, the property the design names.)
    recipe_id, coord = _seed_recipe(session)
    ev = _evidence_for(recipe_id, outcome="passed")
    finalize_run(session, ev)

    again = coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id, last_run_id=ev.run_id,
        last_run_at=ev.finished_at + timedelta(seconds=99),    # different ts
        last_run_outcome="failed",                             # different outcome
        last_run_recipe_version_seq=2)
    # no-op: original passed-run state preserved.
    assert again.last_run_outcome == "passed"
    assert again.last_run_at == ev.finished_at


def test_refinalize_same_run_id_is_rejected_by_pk(session):
    # Runs are never silently duplicated: a second persist of the same run_id
    # violates the s4_execution_runs PK (the executor mints a fresh run_id per
    # run, so this only fires on a buggy double-finalize).
    recipe_id, _ = _seed_recipe(session)
    ev = _evidence_for(recipe_id)
    persist_run_evidence(session, ev)
    with pytest.raises(IntegrityError):
        persist_run_evidence(session, ev)

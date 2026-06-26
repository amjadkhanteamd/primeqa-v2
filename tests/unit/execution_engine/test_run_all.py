"""Unit tests for the run-all execution path (D-277, S6 Slice 3.3) — fake
session / coordinator / executor, no real DB.

The load-bearing property is PER-PROBE FAILURE ISOLATION + COMPLETENESS: every
applicable recipe of a claim runs in one batch (one ``batch_id``), and a probe
that errors mid-batch STILL persists a batch-stamped errored row (so "ran and
errored" stays distinct from "never ran" — the distinction Slice 4's
completeness check reads). The S6 interpret stage is best-effort and no-ops on a
fake session, so only the s4_execution_runs rows are captured.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit

from primeqa.execution_engine.evidence import RunEvidence
from primeqa.execution_engine.result_store import S4ExecutionRun
from primeqa.execution_engine.run import (
    run_all_recipes_execution,
    run_all_recipes_execution_async,
)

_T = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)


class _FakeSession:
    """Captures added rows; models SAVEPOINT rollback (begin_nested drops rows
    added in a failed block, like a real Postgres savepoint). ``fail_flush_on``
    makes the Nth flush() raise — to exercise the persist-failure isolation path."""

    def __init__(self):
        self.added = []
        self.flush_calls = 0
        self.fail_flush_on = None

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flush_calls += 1
        if self.fail_flush_on is not None and self.flush_calls == self.fail_flush_on:
            raise RuntimeError("simulated flush failure")

    @contextmanager
    def begin_nested(self):
        mark = len(self.added)
        try:
            yield
        except Exception:
            del self.added[mark:]          # savepoint rollback
            raise


class _FakeCoord:
    """Stands in for the coordinator: returns a fixed recipe set + a no-op posture
    callback (so finalize_run's report_run_outcome doesn't need a DB)."""

    def __init__(self, recipes):
        self._recipes = list(recipes)
        self.posture_calls = []

    def select_recipes_for_execution(self, session, test_id, **kwargs):
        return list(self._recipes)

    def report_run_outcome(self, session, **kwargs):
        self.posture_calls.append(kwargs)
        return None


def _fake_recipe(recipe_kind: str = "data-recipe"):
    # the run-all loop + synth only read these attrs; execute_fn is faked.
    return SimpleNamespace(
        recipe_id=uuid4(), version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None,
        recipe_kind=recipe_kind)


def _passed_evidence(recipe, environment_id):
    return RunEvidence(
        run_id=uuid4(), recipe_id=recipe.recipe_id, recipe_version_seq=recipe.version_seq,
        claim_test_id=recipe.claim_test_id, claim_version_seq=recipe.claim_version_seq,
        environment_id=environment_id, api_choice="rest", outcome="passed",
        started_at=_T, finished_at=_T, steps=(), error=None)


def _s4_rows(session):
    return [r for r in session.added if isinstance(r, S4ExecutionRun)]


# --- the test that matters: failure isolation + completeness -----------------

def test_runall_one_probe_errors_still_persists_a_batch_stamped_errored_row():
    recipes = [_fake_recipe() for _ in range(3)]
    erroring = recipes[1].recipe_id

    def fake_execute(recipe, session, environment_id, client, **kwargs):
        if recipe.recipe_id == erroring:
            raise RuntimeError("simulated probe failure")     # raises mid-batch
        return _passed_evidence(recipe, environment_id)

    coord = _FakeCoord(recipes)
    session = _FakeSession()
    result = run_all_recipes_execution(
        session, uuid4(), environment_id=7, coordinator=coord, execute_fn=fake_execute)

    rows = _s4_rows(session)
    # N applicable recipes → N batch-stamped run rows (the failed one INCLUDED).
    assert len(rows) == 3
    assert result.ran is True and len(result.probes) == 3
    # exactly one errored, the other two ran normally.
    assert sum(1 for r in rows if r.outcome == "errored") == 1
    assert sum(1 for r in rows if r.outcome == "passed") == 2
    # one batch_id across ALL rows (incl. the errored one — not orphaned).
    assert len({r.batch_id for r in rows}) == 1
    assert all(r.batch_id == result.batch_id for r in rows)
    assert all(r.batch_id is not None for r in rows)
    assert all(r.source == "runall_probe" for r in rows)


def test_runall_probe_executor_returns_errored_evidence_is_persisted():
    # the common case: the executor RETURNS errored evidence (not a raise) — it is
    # persisted batch-stamped just the same.
    recipes = [_fake_recipe(), _fake_recipe()]
    errored_rid = recipes[0].recipe_id

    def fake_execute(recipe, session, environment_id, client, **kwargs):
        ev = _passed_evidence(recipe, environment_id)
        if recipe.recipe_id == errored_rid:
            ev = RunEvidence(**{**ev.__dict__, "outcome": "errored"})
        return ev

    session = _FakeSession()
    result = run_all_recipes_execution(
        session, uuid4(), environment_id=7,
        coordinator=_FakeCoord(recipes), execute_fn=fake_execute)
    rows = _s4_rows(session)
    assert len(rows) == 2
    assert sum(1 for r in rows if r.outcome == "errored") == 1
    assert len({r.batch_id for r in rows}) == 1


def test_runall_all_passed_batch():
    recipes = [_fake_recipe() for _ in range(3)]
    session = _FakeSession()
    result = run_all_recipes_execution(
        session, uuid4(), environment_id=7, coordinator=_FakeCoord(recipes),
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))
    rows = _s4_rows(session)
    assert len(rows) == 3 and all(r.outcome == "passed" for r in rows)
    assert len({r.batch_id for r in rows}) == 1
    assert result.ran is True and len(result.probes) == 3


def test_runall_empty_applicable_set_persists_nothing():
    session = _FakeSession()
    result = run_all_recipes_execution(
        session, uuid4(), environment_id=7, coordinator=_FakeCoord([]),
        execute_fn=lambda *a, **k: pytest.fail("execute must not be called"))
    assert result.ran is False
    assert result.probes == ()
    assert _s4_rows(session) == []


def test_runall_batch_id_minted_once_per_invocation():
    recipes = [_fake_recipe() for _ in range(2)]
    s1, s2 = _FakeSession(), _FakeSession()
    r1 = run_all_recipes_execution(s1, uuid4(), environment_id=7,
                                   coordinator=_FakeCoord(recipes),
                                   execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))
    r2 = run_all_recipes_execution(s2, uuid4(), environment_id=7,
                                   coordinator=_FakeCoord(recipes),
                                   execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))
    assert r1.batch_id != r2.batch_id                  # a fresh batch_id per call


# --- async variant: bracketed shape, same batch semantics --------------------

def test_async_runall_persists_batch_stamped_rows(monkeypatch):
    monkeypatch.setattr("primeqa.execution_engine.run._resolve_env_gate",
                        lambda session, environment_id: ("full", False))
    recipes = [_fake_recipe(recipe_kind="metadata-recipe") for _ in range(2)]
    captured = _FakeSession()

    @contextmanager
    def fake_scope(tenant_id):
        yield captured

    result = run_all_recipes_execution_async(
        1, uuid4(), environment_id=7, client=object(),
        coordinator=_FakeCoord(recipes), session_scope=fake_scope,
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))
    rows = _s4_rows(captured)
    assert len(rows) == 2
    assert len({r.batch_id for r in rows}) == 1
    assert all(r.source == "runall_probe" for r in rows)
    assert result.ran is True and len(result.probes) == 2


def test_async_runall_one_probe_raises_still_persists_errored(monkeypatch):
    monkeypatch.setattr("primeqa.execution_engine.run._resolve_env_gate",
                        lambda session, environment_id: ("full", False))
    recipes = [_fake_recipe(recipe_kind="metadata-recipe") for _ in range(3)]
    erroring = recipes[2].recipe_id
    captured = _FakeSession()

    @contextmanager
    def fake_scope(tenant_id):
        yield captured

    def fake_execute(recipe, session, environment_id, client, **kwargs):
        if recipe.recipe_id == erroring:
            raise RuntimeError("simulated async probe failure")
        return _passed_evidence(recipe, environment_id)

    result = run_all_recipes_execution_async(
        1, uuid4(), environment_id=7, client=object(),
        coordinator=_FakeCoord(recipes), session_scope=fake_scope,
        execute_fn=fake_execute)
    rows = _s4_rows(captured)
    assert len(rows) == 3
    assert sum(1 for r in rows if r.outcome == "errored") == 1
    assert len({r.batch_id for r in rows}) == 1


def test_async_runall_empty_set_persists_nothing():
    @contextmanager
    def fake_scope(tenant_id):
        yield _FakeSession()

    result = run_all_recipes_execution_async(
        1, uuid4(), environment_id=7, coordinator=_FakeCoord([]),
        session_scope=fake_scope,
        execute_fn=lambda *a, **k: pytest.fail("execute must not be called"))
    assert result.ran is False and result.probes == ()


# --- persist (finalize) failure must NOT abort the batch (review fix) ---------

def test_sync_runall_one_probe_persist_fails_others_still_persist():
    # a probe whose finalize/persist FLUSH fails rolls back to its savepoint
    # (no row) and the loop continues — the batch is not aborted.
    recipes = [_fake_recipe() for _ in range(3)]
    session = _FakeSession()
    session.fail_flush_on = 2          # the 2nd probe's persist flush raises
    result = run_all_recipes_execution(
        session, uuid4(), environment_id=7, coordinator=_FakeCoord(recipes),
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))
    rows = _s4_rows(session)
    assert len(rows) == 2                          # the failed probe left NO row
    assert len(result.probes) == 2                 # it is skipped; the batch continued
    assert len({r.batch_id for r in rows}) == 1


def test_async_runall_one_probe_persist_fails_others_still_persist():
    recipes = [_fake_recipe(recipe_kind="metadata-recipe") for _ in range(3)]
    captured = _FakeSession()
    captured.fail_flush_on = 2

    @contextmanager
    def fake_scope(tenant_id):
        mark = len(captured.added)
        try:
            yield captured
        except Exception:
            del captured.added[mark:]      # fresh-scope rollback on a probe failure
            raise

    result = run_all_recipes_execution_async(
        1, uuid4(), environment_id=7, client=object(),
        coordinator=_FakeCoord(recipes), session_scope=fake_scope,
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))
    rows = _s4_rows(captured)
    assert len(rows) == 2 and len(result.probes) == 2   # one probe skipped, batch ran on

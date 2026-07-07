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


def _noop_manifest(*a, **k):
    """Stub for the Slice 4c.0 batch-manifest writer — these loop-isolation tests
    don't exercise the manifest, and the real writer would open a tenant DB conn
    (these tests pass tenant_id=1 but run with no DB). The manifest write has its
    own dedicated tests below."""
    return None


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
    callback (so finalize_run's report_run_outcome doesn't need a DB).
    D-300 B2: ``authored`` (defaults to the data-recipe selection ids) is the
    status-blind data-probe membership the manifest records."""

    def __init__(self, recipes, authored=None):
        self._recipes = list(recipes)
        self._authored = authored
        self.posture_calls = []

    def select_recipes_for_execution(self, session, test_id, **kwargs):
        return list(self._recipes)

    def current_data_recipe_ids(self, session, test_id):
        if self._authored is not None:
            return list(self._authored)
        return [r.recipe_id for r in self._recipes
                if getattr(r, "recipe_kind", None) == "data-recipe"]

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
    # D-300.1: these tests exercise loop MECHANICS with cheap metadata fakes
    # (data-recipe prep needs real bodies); probe-set MEMBERSHIP has its own
    # tests — bypass the filter here.
    monkeypatch.setattr("primeqa.execution_engine.run._probe_recipes", lambda rs: rs)
    recipes = [_fake_recipe(recipe_kind="metadata-recipe") for _ in range(2)]
    captured = _FakeSession()

    @contextmanager
    def fake_scope(tenant_id):
        yield captured

    result = run_all_recipes_execution_async(
        1, uuid4(), environment_id=7, client=object(),
        coordinator=_FakeCoord(recipes), session_scope=fake_scope,
        manifest_writer=_noop_manifest,
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))
    rows = _s4_rows(captured)
    assert len(rows) == 2
    assert len({r.batch_id for r in rows}) == 1
    assert all(r.source == "runall_probe" for r in rows)
    assert result.ran is True and len(result.probes) == 2


def test_async_runall_one_probe_raises_still_persists_errored(monkeypatch):
    monkeypatch.setattr("primeqa.execution_engine.run._resolve_env_gate",
                        lambda session, environment_id: ("full", False))
    # D-300.1: these tests exercise loop MECHANICS with cheap metadata fakes
    # (data-recipe prep needs real bodies); probe-set MEMBERSHIP has its own
    # tests — bypass the filter here.
    monkeypatch.setattr("primeqa.execution_engine.run._probe_recipes", lambda rs: rs)
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
        manifest_writer=_noop_manifest,
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
        session_scope=fake_scope, manifest_writer=_noop_manifest,
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


def test_async_runall_one_probe_persist_fails_others_still_persist(monkeypatch):
    # D-300.1: mechanics-only — bypass the probe-set filter (see note above).
    monkeypatch.setattr("primeqa.execution_engine.run._probe_recipes", lambda rs: rs)
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
        manifest_writer=_noop_manifest,
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))
    rows = _s4_rows(captured)
    assert len(rows) == 2 and len(result.probes) == 2   # one probe skipped, batch ran on


# --- Slice 4c.0 (D-281): the batch manifest — written EARLY, before probes ----

def test_sync_runall_writes_manifest_before_probes():
    # the manifest (the EXPECTED applicable set) is recorded FIRST — before any
    # probe executes — with the threaded tenant_id, the batch_id, and the full set.
    recipes = [_fake_recipe() for _ in range(3)]
    events = []

    def rec_manifest(tenant_id, batch_id, test_id, recipe_ids, **k):
        events.append(("manifest", tenant_id, batch_id, tuple(recipe_ids)))

    def rec_execute(recipe, *a, **k):
        events.append(("execute", recipe.recipe_id))
        return _passed_evidence(recipe, 7)

    tid = uuid4()
    result = run_all_recipes_execution(
        _FakeSession(), tid, environment_id=7, tenant_id=42,
        coordinator=_FakeCoord(recipes),
        execute_fn=rec_execute, manifest_writer=rec_manifest)

    assert events[0][0] == "manifest"                       # FIRST, before probes
    assert events[0][1] == 42                               # tenant threaded through
    assert events[0][2] == result.batch_id                 # the batch's id
    assert events[0][3] == tuple(r.recipe_id for r in recipes)   # the applicable set
    assert [e[0] for e in events[1:]] == ["execute", "execute", "execute"]


def test_sync_runall_empty_set_writes_no_manifest():
    # no applicable set ⇒ no batch runs ⇒ no manifest (the writer is never called).
    called = []
    result = run_all_recipes_execution(
        _FakeSession(), uuid4(), environment_id=7, tenant_id=42,
        coordinator=_FakeCoord([]),
        execute_fn=lambda *a, **k: pytest.fail("execute must not run"),
        manifest_writer=lambda *a, **k: called.append(a))
    assert result.ran is False
    assert called == []


def test_sync_runall_manifest_written_even_when_a_probe_errors():
    # the manifest precedes the loop, so a probe erroring mid-batch leaves the
    # manifest intact — the "crashed-early ⇒ readable as incomplete" property,
    # modeled at the writer-call level (the real durability = own txn, scratch-DB
    # tested).
    recipes = [_fake_recipe() for _ in range(3)]
    manifests = []
    erroring = recipes[1].recipe_id

    def rec_manifest(tenant_id, batch_id, test_id, recipe_ids, **k):
        manifests.append((batch_id, tuple(recipe_ids)))

    def fake_execute(recipe, *a, **k):
        if recipe.recipe_id == erroring:
            raise RuntimeError("probe blew up mid-batch")
        return _passed_evidence(recipe, 7)

    result = run_all_recipes_execution(
        _FakeSession(), uuid4(), environment_id=7, tenant_id=42,
        coordinator=_FakeCoord(recipes),
        execute_fn=fake_execute, manifest_writer=rec_manifest)
    assert len(manifests) == 1
    assert manifests[0][0] == result.batch_id
    assert manifests[0][1] == tuple(r.recipe_id for r in recipes)   # FULL expected set


def test_sync_runall_no_tenant_id_skips_manifest_default_writer():
    # the DEFAULT writer no-ops when tenant_id is None (no tenant context) — the
    # loop still runs (existing run-all tests rely on this). Proven by: a run with
    # tenant_id unset + the default writer does not raise and runs all probes.
    recipes = [_fake_recipe() for _ in range(2)]
    result = run_all_recipes_execution(
        _FakeSession(), uuid4(), environment_id=7,        # no tenant_id
        coordinator=_FakeCoord(recipes),
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))
    assert result.ran is True and len(result.probes) == 2


def test_async_runall_writes_manifest_with_expected_set(monkeypatch):
    monkeypatch.setattr("primeqa.execution_engine.run._resolve_env_gate",
                        lambda session, environment_id: ("full", False))
    # D-300.1: these tests exercise loop MECHANICS with cheap metadata fakes
    # (data-recipe prep needs real bodies); probe-set MEMBERSHIP has its own
    # tests — bypass the filter here.
    monkeypatch.setattr("primeqa.execution_engine.run._probe_recipes", lambda rs: rs)
    recipes = [_fake_recipe(recipe_kind="metadata-recipe") for _ in range(2)]
    captured = _FakeSession()

    @contextmanager
    def fake_scope(tenant_id):
        yield captured

    events = []

    def rec_manifest(tenant_id, batch_id, test_id, recipe_ids, **k):
        events.append(("manifest", tuple(recipe_ids)))

    def rec_execute(recipe, *a, **k):
        events.append(("execute", recipe.recipe_id))
        return _passed_evidence(recipe, 7)

    run_all_recipes_execution_async(
        7, uuid4(), environment_id=7, client=object(),
        # D-300 B2: the manifest records the AUTHORED membership — declare it
        # on the fake (these mechanics fakes are metadata-kind, so the default
        # data-recipe derivation would be empty).
        coordinator=_FakeCoord(recipes, authored=[r.recipe_id for r in recipes]),
        session_scope=fake_scope,
        execute_fn=rec_execute, manifest_writer=rec_manifest)

    # the manifest is recorded BEFORE any probe executes, carrying the AUTHORED set.
    assert events[0][0] == "manifest"
    assert events[0][1] == tuple(r.recipe_id for r in recipes)


# --- D-300.1: probe-set membership — data probes only ------------------------

def test_runall_filters_metadata_recipes_from_batch_and_manifest():
    # A mixed applicable set: the primary reject (data, 0), a boundary accept
    # probe (data, -1), and the D-228 inspection fallback (metadata, -10). The
    # batch AND the manifest must both carry ONLY the two data probes — a
    # metadata read never enters a boundary strict-AND.
    data_primary = _fake_recipe(recipe_kind="data-recipe")
    data_boundary = _fake_recipe(recipe_kind="data-recipe")
    inspection = _fake_recipe(recipe_kind="metadata-recipe")
    session = _FakeSession()
    manifests = []

    def rec_manifest(tenant_id, batch_id, test_id, recipe_ids, **k):
        manifests.append(tuple(recipe_ids))

    result = run_all_recipes_execution(
        session, uuid4(), environment_id=7, client=object(),
        coordinator=_FakeCoord([data_primary, data_boundary, inspection]),
        tenant_id=1, manifest_writer=rec_manifest,
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))

    assert result.ran is True
    assert {p.recipe_id for p in result.probes} == {
        data_primary.recipe_id, data_boundary.recipe_id}
    assert manifests == [(data_primary.recipe_id, data_boundary.recipe_id)]


def test_runall_all_metadata_set_is_no_eligible_recipes():
    # An applicable set with NO data probe runs nothing: no batch, no manifest —
    # the honest not-Verified outcome for an env with no data-probe capability.
    session = _FakeSession()
    manifests = []

    def rec_manifest(tenant_id, batch_id, test_id, recipe_ids, **k):
        manifests.append(tuple(recipe_ids))

    result = run_all_recipes_execution(
        session, uuid4(), environment_id=7, client=object(),
        coordinator=_FakeCoord([_fake_recipe(recipe_kind="metadata-recipe")]),
        tenant_id=1, manifest_writer=rec_manifest,
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))

    assert result.ran is False and result.reason == "no_eligible_recipes"
    assert manifests == [] and _s4_rows(session) == []


def test_async_runall_filters_metadata_from_manifest(monkeypatch):
    # The async path applies the SAME filter between selection and manifest.
    monkeypatch.setattr("primeqa.execution_engine.run._resolve_env_gate",
                        lambda session, environment_id: ("full", False))
    monkeypatch.setattr("primeqa.execution_engine.run._prepare_async_execute",
                        lambda *a, **k: (object(), None, frozenset()))
    data_probe = _fake_recipe(recipe_kind="data-recipe")
    inspection = _fake_recipe(recipe_kind="metadata-recipe")
    captured = _FakeSession()
    manifests = []

    @contextmanager
    def fake_scope(tenant_id):
        yield captured

    def rec_manifest(tenant_id, batch_id, test_id, recipe_ids, **k):
        manifests.append(tuple(recipe_ids))

    result = run_all_recipes_execution_async(
        1, uuid4(), environment_id=7, client=object(),
        coordinator=_FakeCoord([data_probe, inspection]),
        session_scope=fake_scope, manifest_writer=rec_manifest,
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))

    assert manifests == [(data_probe.recipe_id,)]
    assert [p.recipe_id for p in result.probes] == [data_probe.recipe_id]


def test_runall_manifest_records_authored_membership_not_approved_subset():
    # D-300 review-fix B2 (the partial-approval wrong-green): the manifest
    # records the claim's AUTHORED data-probe membership. When the approved/
    # selectable subset is SMALLER (a member unapproved or deprecated), the
    # batch runs the subset but the manifest expects the full membership —
    # the completeness reader then marks the batch INCOMPLETE -> not-Verified.
    approved_probe = _fake_recipe(recipe_kind="data-recipe")
    unapproved_id = uuid4()                      # authored but never selectable
    session = _FakeSession()
    manifests = []

    def rec_manifest(tenant_id, batch_id, test_id, recipe_ids, **k):
        manifests.append(tuple(recipe_ids))

    result = run_all_recipes_execution(
        session, uuid4(), environment_id=7, client=object(),
        coordinator=_FakeCoord([approved_probe],
                               authored=[approved_probe.recipe_id, unapproved_id]),
        tenant_id=1, manifest_writer=rec_manifest,
        execute_fn=lambda recipe, *a, **k: _passed_evidence(recipe, 7))

    assert result.ran is True and len(result.probes) == 1   # only the approved ran
    assert manifests == [(approved_probe.recipe_id, unapproved_id)]  # both expected

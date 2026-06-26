"""Unit + integration tests for the S6 batch reader + completeness gate
(D-282, Slice 4c.1). The LOAD-BEARING properties (the deliverable):

  * **row-ABSENT ⇒ INCOMPLETE ⇒ not-Verified, with NO ProbeResults built** (the
    failure mode that, mishandled, silently passes). Tested pure + live.
  * **DRIFT-IMMUNITY** — completeness is a function of the persisted manifest, NOT
    a recompute of ``select_recipes_for_execution`` (patched to RAISE; the reader
    still works).
  * errored-row-PRESENT is COMPLETE-but-indeterminate (flows to the bva arm);
    most-recent batch by ``max(finished_at)`` not ``batch_id``; no fallback to an
    older complete batch.

Pure / fake-session tests run with no DB. One integration test (``@integration``,
seed-in-txn-rollback) proves the SQL + the most-recent ORDERING against the DB.
"""
from __future__ import annotations

import os
import json
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.unit

from primeqa.interpretation import batch_reader as BR
from primeqa.interpretation.batch_reader import (
    BatchCompleteness,
    _assess,
    _extract_expect_rejection,
    read_batch_completeness,
    select_most_recent_batch,
)
from primeqa.interpretation.strategy import apply_strategy


def _row(recipe_id, outcome, *, body=None):
    return {"recipe_id": recipe_id, "outcome": outcome, "body": body,
            "run_id": uuid4(), "s6_verdict": None}


# === 1. _assess — the completeness decision (pure; the load-bearing logic) =====

def test_assess_complete_all_pass_builds_passing_probes():
    r1, r2 = uuid4(), uuid4()
    complete, probes = _assess([r1, r2], [_row(r1, "passed"), _row(r2, "passed")])
    assert complete is True
    assert [p.outcome for p in probes] == ["pass", "pass"]
    # a downstream bva grade over a complete all-pass set is Verified.
    assert apply_strategy(None, "bva", list(probes)) is True


def test_assess_complete_one_errored_is_complete_but_indeterminate():
    # errored-row-PRESENT: the row exists → COMPLETE (membership satisfied); the
    # probe is indeterminate → the bva arm returns not-Verified.
    r1, r2 = uuid4(), uuid4()
    complete, probes = _assess([r1, r2], [_row(r1, "passed"), _row(r2, "errored")])
    assert complete is True
    assert sorted(p.outcome for p in probes) == ["indeterminate", "pass"]
    assert apply_strategy(None, "bva", list(probes)) is False     # indeterminate sinks it


def test_assess_incomplete_row_absent_short_circuits_no_probes():
    # THE LOAD-BEARING TEST. An expected recipe has NO row → INCOMPLETE → no
    # ProbeResults are built and the bva arm is never reached. probes is None.
    r1, r2, r3 = uuid4(), uuid4(), uuid4()
    complete, probes = _assess([r1, r2, r3], [_row(r1, "passed"), _row(r2, "passed")])
    assert complete is False
    assert probes is None              # NOTHING built — the arm cannot be called


def test_assess_extra_row_is_still_complete_subset_not_equality():
    # ⊆ not == : an extra present row (not expected) never makes it incomplete —
    # the load-bearing direction is "every EXPECTED recipe has a row".
    r1, r2, extra = uuid4(), uuid4(), uuid4()
    complete, probes = _assess(
        [r1, r2], [_row(r1, "passed"), _row(r2, "passed"), _row(extra, "failed")])
    assert complete is True
    assert len(probes) == 2            # one per EXPECTED recipe, not per row


def test_assess_probes_are_in_manifest_order():
    r1, r2, r3 = uuid4(), uuid4(), uuid4()
    rows = [_row(r3, "errored"), _row(r1, "passed"), _row(r2, "failed")]   # shuffled
    complete, probes = _assess([r1, r2, r3], rows)
    assert complete is True
    assert [p.outcome for p in probes] == ["pass", "fail", "indeterminate"]   # r1,r2,r3


def test_assess_one_failed_is_complete_and_bva_not_verified():
    r1, r2 = uuid4(), uuid4()
    complete, probes = _assess([r1, r2], [_row(r1, "passed"), _row(r2, "failed")])
    assert complete is True
    assert apply_strategy(None, "bva", list(probes)) is False


# === 2. _extract_expect_rejection — polarity from the recipe body (pure) =======

def test_extract_expect_rejection_reject_body_true():
    body = {"kind": "data-recipe", "steps": [
        {"kind": "create", "expect_rejection": {"reason": "FLS"}}]}
    assert _extract_expect_rejection(body) is True


def test_extract_expect_rejection_plain_body_false():
    body = {"kind": "data-recipe", "steps": [
        {"kind": "create", "expect_rejection": None}, {"kind": "assert"}]}
    assert _extract_expect_rejection(body) is False


@pytest.mark.parametrize("body", [None, {}, {"steps": None}, "not-a-dict", 5])
def test_extract_expect_rejection_tolerant_of_missing_body(body):
    assert _extract_expect_rejection(body) is False


# === 3. read_batch_completeness — orchestration (fake session, no DB) ==========

class _R:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Dispatches by statement IDENTITY (the module-level text() objects) and
    returns canned results — so the reader runs with no DB and we can prove it
    never touches anything else (e.g. select_recipes_for_execution)."""

    def __init__(self, *, most_recent=None, manifest=None, batch_rows=None):
        self.most_recent = most_recent          # a batch_id or None
        self.manifest = manifest                # expected_recipe_ids list, or None
        self.batch_rows = batch_rows or []

    def execute(self, stmt, params=None):
        if stmt is BR._MOST_RECENT_BATCH_SQL:
            return _R([(self.most_recent, None)] if self.most_recent is not None else [])
        if stmt is BR._MANIFEST_SQL:
            return _R([(self.manifest,)] if self.manifest is not None else [])
        if stmt is BR._BATCH_ROWS_SQL:
            return _R(self.batch_rows)
        raise AssertionError(f"unexpected statement: {stmt}")


def test_read_complete_batch_returns_probes():
    bid, r1, r2 = uuid4(), uuid4(), uuid4()
    s = _FakeSession(most_recent=bid, manifest=[r1, r2],
                     batch_rows=[_row(r1, "passed"), _row(r2, "passed")])
    res = read_batch_completeness(s, uuid4())
    assert res.complete is True and res.reason == "complete"
    assert res.batch_id == bid and len(res.probes) == 2
    assert apply_strategy(None, "bva", list(res.probes)) is True


def test_read_incomplete_batch_returns_no_probes():
    bid, r1, r2, r3 = uuid4(), uuid4(), uuid4(), uuid4()
    s = _FakeSession(most_recent=bid, manifest=[r1, r2, r3],
                     batch_rows=[_row(r1, "passed"), _row(r2, "passed")])  # r3 absent
    res = read_batch_completeness(s, uuid4())
    assert res.complete is False and res.reason == "incomplete"
    assert res.probes is None


def test_read_no_batch_returns_no_batch_signal():
    res = read_batch_completeness(_FakeSession(most_recent=None), uuid4())
    assert res.complete is False and res.probes is None and res.reason == "no_batch"


def test_read_missing_manifest_is_not_completeness_knowable():
    bid = uuid4()
    s = _FakeSession(most_recent=bid, manifest=None, batch_rows=[_row(uuid4(), "passed")])
    res = read_batch_completeness(s, uuid4())
    assert res.complete is False and res.probes is None and res.reason == "no_manifest"


def test_read_never_calls_select_recipes_for_execution(monkeypatch):
    # DRIFT-IMMUNITY / no-live-env-resolve: the completeness path reads ONLY the
    # persisted manifest. Patch select_recipes_for_execution to RAISE — the reader
    # must still work (it never calls it), proving it doesn't recompute.
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator

    def _boom(*a, **k):
        raise AssertionError("read path must NOT recompute the applicable set")

    monkeypatch.setattr(SemanticTransactionCoordinator,
                        "select_recipes_for_execution", _boom)
    monkeypatch.setattr(SemanticTransactionCoordinator,
                        "select_recipe_for_execution", _boom)
    bid, r1, r2 = uuid4(), uuid4(), uuid4()
    s = _FakeSession(most_recent=bid, manifest=[r1, r2],
                     batch_rows=[_row(r1, "passed"), _row(r2, "passed")])
    res = read_batch_completeness(s, uuid4())        # does not raise
    assert res.complete is True


# === 4. integration — SQL + most-recent ORDERING (by finished_at, not batch_id) =

@pytest.mark.integration
def test_most_recent_by_finished_at_not_batch_id_and_no_fallback():
    """Seed TWO batches for one claim: the LATER (by finished_at) is INCOMPLETE,
    the EARLIER is COMPLETE, and the later batch's uuid is LEXICALLY SMALLER (so a
    wrong ``ORDER BY batch_id`` would pick the earlier/complete one). The reader
    must return the LATER/INCOMPLETE batch — latest-is-truth, NO fallback to the
    older complete batch (Decision C). Seeds in a transaction and ROLLS BACK."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))
            url = os.environ.get("DATABASE_URL")
        except Exception:
            url = None
    if not url:
        pytest.skip("no DATABASE_URL — the SQL/ordering proof needs the DB")

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    eng = create_engine(url)
    tid = uuid4()
    # the LATER-finished batch gets the SMALLER uuid (so batch_id ordering ≠ time).
    b_small, b_large = sorted([uuid4(), uuid4()], key=str)
    late_batch, early_batch = b_small, b_large          # late finished, small uuid
    assert str(late_batch) < str(early_batch)           # the trap is real

    now = datetime.now(timezone.utc)
    late_at, early_at = now, now - timedelta(hours=2)
    r1, r2, r3, r4 = uuid4(), uuid4(), uuid4(), uuid4()

    ins_run = text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, evidence, "
        "batch_id, source) VALUES (:rid,:rec,1,:tid,7,:oc,:fa,:fa,'{}'::jsonb,:bid,'runall_probe')")
    ins_man = text(
        "INSERT INTO s4_runall_batch_manifests (batch_id, claim_test_id, "
        "expected_recipe_ids, created_at) VALUES (:bid,:tid,:exp,:ca)")

    with eng.connect() as conn:
        trans = conn.begin()                            # begin BEFORE any execute
        conn.execute(text("SET search_path TO tenant_1, public"))
        try:
            # EARLY batch — COMPLETE (expects r1,r2; both ran).
            conn.execute(ins_man, {"bid": early_batch, "tid": tid,
                                   "exp": [r1, r2], "ca": early_at})
            for rec in (r1, r2):
                conn.execute(ins_run, {"rid": uuid4(), "rec": rec, "tid": tid,
                                       "oc": "passed", "fa": early_at, "bid": early_batch})
            # LATE batch — INCOMPLETE (expects r3,r4; only r3 ran → r4 absent).
            conn.execute(ins_man, {"bid": late_batch, "tid": tid,
                                   "exp": [r3, r4], "ca": late_at})
            conn.execute(ins_run, {"rid": uuid4(), "rec": r3, "tid": tid,
                                   "oc": "passed", "fa": late_at, "bid": late_batch})

            s = Session(bind=conn)
            # most-recent picks the LATER batch (by finished_at), the small uuid.
            assert select_most_recent_batch(s, tid) == late_batch
            res = read_batch_completeness(s, tid)
            # latest-is-truth: the later batch is INCOMPLETE → not-Verified; NO
            # fallback to the earlier complete batch.
            assert res.batch_id == late_batch
            assert res.complete is False and res.probes is None
        finally:
            trans.rollback()                            # never persists


@pytest.mark.integration
def test_complete_batch_end_to_end_live():
    """A single COMPLETE batch seeded live → complete=True, probes built from the
    real s4 rows. Seeds in a txn and ROLLS BACK."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))
            url = os.environ.get("DATABASE_URL")
        except Exception:
            url = None
    if not url:
        pytest.skip("no DATABASE_URL")

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    eng = create_engine(url)
    tid, bid, r1, r2 = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    ins_run = text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, evidence, "
        "batch_id, source) VALUES (:rid,:rec,1,:tid,7,:oc,:fa,:fa,'{}'::jsonb,:bid,'runall_probe')")
    ins_man = text(
        "INSERT INTO s4_runall_batch_manifests (batch_id, claim_test_id, "
        "expected_recipe_ids, created_at) VALUES (:bid,:tid,:exp,:ca)")
    with eng.connect() as conn:
        trans = conn.begin()                            # begin BEFORE any execute
        conn.execute(text("SET search_path TO tenant_1, public"))
        try:
            conn.execute(ins_man, {"bid": bid, "tid": tid, "exp": [r1, r2], "ca": now})
            conn.execute(ins_run, {"rid": uuid4(), "rec": r1, "tid": tid,
                                   "oc": "passed", "fa": now, "bid": bid})
            conn.execute(ins_run, {"rid": uuid4(), "rec": r2, "tid": tid,
                                   "oc": "failed", "fa": now, "bid": bid})
            s = Session(bind=conn)
            res = read_batch_completeness(s, tid)
            assert res.complete is True and res.batch_id == bid
            assert len(res.probes) == 2
            # one passed + one failed → the bva arm grades it not-Verified.
            assert apply_strategy(None, "bva", list(res.probes)) is False
        finally:
            trans.rollback()

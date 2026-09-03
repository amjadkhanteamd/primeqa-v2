"""Scheduling-slice DB-real tests — gated on S3A3_TEST_DATABASE_URL
(scratch with tenant 20260904_0010, the APPROVED B-1 claim set present).
Covers the briefed matrix: cadence fires; overlap SKIP proven; dead
authority refused loudly; enqueue failure recorded, never silent; and a
real end-to-end scheduled run (fire -> claim -> consume with the scan
faked -> process -> verdicts) on fixture data."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL"),
]
if DB:
    os.environ.setdefault("DATABASE_URL", DB)

ADMIN_SUBJ = {"user_id": 1, "tenant_id": 1, "role": "admin"}
AN_HOUR_AGO = datetime.now(timezone.utc) - timedelta(hours=1)

# The copied B-1 set's membership references PROD claim ids that do not
# exist in scratch's test_claims — schedules here ride a SCRATCH-NATIVE
# set, enumerated and approved through the real acts (claims dedupe by
# identity, so repeated module runs are no-ops on the claim store).
_SETS: dict = {}


@pytest.fixture(scope="module")
def approved_set():
    if "approved" in _SETS:
        return _SETS["approved"]
    from primeqa.generation.enumeration import enumerate_claims
    from primeqa.test_representation.claim_sets import approve_claim_set
    eng = create_engine(DB, connect_args={
        "options": "-csearch_path=tenant_1,public -capp.tenant_id=1"})
    with Session(bind=eng) as s:
        out = enumerate_claims(s, catalogue_release_id=3,
                               inventory_version=1,
                               persona_scope="customer", created_by=1)
        approve_claim_set(s, claim_set_id=out["claim_set_id"],
                          user_id=1, tenant_id=1)
        s.commit()
        _SETS["approved"] = str(out["claim_set_id"])
    return _SETS["approved"]


@pytest.fixture(autouse=True)
def _bound_to_scratch():
    from primeqa.semantic import connection as C
    if DB.rsplit("/", 1)[-1] not in str(C.get_engine().url):
        pytest.skip("S1 engine already bound to a different database")


@pytest.fixture()
def session():
    eng = create_engine(DB, pool_pre_ping=True, connect_args={
        "options": "-csearch_path=tenant_1,public -capp.tenant_id=1"})
    s = Session(bind=eng)
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def cleanup(session):
    """Every schedule + job this module creates is deleted afterwards —
    the scratch queue must stay claimable for other suites."""
    created = {"schedules": [], "jobs": []}
    yield created
    session.rollback()               # a failed test leaves no aborted txn
    for j in created["jobs"]:
        for tbl, col in (("s6_ui_verdicts", "job_id"),
                         ("s6_ui_processing_runs", "job_id"),
                         ("s4_ui_inspection_results", "job_id"),
                         ("s4_ui_inspection_jobs", "id")):
            session.execute(text(
                f"DELETE FROM {tbl} WHERE {col} = :j"), {"j": j})
    for sid in created["schedules"]:
        session.execute(text(
            "DELETE FROM ui_run_schedules WHERE id = :i"), {"i": sid})
    session.commit()


def _mk(session, cleanup, claim_set_id, **over):
    """A schedule due immediately: every-minute cron, created an hour
    ago (is_due is lateness-tolerant)."""
    from primeqa.execution_engine import ui_schedules as U
    kw = dict(subject=ADMIN_SUBJ, claim_set_id=claim_set_id,
              cron_expr="* * * * *", note="test")
    kw.update(over)
    out = U.create_ui_run_schedule(1, **kw)
    sid = out["schedule_id"]
    cleanup["schedules"].append(sid)
    session.execute(text(
        "UPDATE ui_run_schedules SET created_at = :t WHERE id = :i"),
        {"t": AN_HOUR_AGO, "i": sid})
    session.commit()
    return sid


def test_a0_unprovisioned_tenant_skips_loudly_once(caplog):
    """The deploy-watch incident as a regression: a tenant without the
    table (or without a schema at all) skips loudly-once — no raise, no
    per-tick traceback flood."""
    import logging

    from primeqa.execution_engine import ui_schedules as U
    U._WARNED_UNPROVISIONED.discard(99)
    with caplog.at_level(logging.WARNING, logger="primeqa.ui_schedules"):
        out1 = U.fire_due_ui_schedules(99)
        out2 = U.fire_due_ui_schedules(99)
    assert out1.get("unprovisioned") and out2.get("unprovisioned")
    assert out1["checked"] == 0 and not out1["failed"]
    warns = [r for r in caplog.records if "no" in r.message
             and "ui_run_schedules" in r.message]
    assert len(warns) == 1                       # loudly-ONCE


def test_a_creation_gates(session, cleanup, approved_set):
    from primeqa.core.authz import AuthorizationError
    from primeqa.execution_engine import ui_schedules as U

    # viewer refused at the boundary — creation IS the authority check
    with pytest.raises(AuthorizationError):
        U.create_ui_run_schedule(1, subject={"user_id": 9, "tenant_id": 1,
                                             "role": "viewer"},
                                 claim_set_id=approved_set, cron_expr="* * * * *")
    # bad cron refused
    with pytest.raises(U.UiScheduleError, match="invalid cron"):
        U.create_ui_run_schedule(1, subject=ADMIN_SUBJ,
                                 claim_set_id=approved_set, cron_expr="nope")
    # an unapproved set refused — scheduling automates RUNS, never approval
    draft = session.execute(text(
        "SELECT id FROM claim_sets WHERE status <> 'approved' LIMIT 1"
    )).scalar()
    if draft:
        with pytest.raises(U.UiScheduleError, match="never approval"):
            U.create_ui_run_schedule(1, subject=ADMIN_SUBJ,
                                     claim_set_id=draft,
                                     cron_expr="* * * * *")
    # credential-shaped auth refused (descriptor only)
    with pytest.raises(U.UiScheduleError, match="not schedulable"):
        U.create_ui_run_schedule(1, subject=ADMIN_SUBJ, claim_set_id=approved_set,
                                 cron_expr="* * * * *",
                                 auth={"mode": "password"})
    # a valid create audits with the real actor
    sid = _mk(session, cleanup, approved_set,
              auth={"mode": "vault", "persona": "customer"})
    audit = session.execute(text("""
        SELECT user_id FROM public.activity_log
        WHERE action = 'ui.schedule_created'
          AND details->>'schedule_id' = :s"""),
        {"s": str(sid)}).scalar()
    assert audit == 1


def test_b_cadence_fires_and_the_manifest_is_fresh(session, cleanup, approved_set):
    from primeqa.execution_engine import ui_schedules as U
    sid = _mk(session, cleanup, approved_set)
    out = U.fire_due_ui_schedules(1)
    fired = [f for f in out["fired"] if f["schedule_id"] == sid]
    assert len(fired) == 1
    job_id = fired[0]["job_id"]
    cleanup["jobs"].append(job_id)
    # the fresh manifest carries the CURRENT pins (census + run set)
    pins = session.execute(text("""
        SELECT m.payload->'pins' FROM s4_ui_run_manifests m
        JOIN s4_ui_inspection_jobs j ON j.manifest_id = m.id
        WHERE j.id = :j"""), {"j": job_id}).scalar_one()
    assert len(pins["engine_run_set"]) == 74
    assert pins["census"]["schema_version"] == 1
    mode = session.execute(text("""
        SELECT m.payload->'execution'->>'mode' FROM s4_ui_run_manifests m
        JOIN s4_ui_inspection_jobs j ON j.manifest_id = m.id
        WHERE j.id = :j"""), {"j": job_id}).scalar_one()
    assert mode == "scheduled"
    # the enqueue audit carries the system-as-actor attribution
    trig = session.execute(text("""
        SELECT details->'trigger' FROM public.activity_log
        WHERE action = 'ui.run_enqueued' AND details->>'job_id' = :j"""),
        {"j": job_id}).scalar_one()
    assert trig == {"scheduled_by_schedule": sid, "authorised_by_user": 1}
    # schedule row advanced
    row = session.execute(text("""
        SELECT last_job_id::text, error_state FROM ui_run_schedules
        WHERE id = :i"""), {"i": sid}).fetchone()
    assert row[0] == job_id and row[1] is None
    # not due again inside the same minute -> nothing new
    again = U.fire_due_ui_schedules(1)
    assert not [f for f in again["fired"] if f["schedule_id"] == sid]


def test_c_overlap_skips_and_audits_never_stacks(session, cleanup, approved_set):
    from primeqa.execution_engine import ui_schedules as U
    sid = _mk(session, cleanup, approved_set)
    out = U.fire_due_ui_schedules(1)
    job_id = [f for f in out["fired"] if f["schedule_id"] == sid][0]["job_id"]
    cleanup["jobs"].append(job_id)
    # make it due again while the job is still PENDING (no worker here)
    session.execute(text("""
        UPDATE ui_run_schedules SET last_enqueued_at = :t WHERE id = :i"""),
        {"t": AN_HOUR_AGO, "i": sid})
    session.commit()
    out2 = U.fire_due_ui_schedules(1)
    assert sid in out2["skipped_overlap"]
    assert not [f for f in out2["fired"] if f["schedule_id"] == sid]
    n_jobs = session.execute(text("""
        SELECT COUNT(*) FROM s4_ui_inspection_jobs j
        JOIN s4_ui_run_manifests m ON m.id = j.manifest_id
        WHERE m.payload->'execution'->>'mode' = 'scheduled'
          AND j.id = :j"""), {"j": job_id}).scalar_one()
    assert n_jobs == 1                                  # never stacked
    row = session.execute(text("""
        SELECT skips_since_last_run, last_skipped_at FROM ui_run_schedules
        WHERE id = :i"""), {"i": sid}).fetchone()
    assert row[0] == 1 and row[1] is not None
    audit = session.execute(text("""
        SELECT COUNT(*) FROM public.activity_log
        WHERE action = 'ui.schedule_overlap_skipped'
          AND details->>'schedule_id' = :s"""), {"s": str(sid)}).scalar_one()
    assert audit >= 1


def test_d_dead_authority_deactivates_loudly(session, cleanup, approved_set):
    from primeqa.execution_engine import ui_schedules as U
    probe_uid = 990903
    session.execute(text("""
        INSERT INTO public.users (id, tenant_id, email, password_hash,
                                  full_name, role, is_active)
        VALUES (:u, 1, 'probe-dead-authority@test.local', 'x',
                'Probe', 'tester', TRUE)
        ON CONFLICT (id) DO UPDATE SET is_active = TRUE, role = 'tester'
    """), {"u": probe_uid})
    session.commit()
    try:
        sid = _mk(session, cleanup, approved_set,
                  subject={"user_id": probe_uid, "tenant_id": 1,
                           "role": "tester"})
        session.execute(text(
            "UPDATE public.users SET is_active = FALSE WHERE id = :u"),
            {"u": probe_uid})
        session.commit()
        out = U.fire_due_ui_schedules(1)
        assert sid in out["deactivated_dead_authority"]
        row = session.execute(text("""
            SELECT active, error_state, deactivated_reason
            FROM ui_run_schedules WHERE id = :i"""), {"i": sid}).fetchone()
        assert row[0] is False and row[1] == "dead_authority"
        assert "inactive" in row[2]
        audit = session.execute(text("""
            SELECT details->>'disposition' FROM public.activity_log
            WHERE action = 'ui.schedule_dead_authority'
              AND details->>'schedule_id' = :s
            ORDER BY created_at DESC LIMIT 1"""), {"s": str(sid)}).scalar()
        assert "never runs on dead authority" in audit
        # deactivated: the next tick does not even consider it
        again = U.fire_due_ui_schedules(1)
        assert sid not in again["deactivated_dead_authority"]
    finally:
        session.rollback()
        # scratch-only probe cleanup: its audit rows reference the user
        session.execute(text(
            "DELETE FROM public.activity_log WHERE user_id = :u"),
            {"u": probe_uid})
        session.execute(text(
            "DELETE FROM public.users WHERE id = :u"), {"u": probe_uid})
        session.commit()


def test_e_enqueue_failure_is_recorded_never_silent(session, cleanup,
                                                    approved_set, monkeypatch):
    from primeqa.execution_engine import ui_manifest as M
    from primeqa.execution_engine import ui_schedules as U
    sid = _mk(session, cleanup, approved_set)

    def _boom(*a, **k):
        raise RuntimeError("planted enqueue failure")
    monkeypatch.setattr(M, "enqueue_ui_run", _boom)
    # ui_schedules imports the symbol at call time from the module
    monkeypatch.setattr(
        "primeqa.execution_engine.ui_manifest.enqueue_ui_run", _boom)
    out = U.fire_due_ui_schedules(1)
    assert sid in out["failed"]
    row = session.execute(text("""
        SELECT error_state, last_error, active FROM ui_run_schedules
        WHERE id = :i"""), {"i": sid}).fetchone()
    assert row[0] == "enqueue_failed"
    assert "planted enqueue failure" in row[1]
    assert row[2] is True            # errored, not silently deactivated
    audit = session.execute(text("""
        SELECT COUNT(*) FROM public.activity_log
        WHERE action = 'ui.schedule_enqueue_failed'
          AND details->>'schedule_id' = :s"""), {"s": str(sid)}).scalar_one()
    assert audit >= 1


def test_f_end_to_end_scheduled_run_on_fixture_surfaces(session, cleanup,
                                                        approved_set,
                                                        monkeypatch, capsys):
    """fire -> the queue -> consume (scan faked, queue/evidence mechanics
    real) -> process -> verdicts attributed to the SCHEDULED job."""
    from primeqa.browser_worker import queue as q
    from primeqa.browser_worker.consume import consume_job
    from primeqa.execution_engine import ui_schedules as U
    from primeqa.interpretation.ui_conformance import process_job

    # park any stray pending jobs so claim_one takes ours
    session.execute(text("""
        DELETE FROM s4_ui_inspection_results WHERE job_id IN (
            SELECT id FROM s4_ui_inspection_jobs
            WHERE status IN ('pending', 'in_progress'))"""))
    session.execute(text("DELETE FROM s4_ui_inspection_jobs "
                         "WHERE status IN ('pending', 'in_progress')"))
    session.commit()

    sid = _mk(session, cleanup, approved_set, auth=None)
    out = U.fire_due_ui_schedules(1)
    job_id = [f for f in out["fired"] if f["schedule_id"] == sid][0]["job_id"]
    cleanup["jobs"].append(job_id)

    run_set_seen = {}

    def _scan(url, **kw):
        run_set_seen["run_set"] = kw.get("run_set")
        run_set_seen["census_cfg"] = kw.get("census")
        return {"status": "OK", "fingerprint": {"sha256": "f" * 64},
                "timings_ms": {"nav": 1.0},
                "engine_observations": {
                    "violations": [], "incomplete": [],
                    "violations_count": 0, "passes_count": len(
                        kw.get("run_set") or []),
                    "incomplete_count": 0,
                    "passes_ids": sorted(kw.get("run_set") or []),
                    "inapplicable_ids": [],
                    "run_set": sorted(kw.get("run_set") or [])},
                "census": {"schema_version": 1,
                           "traversal_mode": "light_only",
                           "node_cap": kw.get("census", {}).get("node_cap"),
                           "cap_hit": False, "capture_errors": 0,
                           "n": 0, "nodes": []}}
    monkeypatch.setattr("primeqa.browser_worker.consume.scan_page", _scan)

    job = q.claim_one(session)
    assert job is not None and job["job_id"] == job_id
    consume_job(session, job)
    assert session.execute(text(
        "SELECT status FROM s4_ui_inspection_jobs WHERE id = :j"),
        {"j": job_id}).scalar_one() == "succeeded"
    assert len(run_set_seen["run_set"]) == 74       # the fresh pin reached it
    assert run_set_seen["census_cfg"]["schema_version"] == 1

    result = process_job(session, job_id=uuid.UUID(job_id))
    session.commit()
    # data-driven: the scratch-native set = (74 platform + the recorded
    # tenant union) x 2 surfaces
    n_union = session.execute(text(
        "SELECT COUNT(*) FROM cust_release_members "
        "WHERE platform_release_id = 3")).scalar_one()
    assert result["verdicts_written"] == (74 + n_union) * 2
    assert result["verdict_counts"].get("PASS", 0) > 0
    # ...and the SCHEDULED custom claims were decided from the census:
    # an empty census matches nothing -> no_match_set, never PASS
    cust = session.execute(text("""
        SELECT verdict, verdict_basis->>'reason'
        FROM s6_ui_verdicts
        WHERE job_id = :j AND plimsol_rule_id LIKE 'PLM-CUST-%'"""),
        {"j": job_id}).fetchall()
    assert len(cust) == n_union * 2
    assert all(v == "NOT_DETERMINED" and r == "no_match_set"
               for v, r in cust)

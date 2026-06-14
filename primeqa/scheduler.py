"""Reaper/scheduler entrypoint.

Runs on a timer. Handles:
- Dead job reaper (stuck stages with no worker heartbeat)
- Stuck slot reaper (slots held beyond max_execution_time_sec)
- Stale worker cleanup (workers with no heartbeat for 2+ minutes)
"""

import logging
import os
import time

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

log = logging.getLogger(__name__)

REAPER_INTERVAL = 60


def create_scheduler_context():
    from primeqa import db as dbmod
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not set")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    dbmod.init_db(database_url)
    db = dbmod.SessionLocal()
    # D-221 R3 / D-240: v1 pipeline repos + the ExecutionSlot repo retired with
    # their tables; the scheduler context is db + the heartbeat liveness repo only.
    from primeqa.execution.repository import WorkerHeartbeatRepository
    return {
        "db": db,
        "heartbeat_repo": WorkerHeartbeatRepository(db),
    }


def reap_stale_workers(ctx):
    """Mark workers with no heartbeat for 2+ minutes as dead.

    `died_reason='heartbeat_timeout'` is the generic fallback — a
    worker that shut down gracefully will have already written a more
    specific reason ('SIGTERM' or the exception string) before the
    heartbeat expired, and the repo preserves the first-set reason.
    """
    heartbeat_repo = ctx["heartbeat_repo"]
    dead = heartbeat_repo.find_dead_workers(timeout_seconds=120)
    for wh in dead:
        heartbeat_repo.mark_dead(wh.worker_id, died_reason="heartbeat_timeout")
        log.warning("Marked worker %s as dead (heartbeat_timeout); last_run=%s last_stage=%s",
                    wh.worker_id, wh.current_run_id, wh.current_stage)


def scheduler_tick(ctx):
    """Single reaper iteration. Each tick is isolated (D-178): one tick raising never
    skips the ticks after it — the s1 sync enqueuer/reaper run LAST (after 12 others),
    so an unguarded earlier tick used to starve them. Defense in depth on top of
    ``run_scheduler``'s per-iteration guard. The tick list is built here (not at module
    scope) so every tick function name is already defined when it's referenced."""
    # D-221 R3: the v1 pipeline reapers, scheduled_runs firer, v1
    # generation-job reaper, and run_events trimmer retired with the engine.
    ticks = (
        reap_stale_workers,
        s3_reaper_tick,               # D-106.4 slice 5 (substrate-3 queue)
        s4_reaper_tick,               # D-132 (substrate-4 execution queue)
        s4_cleanup_reaper_tick,       # D-230 (substrate-4 stranded-record cleanup)
        s4_schedule_tick,             # D-214 (scheduled substrate regression runs)
        repair_triage_tick,           # D-215.1 (repair-agent spine — proposal-only)
        s8_grounding_tick,            # D-143 (substrate-8 grounding recompute)
        s1_sync_enqueuer_tick,        # D-153 (substrate-1 sync cadence)
        s1_sync_reaper_tick,          # D-153 (substrate-1 sync queue)
    )
    for tick in ticks:
        try:
            tick(ctx)
        except Exception:
            log.exception("scheduler tick %s failed; continuing",
                          getattr(tick, "__name__", tick))


def s3_reaper_tick(ctx):
    """Reap stale substrate-3 generation jobs (D-106.4 slice 5).

    s3_generation_jobs is per-tenant, so enumerate active tenants from
    shared.tenants and reap each — per-tenant try/except (in run_s3_reaper_tick)
    so one tenant's failure never starves the others. The timeout is generous:
    the consumer heartbeats only at claim/start (run_generation is a single
    blocking call, no mid-run hook), so a stuck claimed/running job is failed
    only well past the longest legitimate run.
    """
    try:
        from primeqa.generation.consumer import run_s3_reaper_tick
        from primeqa.semantic.connection import admin_run_in_shared_schema
        with admin_run_in_shared_schema() as conn:
            tenant_ids = [r[0] for r in conn.execute(text(
                "SELECT id FROM shared.tenants WHERE deleted_at IS NULL ORDER BY id"
            )).fetchall()]
        total = sum(run_s3_reaper_tick(tenant_ids).values())
        if total:
            log.warning("reaped %d stale s3 generation job(s)", total)
    except Exception as e:
        log.warning("s3_reaper_tick failed: %s", e)


def s4_schedule_tick(ctx):
    """Fire due substrate run schedules (D-214 — the scheduled regression
    trigger). Per-tenant isolation (one tenant's failure never starves the
    rest); production environments are excluded inside ``fire_due_schedules``
    (sandbox-only, D-214 §4). The per-tenant prod-env set comes from the v1
    ``environments`` table on the scheduler's own session."""
    try:
        from primeqa.core.models import Environment, Tenant
        from primeqa.execution_engine.schedules import fire_due_schedules
        db = ctx["db"]
        for tenant in db.query(Tenant).all():
            try:
                prod_ids = {e.id for e in db.query(Environment).filter(
                    Environment.tenant_id == tenant.id,
                    Environment.is_production.is_(True)).all()}
                out = fire_due_schedules(tenant.id, production_env_ids=prod_ids)
                if out["fired"]:
                    log.info("s4 schedules fired for tenant %s: %s (%d jobs)",
                             tenant.id, out["fired"], out["enqueued"])
            except Exception:
                log.exception("s4_schedule_tick failed for tenant %s; continuing",
                              tenant.id)
    except Exception as e:
        log.warning("s4_schedule_tick failed: %s", e)


def repair_triage_tick(ctx):
    """Triage new failed/errored S6 interpretations into repair PROPOSALS, then
    run the FLAG-GATED auto-apply pass. Deterministic rerun/regenerate (D-215.1)
    + the LLM-proposed recipe_edit (D-236, when a per-env Anthropic key resolves).
    PROPOSAL-ONLY by default — a human decides on the Repairs panel; auto-apply is
    dormant unless a tenant enabled ``repair_auto_apply`` (sandbox + high
    confidence only; prod always human). Per-tenant isolation."""
    def _api_key(tenant_id, environment_id):
        # Best-effort per-env Anthropic key; "" on anything missing (the LLM
        # proposal is then skipped — never an error in the tick).
        try:
            from primeqa.core.repository import (
                ConnectionRepository, EnvironmentRepository,
            )
            env = EnvironmentRepository(ctx["db"]).get_environment(
                environment_id, tenant_id)
            if env is None or not getattr(env, "llm_connection_id", None):
                return ""
            conn = ConnectionRepository(ctx["db"]).get_connection_decrypted(
                env.llm_connection_id, tenant_id)
            return (conn or {}).get("config", {}).get("api_key", "")
        except Exception:
            return ""

    try:
        from primeqa.core.models import Tenant
        from primeqa.intelligence.repair_agent import (
            auto_apply_proposals, triage_new_failures,
        )
        for tenant in ctx["db"].query(Tenant).all():
            out = triage_new_failures(tenant.id, api_key_resolver=_api_key)
            if out["proposed"]:
                log.info("repair triage proposed %d repair(s) for tenant %s",
                         out["proposed"], tenant.id)
            aa = auto_apply_proposals(tenant.id)
            if aa["applied"]:
                log.info("repair auto-applied %d recipe edit(s) for tenant %s",
                         aa["applied"], tenant.id)
    except Exception as e:
        log.warning("repair_triage_tick failed: %s", e)


def s8_grounding_tick(ctx):
    """Recompute S8 grounding-validity for tenants whose S1 has advanced (D-143).

    s8_grounding_validity is per-tenant, so enumerate active tenants from
    shared.tenants and recompute each — per-tenant try/except (in
    run_s8_grounding_tick) so one tenant's failure never starves the others.
    Freshness is derived from the store (a claim is fresh iff its verdict was
    computed against the current S1 seq), so a tick whose tenants are all fresh
    is a cheap no-op.
    """
    try:
        from primeqa.evolution.recompute import run_s8_grounding_tick
        from primeqa.semantic.connection import admin_run_in_shared_schema
        with admin_run_in_shared_schema() as conn:
            tenant_ids = [r[0] for r in conn.execute(text(
                "SELECT id FROM shared.tenants WHERE deleted_at IS NULL ORDER BY id"
            )).fetchall()]
        total = sum(run_s8_grounding_tick(tenant_ids).values())
        if total:
            log.info("s8 grounding: recomputed %d claim grounding(s)", total)
    except Exception as e:
        log.warning("s8_grounding_tick failed: %s", e)


def s4_reaper_tick(ctx):
    """Reap stale substrate-4 execution jobs (D-132).

    s4_execution_jobs is per-tenant, so enumerate active tenants from
    shared.tenants and reap each — per-tenant try/except (in run_s4_reaper_tick)
    so one tenant's failure never starves the others. Structural mirror of
    s3_reaper_tick; the timeout is generous (the consumer heartbeats only at
    claim/start, the async run being one blocking sequence).
    """
    try:
        from primeqa.execution_engine.consumer import run_s4_reaper_tick
        from primeqa.semantic.connection import admin_run_in_shared_schema
        with admin_run_in_shared_schema() as conn:
            tenant_ids = [r[0] for r in conn.execute(text(
                "SELECT id FROM shared.tenants WHERE deleted_at IS NULL ORDER BY id"
            )).fetchall()]
        total = sum(run_s4_reaper_tick(tenant_ids).values())
        if total:
            log.warning("reaped %d stale s4 execution job(s)", total)
    except Exception as e:
        log.warning("s4_reaper_tick failed: %s", e)


def s4_cleanup_reaper_tick(ctx):
    """Reap Salesforce records a crashed substrate-4 run left stranded (D-230).

    s4_created_records is per-tenant, so enumerate active tenants and reap each —
    per-tenant try/except (in run_s4_cleanup_reaper_tick) so one tenant's failure
    never starves the others. Distinct from s4_reaper_tick (stuck JOBS) — this
    reclaims orphaned ORG records the write-ahead sink recorded but in-run
    teardown never deleted (a worker kill, a transport hang mid-run)."""
    try:
        from primeqa.execution_engine.consumer import run_s4_cleanup_reaper_tick
        from primeqa.semantic.connection import admin_run_in_shared_schema
        with admin_run_in_shared_schema() as conn:
            tenant_ids = [r[0] for r in conn.execute(text(
                "SELECT id FROM shared.tenants WHERE deleted_at IS NULL ORDER BY id"
            )).fetchall()]
        total = sum(run_s4_cleanup_reaper_tick(tenant_ids).values())
        if total:
            log.warning("reaped %d stranded s4 record(s)", total)
    except Exception as e:
        log.warning("s4_cleanup_reaper_tick failed: %s", e)


def s1_sync_enqueuer_tick(ctx):
    """Enqueue substrate-1 metadata-sync jobs on a cadence (D-153).

    s1_sync_jobs is per-tenant, so enumerate active tenants from shared.tenants and
    enqueue each — per-tenant try/except (in run_s1_sync_enqueuer_tick) so one
    tenant's failure never starves the others. The enqueuer scans connected_orgs
    needing a (re)sync (an incomplete sync_run → resume, OR never/stale beyond the
    cadence window); create_or_get_job dedups, so a steady state enqueues nothing.
    """
    try:
        from primeqa.sync.consumer import run_s1_sync_enqueuer_tick
        from primeqa.semantic.connection import admin_run_in_shared_schema
        with admin_run_in_shared_schema() as conn:
            tenant_ids = [r[0] for r in conn.execute(text(
                "SELECT id FROM shared.tenants WHERE deleted_at IS NULL ORDER BY id"
            )).fetchall()]
        total = sum(run_s1_sync_enqueuer_tick(tenant_ids).values())
        if total:
            log.info("s1 sync: enqueued %d org sync(s)", total)
    except Exception as e:
        log.warning("s1_sync_enqueuer_tick failed: %s", e)


def s1_sync_reaper_tick(ctx):
    """Reap stale substrate-1 sync jobs (D-153).

    s1_sync_jobs is per-tenant, so enumerate active tenants from shared.tenants and
    reap each — per-tenant try/except (in run_s1_sync_reaper_tick) so one tenant's
    failure never starves the others. stale_minutes=45 (D-151) exceeds the longest
    legitimate sync; a reaped job leaves its sync_run resumable, so a re-enqueue
    continues from last_completed_phase (D-152 carry-forward).
    """
    try:
        from primeqa.sync.consumer import run_s1_sync_reaper_tick
        from primeqa.semantic.connection import admin_run_in_shared_schema
        with admin_run_in_shared_schema() as conn:
            tenant_ids = [r[0] for r in conn.execute(text(
                "SELECT id FROM shared.tenants WHERE deleted_at IS NULL ORDER BY id"
            )).fetchall()]
        total = sum(run_s1_sync_reaper_tick(tenant_ids).values())
        if total:
            log.warning("reaped %d stale s1 sync job(s)", total)
    except Exception as e:
        log.warning("s1_sync_reaper_tick failed: %s", e)


_last_trim = {"at": 0}

def run_scheduler():
    """Main scheduler loop."""
    print("Scheduler starting...")
    ctx = create_scheduler_context()

    try:
        while True:
            # D-178: a single tick failure (a transient DB blip, an unguarded tick
            # raising) must NEVER kill the scheduler process — an unguarded throw here
            # is what caused the 15h S1-sync outage (scheduler died → s1 reaper never
            # ran → the orphaned sync job was never resumed). Log it, clear any poisoned
            # transaction on the shared session, and continue the loop.
            try:
                scheduler_tick(ctx)
            except Exception:
                log.exception("scheduler_tick failed; continuing")
                try:
                    ctx["db"].rollback()
                except Exception:
                    log.exception("scheduler ctx db rollback failed")
            time.sleep(REAPER_INTERVAL)
    except KeyboardInterrupt:
        print("Scheduler shutting down")
    finally:
        ctx["db"].close()


if __name__ == "__main__":
    run_scheduler()

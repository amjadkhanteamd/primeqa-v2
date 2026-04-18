"""Worker-side helpers for the metadata background-job queue (migration 025).

The Railway `worker` process calls `poll_and_run_once(db, worker_id)` on
each tick. It:

  1. Atomically claims one queued meta_version (SELECT ... FOR UPDATE SKIP
     LOCKED) by flipping status to 'in_progress' and setting worker_id +
     heartbeat_at.
  2. Invokes MetadataService.run_queued_sync, threading in:
       - an OAuth-token fetcher (so the service doesn't have to know Flask)
       - a heartbeat callback that bumps heartbeat_at every 10s
  3. On success: sync already self-finalises (status='complete').
  4. On failure: sync raises; we flip status='failed' with error_message.
  5. On cancel: sync returns {cancelled: True}; we flip status='cancelled'.

Designed to be called in the same process that owns the session. Safe
against multiple workers \u2014 FOR UPDATE SKIP LOCKED guarantees at most one
worker claims a given row.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SEC = 10


def poll_and_run_once(db, worker_id: str) -> bool:
    """Try to claim + run one queued metadata sync. Returns True if it
    processed a job, False if the queue was empty."""
    from primeqa.metadata.models import MetaVersion

    mv_id = _claim_next(db, worker_id)
    if mv_id is None:
        return False

    _run_claimed(db, mv_id, worker_id)
    return True


def _claim_next(db, worker_id: str):
    """Atomically claim the oldest queued meta_version. Returns its id or None."""
    from sqlalchemy import text

    # Use SELECT ... FOR UPDATE SKIP LOCKED so multiple worker processes
    # can safely race.
    now = datetime.now(timezone.utc)
    row = db.execute(text("""
        UPDATE meta_versions
        SET    status        = 'in_progress',
               worker_id     = :worker_id,
               started_at    = COALESCE(started_at, :now),
               heartbeat_at  = :now
        WHERE  id = (
            SELECT id FROM meta_versions
            WHERE status = 'queued'
            ORDER BY queued_at NULLS LAST
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id
    """), {"worker_id": worker_id, "now": now}).fetchone()
    db.commit()
    return row[0] if row else None


def _run_claimed(db, mv_id: int, worker_id: str):
    """Run the sync for a meta_version already claimed as in_progress."""
    from primeqa.metadata.models import MetaVersion
    from primeqa.metadata.repository import MetadataRepository
    from primeqa.metadata.service import MetadataService
    from primeqa.core.repository import EnvironmentRepository

    meta_repo = MetadataRepository(db)
    env_repo = EnvironmentRepository(db)
    svc = MetadataService(meta_repo, env_repo)

    # Background heartbeat thread \u2014 keeps meta_versions.heartbeat_at fresh
    # so the scheduler reaper doesn't declare us dead. Stopped on exit.
    stop_event = threading.Event()

    def _heartbeat_loop():
        while not stop_event.wait(HEARTBEAT_INTERVAL_SEC):
            try:
                # Use a short-lived session so we don't interfere with the
                # main sync transaction
                from primeqa import db as dbmod
                hb_db = dbmod.SessionLocal()
                try:
                    hb_db.query(MetaVersion).filter(MetaVersion.id == mv_id).update({
                        "heartbeat_at": datetime.now(timezone.utc),
                    })
                    hb_db.commit()
                finally:
                    hb_db.close()
            except Exception as e:
                log.warning("heartbeat update failed for mv=%s: %s", mv_id, e)

    hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    hb_thread.start()

    def _token_fetcher(env, cfg):
        return _oauth_token(env, cfg)

    def _heartbeat_cb():
        # Inline heartbeat bumps (cheap), complementing the background thread
        try:
            db.query(MetaVersion).filter(MetaVersion.id == mv_id).update({
                "heartbeat_at": datetime.now(timezone.utc),
            })
            db.commit()
        except Exception:
            pass

    try:
        result = svc.run_queued_sync(
            meta_version_id=mv_id,
            worker_id=worker_id,
            oauth_token_fetcher=_token_fetcher,
            heartbeat_cb=_heartbeat_cb,
        )
        # Status is set by run_queued_sync itself. If it returned
        # {'cancelled': True}, flip to cancelled (service emitted event but
        # didn't write meta_versions.status).
        if isinstance(result, dict) and result.get("cancelled"):
            db.query(MetaVersion).filter(MetaVersion.id == mv_id).update({
                "status": "cancelled",
                "completed_at": datetime.now(timezone.utc),
            })
            db.commit()
            log.info("meta_version %s cancelled", mv_id)
        else:
            log.info("meta_version %s completed", mv_id)
    except Exception as e:
        log.exception("meta_version %s failed: %s", mv_id, e)
        # Service has already called fail_meta_version() to set status='failed';
        # but just to be safe in case that failed too:
        try:
            db.query(MetaVersion).filter(
                MetaVersion.id == mv_id,
                MetaVersion.status == "in_progress",
            ).update({
                "status": "failed",
                "completed_at": datetime.now(timezone.utc),
            })
            db.commit()
        except Exception:
            db.rollback()
    finally:
        stop_event.set()


def _oauth_token(env, cfg) -> str:
    """Run the connection's OAuth flow and return a fresh access_token."""
    import requests as http_requests

    login_url = (cfg.get("instance_url") or "").rstrip("/")
    if not login_url:
        org_type = cfg.get("org_type", "sandbox")
        login_url = ("https://test.salesforce.com" if org_type == "sandbox"
                     else "https://login.salesforce.com")

    body = {
        "client_id": cfg.get("client_id", ""),
        "client_secret": cfg.get("client_secret", ""),
    }
    if cfg.get("auth_flow") == "password":
        body["grant_type"] = "password"
        body["username"] = cfg.get("username", "")
        body["password"] = cfg.get("password", "")
    else:
        body["grant_type"] = "client_credentials"

    resp = http_requests.post(f"{login_url}/services/oauth2/token",
                              data=body, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Salesforce OAuth failed ({resp.status_code}): {resp.text[:240]}"
        )
    return resp.json().get("access_token", "")


# ---- Scheduler side: reap stalled jobs ------------------------------------

STALL_THRESHOLD_SEC = 120        # 2 min of no heartbeat \u2192 declare worker dead
NO_HEARTBEAT_GRACE_SEC = 300     # never-heartbeated rows older than 5 min are dead


def reap_stalled_jobs(db) -> int:
    """Flip in_progress rows with no recent heartbeat to failed.

    Two classes of stalled rows:
      (1) heartbeat_at < now - STALL_THRESHOLD_SEC \u2014 worker went silent
      (2) heartbeat_at IS NULL AND started_at < now - NO_HEARTBEAT_GRACE_SEC
          \u2014 row was claimed before migration 025 or by a worker that
          crashed before it could write its first heartbeat. Without this
          clause, old in_progress rows hang forever because `heartbeat_at <
          cutoff` is False for NULL.

    Returns count of rows reaped. Called from primeqa.scheduler tick.
    """
    from primeqa.metadata.models import MetaVersion, MetaSyncStatus
    from primeqa.metadata.sync_engine import emit_sync_event
    from datetime import timedelta
    from sqlalchemy import or_, and_

    now = datetime.now(timezone.utc)
    heartbeat_cutoff = now - timedelta(seconds=STALL_THRESHOLD_SEC)
    grace_cutoff = now - timedelta(seconds=NO_HEARTBEAT_GRACE_SEC)

    stalled = db.query(MetaVersion).filter(
        MetaVersion.status == "in_progress",
        or_(
            and_(MetaVersion.heartbeat_at.isnot(None),
                 MetaVersion.heartbeat_at < heartbeat_cutoff),
            and_(MetaVersion.heartbeat_at.is_(None),
                 # either started_at or queued_at is our "been around a while" signal
                 or_(MetaVersion.started_at < grace_cutoff,
                     and_(MetaVersion.started_at.is_(None),
                          MetaVersion.queued_at < grace_cutoff))),
        ),
    ).all()

    for mv in stalled:
        mv.status = "failed"
        mv.completed_at = now
        why = ("Worker stalled; no heartbeat for >2 min"
               if mv.heartbeat_at
               else "Worker never heartbeated; row reaped after grace period")
        for row in db.query(MetaSyncStatus).filter_by(meta_version_id=mv.id).all():
            if row.status in ("running", "pending"):
                row.status = "failed"
                row.error_message = why
                row.completed_at = now
        emit_sync_event(mv.id, "sync_finished", status="failed",
                        error_message=why)

    if stalled:
        db.commit()
    return len(stalled)

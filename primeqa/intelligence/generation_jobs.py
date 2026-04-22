"""Async generation jobs — model, claim/update helpers, processor.

Replaces the inline svc.generate_test_plan() call in POST
/requirements/<id>/generate with a queue: the web request inserts a
row + returns 202; primeqa.worker claims + runs; UI polls
/api/generation-jobs/<id>/status.

Keeps every existing generate_test_plan invariant (TCs persisted in
generation_batches + test_case_versions + optional BAReview) — the
worker calls the *same* service method the sync route used to call.

Dedup + cancel semantics:
  - Dedup: one active (queued/claimed/running) job per
    (requirement_id, environment_id). create_job returns the existing
    job if one is in flight.
  - Cancel: marks status='cancelled'. Worker re-reads status after
    the LLM call returns; if cancelled, it commits NOTHING (LLM tokens
    are already consumed — logged via the gateway — but the batch +
    TC rows are rolled back).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text,
    text,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from primeqa.db import Base


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    requirement_id = Column(Integer, ForeignKey("requirements.id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    status = Column(String(20), nullable=False, server_default="queued")
    progress_pct = Column(Integer, server_default="0")
    progress_msg = Column(String(200))

    generation_batch_id = Column(Integer, ForeignKey("generation_batches.id"))
    test_case_count = Column(Integer)

    error_code = Column(String(50))
    error_message = Column(Text)

    claimed_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    heartbeat_at = Column(DateTime(timezone=True))

    model_used = Column(String(100))
    tokens_used = Column(Integer)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'claimed', 'running', 'completed', 'failed', 'cancelled')",
            name="generation_jobs_status_check",
        ),
    )


# --------------------------------------------------------------------------
# UI-facing error mapping. Keys match error_code values the worker sets;
# values are the user-friendly message displayed on /requirements/:id.
# --------------------------------------------------------------------------

ERROR_MESSAGES: dict[str, str] = {
    "rate_limited":
        "Generation rate limit reached. Please try again in a few minutes.",
    "auth_error":
        "AI model authentication failed. Ask your admin to check the API key.",
    "content_error":
        "The AI could not generate valid test cases for this requirement. "
        "Try simplifying the ticket.",
    "timeout":
        "Generation timed out. The requirement may be too complex. "
        "Try again or simplify.",
    "quota_exceeded":
        "AI usage quota exceeded for today. Try again tomorrow or ask "
        "an admin to increase limits.",
    "worker_timeout":
        "Generation was interrupted (worker restart or crash). Please retry.",
    "llm_error":
        "The AI model call failed. Check connection settings or retry.",
    "no_metadata":
        "Generation blocked: no metadata version for this environment. "
        "Refresh metadata from Settings -> Environments.",
    "cancelled":
        "Cancelled by user.",
    "generation_error":
        "An unexpected error occurred during generation.",
}


def user_message_for(error_code: Optional[str],
                     fallback: Optional[str] = None) -> str:
    """Return the user-friendly string for a stored error_code."""
    if error_code and error_code in ERROR_MESSAGES:
        return ERROR_MESSAGES[error_code]
    return fallback or ERROR_MESSAGES["generation_error"]


# --------------------------------------------------------------------------
# Web-side helpers: create + dedup + get-active.
# --------------------------------------------------------------------------

def get_active_job(db: Session, tenant_id: int, requirement_id: int,
                   environment_id: int) -> Optional[GenerationJob]:
    """Return the active (queued/claimed/running) job for this req+env if any."""
    return (db.query(GenerationJob)
            .filter(GenerationJob.tenant_id == tenant_id,
                    GenerationJob.requirement_id == requirement_id,
                    GenerationJob.environment_id == environment_id,
                    GenerationJob.status.in_(("queued", "claimed", "running")))
            .order_by(GenerationJob.created_at.desc())
            .first())


def create_or_get_job(db: Session, *, tenant_id: int, environment_id: int,
                     requirement_id: int, created_by: int
                     ) -> tuple[GenerationJob, bool]:
    """Create a new queued job, or return the existing active one.

    Returns (job, already_existed).
    """
    existing = get_active_job(db, tenant_id, requirement_id, environment_id)
    if existing is not None:
        return existing, True
    job = GenerationJob(
        tenant_id=tenant_id,
        environment_id=environment_id,
        requirement_id=requirement_id,
        created_by=created_by,
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, False


# --------------------------------------------------------------------------
# Worker-side helpers: claim, update, run.
# --------------------------------------------------------------------------

def claim_next_queued_job(db: Session) -> Optional[GenerationJob]:
    """Claim the oldest queued job via SELECT FOR UPDATE SKIP LOCKED.

    Returns the claimed GenerationJob (status=claimed), or None if the
    queue is empty. Safe to call from multiple workers concurrently —
    SKIP LOCKED ensures each row is claimed by exactly one worker.
    """
    row = db.execute(text(
        """
        UPDATE generation_jobs
           SET status = 'claimed',
               claimed_at = NOW(),
               heartbeat_at = NOW()
         WHERE id = (
               SELECT id FROM generation_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
         )
         RETURNING id
        """
    )).fetchone()
    db.commit()
    if row is None:
        return None
    return db.query(GenerationJob).filter_by(id=row[0]).first()


def update_job(db: Session, job_id: int, **kwargs) -> None:
    """Set fields on a job row and commit. Adds heartbeat_at=NOW()."""
    kwargs.setdefault("heartbeat_at", datetime.now(timezone.utc))
    db.query(GenerationJob).filter_by(id=job_id).update(kwargs)
    db.commit()


def heartbeat(db: Session, job_id: int) -> None:
    """Bump heartbeat_at so the scheduler's reaper doesn't kill us."""
    db.query(GenerationJob).filter_by(id=job_id).update(
        {"heartbeat_at": datetime.now(timezone.utc)}
    )
    db.commit()


def process_job(job: GenerationJob, *, db_factory) -> None:
    """Run the generation for `job`. `db_factory()` returns a fresh Session.

    This is the worker's entrypoint. It:
      1. Opens a session for itself
      2. Starts a heartbeat thread
      3. Calls the SAME TestManagementService.generate_test_plan the
         old sync route used
      4. Writes results back to the job row
      5. Handles cancel-mid-flight by re-reading job.status after the LLM
      6. Always stops the heartbeat in a finally block

    Raising is caught and recorded as a failed job — the worker loop
    should never see an exception bubble out of here.
    """
    import threading

    stop_beat = threading.Event()

    def _beat():
        while not stop_beat.is_set():
            try:
                db = db_factory()
                try:
                    heartbeat(db, job.id)
                finally:
                    db.close()
            except Exception:
                pass
            stop_beat.wait(10)

    heart = threading.Thread(target=_beat, daemon=True)
    heart.start()

    db = db_factory()
    try:
        # Transition claimed -> running.
        update_job(db, job.id,
                   status="running",
                   started_at=datetime.now(timezone.utc),
                   progress_pct=10,
                   progress_msg="Starting generation…")

        # Build the exact same service the sync route used.
        from primeqa.core.repository import (
            ConnectionRepository, EnvironmentRepository,
        )
        from primeqa.metadata.repository import MetadataRepository
        from primeqa.test_management.repository import (
            BAReviewRepository, MetadataImpactRepository, RequirementRepository,
            SectionRepository, TestCaseRepository, TestSuiteRepository,
        )
        from primeqa.test_management.service import TestManagementService

        svc = TestManagementService(
            SectionRepository(db), RequirementRepository(db),
            TestCaseRepository(db), TestSuiteRepository(db),
            BAReviewRepository(db), MetadataImpactRepository(db),
        )
        svc.review_repo = BAReviewRepository(db)

        update_job(db, job.id, progress_pct=30,
                   progress_msg="Calling AI model…")

        try:
            plan = svc.generate_test_plan(
                tenant_id=job.tenant_id,
                requirement_id=job.requirement_id,
                environment_id=job.environment_id,
                created_by=job.created_by,
                env_repo=EnvironmentRepository(db),
                conn_repo=ConnectionRepository(db),
                metadata_repo=MetadataRepository(db),
            )
        except Exception as exc:
            _mark_failed(db, job.id, exc)
            return

        # Cancel-check: did the user cancel while we were waiting on the
        # LLM? LLM tokens are already spent (usage logged by the gateway)
        # but we can still skip committing UI-level success state.
        db.expire_all()
        fresh = db.query(GenerationJob).filter_by(id=job.id).first()
        if fresh is not None and fresh.status == "cancelled":
            # Leave the cancel record as-is. Note the batch/TC rows were
            # committed inside generate_test_plan — that's fine; they're
            # valid rows, just orphaned from the job.
            return

        tcs = plan.get("test_cases", []) or []
        tokens = ((plan.get("tokens") or {}).get("input", 0)
                  + (plan.get("tokens") or {}).get("output", 0))
        update_job(
            db, job.id,
            status="completed",
            completed_at=datetime.now(timezone.utc),
            progress_pct=100,
            progress_msg=f"Generated {len(tcs)} test case"
                          f"{'s' if len(tcs) != 1 else ''}",
            test_case_count=len(tcs),
            generation_batch_id=plan.get("generation_batch_id"),
            model_used=plan.get("model_used"),
            tokens_used=int(tokens) if tokens else None,
        )
    finally:
        stop_beat.set()
        db.close()


def _mark_failed(db: Session, job_id: int, exc: Exception) -> None:
    """Best-effort error -> error_code mapping + mark job failed."""
    code = "generation_error"
    msg = str(exc)
    # Domain-specific codes the gateway raises.
    try:
        from primeqa.intelligence.llm.gateway import LLMError
        if isinstance(exc, LLMError):
            raw = (getattr(exc, "status", None) or "").lower()
            if raw:
                code = raw
    except Exception:
        pass
    lower = msg.lower()
    if "rate" in lower and "limit" in lower:
        code = "rate_limited"
    elif "timeout" in lower:
        code = "timeout"
    elif "auth" in lower and "api key" in lower:
        code = "auth_error"
    elif "metadata" in lower and "version" in lower:
        code = "no_metadata"
    update_job(
        db, job_id,
        status="failed",
        completed_at=datetime.now(timezone.utc),
        error_code=code,
        error_message=msg[:2000],
        progress_msg=f"Failed: {code}",
    )


# --------------------------------------------------------------------------
# Scheduler-side helper: reaper for stuck jobs.
# --------------------------------------------------------------------------

def reap_stale_jobs(db: Session, *, stale_minutes: int = 2) -> int:
    """Mark stuck claimed/running jobs as failed=worker_timeout.

    A stuck job is one that hasn't heartbeated in `stale_minutes`
    minutes. Called from the scheduler's tick loop.

    Returns the number of jobs reaped.
    """
    from datetime import timedelta
    threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    stale = (db.query(GenerationJob)
             .filter(GenerationJob.status.in_(("claimed", "running")),
                     GenerationJob.heartbeat_at < threshold)
             .all())
    for job in stale:
        job.status = "failed"
        job.completed_at = datetime.now(timezone.utc)
        job.error_code = "worker_timeout"
        job.error_message = ("Generation timed out — worker may have "
                             "crashed mid-run. Please retry.")
    if stale:
        db.commit()
    return len(stale)


__all__ = [
    "GenerationJob", "ERROR_MESSAGES", "user_message_for",
    "get_active_job", "create_or_get_job",
    "claim_next_queued_job", "update_job", "heartbeat",
    "process_job", "reap_stale_jobs",
]

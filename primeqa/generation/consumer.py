"""Substrate-3 production-integration consumer (D-106.4, slice 3).

The worker-layer loop that drives the in-process ``run_generation`` core off the
``s3_generation_jobs`` queue. Per-tenant; mirrors the v1 generation tick's
lifecycle (claim -> run -> complete/fail). This slice is **consumer + claim
ONLY** — no enqueue endpoint (slice 4), no reaper (slice 5).

api_key (D-106.4 slice-3 amendment): environment -> connection-scoped, resolved
worker-side. The resolver is **injected** (``api_key_resolver``) so this
substrate consumer stays decoupled from the v1 connection store and is testable
without a real Fernet connection; the substrate core (``run_generation``) stays
api_key-param-pure.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional
from uuid import UUID

from primeqa.generation.intake import build_generation_request
from primeqa.generation.jobs import GenerationJobStore
from primeqa.generation.run import run_generation

log = logging.getLogger(__name__)

# (tenant_id, environment_id) -> Anthropic api_key.
ApiKeyResolver = Callable[[int, Optional[int]], str]


def _classify_error(exc: Exception) -> str:
    """Thin error_code for the failed job (D-106.3 abort-on-error)."""
    from primeqa.intelligence.llm.gateway import LLMError
    from primeqa.intelligence.llm.router import ModelConfigError
    if isinstance(exc, ModelConfigError):
        # The tenant's llm_model_override names a non-selectable (retired?)
        # model — distinguishable from generation_error so the superadmin
        # re-points the tenant instead of debugging the pipeline.
        return "model_config_error"
    if isinstance(exc, LLMError):
        return "llm_error"
    return "generation_error"


def _load_tenant_policy(tenant_id: int):
    """Best-effort TenantPolicy for routing (migration 060 model override +
    always_use_opus). ``load_tenant_config`` is already fail-open — any read
    problem returns the starter defaults (no override) — so a transient DB
    error degrades to today's routing, never a crash. Kept as a seam-shaped
    helper (the ``_bva_boundaries_enabled`` precedent) so tests stub it."""
    from primeqa.intelligence.llm import limits as _limits
    return _limits.load_tenant_config(tenant_id)[1]


def _bva_boundaries_enabled(tenant_id: int) -> bool:
    """Best-effort read of the D-300 per-tenant bva-boundary flag from the
    PUBLIC ``tenant_agent_settings``. The column (migration 059) is
    DELIBERATELY NOT ORM-mapped (deploy-safety — the repair_auto_apply /
    migration-054 precedent): a missing column, missing row, or any read error
    reads as ``False`` (the feature stays dormant), so this code deploys
    safely BEFORE the migration runs."""
    try:
        from sqlalchemy import text
        from primeqa.db import get_db
        db = next(get_db())
        try:
            v = db.execute(text(
                "SELECT llm_enable_bva_boundaries FROM tenant_agent_settings "
                "WHERE tenant_id = :t"), {"t": tenant_id}).scalar()
            return bool(v)
        finally:
            db.close()
    except Exception as exc:
        # D-300 review nit: a transient read failure on an ARMED tenant
        # silently (and stickily, via the same-hash no-op) downgrades the
        # claim to single — leave a trace, never raise.
        log.warning("bva flag read failed for tenant %s (reading False): %s",
                    tenant_id, exc)
        return False


def process_job_for_tenant(
    tenant_id: int, *, api_key_resolver: ApiKeyResolver, tool_turn_fn=None,
) -> Optional[int]:
    """Claim and run one queued job for ``tenant_id``. Returns the processed job
    id, or ``None`` when the tenant's queue is empty.

    Flow: claim (SKIP LOCKED) -> heartbeat -> ``start_attempt`` (mint a fresh
    ``request_id``) -> assemble the fresh request from the job's pinned fields ->
    resolve the api_key (worker-side) -> ``run_generation`` -> ``complete``. Any
    error in the run aborts this job to ``failed`` with a classified
    ``error_code`` (D-106.3) and finalizes the attempt. The **claim is not
    wrapped** — an infrastructure failure (e.g. a missing tenant schema)
    propagates to the per-tenant boundary in :func:`run_s3_generation_tick`."""
    store = GenerationJobStore(tenant_id)
    job = store.claim_next_queued_job()
    if job is None:
        return None
    store.heartbeat(job.id)
    attempt = store.start_attempt(job.id)
    try:
        request = build_generation_request(
            requirement_ref={"key": job.requirement_key,
                             "text": job.requirement_text or ""},
            s1_version_seq=job.s1_version_seq,
            s1_version_name=job.s1_version_name,
            request_id=UUID(attempt.request_id),
            # D-300: the per-tenant bva-boundary flag rides the operational
            # context (identity-preserving); missing column/row -> False.
            enable_bva_boundaries=_bva_boundaries_enabled(tenant_id),
        )
        api_key = api_key_resolver(tenant_id, job.environment_id)
        result = run_generation(
            request, tenant_id=tenant_id, api_key=api_key,
            environment_id=job.environment_id,        # D-286: scope S1 to the run's org
            # Migration 060: thread the tenant policy so route_model honors
            # llm_model_override (and, incidentally, revives always_use_opus
            # on this path — it was dead code while nothing passed a policy).
            tenant_policy=_load_tenant_policy(tenant_id),
            # Cross-reference attribution: the job's owner is the only user
            # identity on this path (the worker runs unattended), so it is what
            # llm_usage_log.user_id records for the spend.
            user_id=job.created_by,
            tool_turn_fn=tool_turn_fn)
        kind = (result.results[0].outcome.outcome_kind.value
                if result.results else "unknown")
        log.info("s3 generation job %s completed (outcome=%s)", job.id, kind)
        store.complete(job.id)
    except Exception as exc:
        log.warning("s3 generation job %s failed: %s", job.id, exc)
        store.fail(job.id, error_code=_classify_error(exc),
                   error_message=str(exc)[:500])
    return job.id


def run_s3_generation_tick(
    tenant_ids, *, api_key_resolver: ApiKeyResolver, tool_turn_fn=None,
) -> dict[int, str]:
    """One job per tenant, with per-tenant isolation (the resilient enrichment
    pattern): a tenant whose claim/processing raises is logged and skipped — it
    never starves the others. Returns ``{tenant_id: outcome}`` for observability
    (``processed:<id>`` / ``empty`` / ``error:<Type>``)."""
    outcomes: dict[int, str] = {}
    for tid in tenant_ids:
        try:
            jid = process_job_for_tenant(
                tid, api_key_resolver=api_key_resolver, tool_turn_fn=tool_turn_fn)
            outcomes[tid] = f"processed:{jid}" if jid is not None else "empty"
        except Exception as exc:
            log.warning("s3_generation_tick: tenant %s failed: %s", tid, exc)
            outcomes[tid] = f"error:{type(exc).__name__}"
    return outcomes


def run_s3_reaper_tick(tenant_ids, *, stale_minutes: int = 10) -> dict[int, int]:
    """The scheduler's counterpart to the consumer tick (D-106.4 slice 5): fail
    jobs stuck past the heartbeat timeout, per tenant, with the same per-tenant
    isolation (one tenant's failure never starves the others). Returns
    ``{tenant_id: reaped_count}`` (a tenant whose reap raises records 0)."""
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.shared.stale_tenants import skip_unprovisioned
    reaped: dict[int, int] = {}
    for tid in tenant_ids:
        try:
            with get_tenant_connection(tid) as conn:
                if skip_unprovisioned(conn, tid, "s3_generation_jobs", log):
                    reaped[tid] = 0
                    continue
            reaped[tid] = GenerationJobStore(tid).reap_stale_jobs(stale_minutes=stale_minutes)
        except Exception as exc:
            log.warning("s3_reaper_tick: tenant %s failed: %s", tid, exc)
            reaped[tid] = 0
    return reaped

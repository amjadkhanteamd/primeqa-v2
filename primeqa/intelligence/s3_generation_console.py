"""S3 generation console — v1's UI bridge to Substrate-3 generation + the S2
read of what it produced (D-165, UI Area 2 slice 2a).

v1 owns the bridge (the allowed v1→substrate direction, like ``s1_sync_console``
and ``substrate_insights``). Three operations for the ``/requirements`` surface:

  - :func:`trigger_s3_generation` — resolve a v1 requirement → ``{key, text}``
    (``s3_enqueue.resolve_requirement``) + validate the env, then pin a queued
    S3 job (``enqueue_s3_generation``). The worker's ``s3_generation_tick`` runs
    it async; there is no synchronous path.
  - :func:`read_requirement_claims` — the requirement's generated test plan:
    ``coordinator.list_tests_by_requirement`` (the ``generated_from`` links the
    persister writes per D-166) → per test ``get_latest_claim`` +
    ``list_active_recipes``, flattened for the template.
  - :func:`read_latest_s3_job` — the most-recent S3 job for the requirement key,
    so a page reload during generation re-renders progress + resumes polling.

All three are **best-effort** — never raise; a substrate hiccup returns
``available=False`` / ``ok=False`` rather than breaking the requirement page. The
requirement key is the substrate ``external_key``: ``jira_key`` or ``req-<id>``,
exactly as ``s3_enqueue._requirement_to_ref`` mints it (the single source of
truth — import it rather than re-deriving, so the read can never drift from what
generation wrote).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

_LATEST_JOB_SQL = (
    "SELECT id, status, progress_pct, progress_msg, error_code, error_message, "
    "created_at, completed_at "
    "FROM s3_generation_jobs WHERE requirement_key = :rk "
    "ORDER BY created_at DESC LIMIT 1")

_ACTIVE = ("queued", "claimed", "running")


def _iso(v):
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


# --- trigger (v1 read + substrate enqueue) -----------------------------------

def trigger_s3_generation(db, *, tenant_id: int, requirement_id: int,
                          environment_id: int, created_by=None) -> dict:
    """Resolve the v1 requirement + validate the env, then enqueue an S3 job. The
    worker runs it async. Best-effort — returns ``{ok: False, error: ...}`` on any
    failure (never raises). Idempotent on ``(requirement_key, s1_version_seq)``: a
    re-trigger against the same S1 version returns the existing job."""
    try:
        from primeqa.core.repository import EnvironmentRepository
        from primeqa.intelligence.s3_enqueue import resolve_requirement
        from primeqa.generation.intake import enqueue_s3_generation

        ref = resolve_requirement(db, requirement_id, tenant_id)
        if ref is None:
            return {"ok": False, "error": "Requirement not found."}
        if EnvironmentRepository(db).get_environment(environment_id, tenant_id) is None:
            return {"ok": False, "error": "Environment not found."}
        job = enqueue_s3_generation(
            tenant_id=tenant_id, requirement_ref=ref,
            environment_id=environment_id, created_by=created_by)
        return {"ok": True, "job_id": job.id, "status": job.status,
                "requirement_key": ref["key"]}
    except Exception as exc:                          # e.g. no S1 version pinned yet
        log.warning("trigger_s3_generation failed for tenant %s req %s: %s",
                    tenant_id, requirement_id, exc)
        return {"ok": False, "error": str(exc)}


# --- read: the requirement's generated test plan (S2 claims + recipes) -------

def _read_claims(session, requirement_key: str) -> list[dict]:
    """Pure: the requirement's ``generated_from`` test plan on an open S2 session.
    Directly testable on the semantic harness with seeded links/claims/recipes."""
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    coord = SemanticTransactionCoordinator()
    matches = coord.list_tests_by_requirement(
        session, external_system="jira", external_key=requirement_key,
        link_kind="generated_from")
    claims = []
    for m in matches:
        claim = coord.get_latest_claim(session, m.test_id)
        if claim is None:                             # link with no live claim — skip
            continue
        recipes = coord.list_active_recipes(session, m.test_id)
        claims.append({
            "test_id": str(m.test_id),
            "archetype": claim.archetype,
            "claim_kind": claim.claim_kind,
            "status": claim.status,
            "version_seq": claim.version_seq,
            "recipe_count": len(recipes),
            "recipes": [{"trigger_kind": r.trigger_kind, "recipe_kind": r.recipe_kind,
                         "priority": r.priority, "status": r.status} for r in recipes],
            "linked_at": _iso(m.linked_at),
        })
    # deterministic: archetype then claim_kind then test_id
    claims.sort(key=lambda c: (c["archetype"] or "", c["claim_kind"] or "", c["test_id"]))
    return claims


def read_requirement_claims(tenant_id: int, requirement_key: str) -> dict:
    """Best-effort read of the requirement's generated test plan. Never raises.
    Returns ``{available, claims}`` — ``available=False`` on any read error (e.g.
    the tenant has no substrate schema)."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                return {"available": True, "claims": _read_claims(session, requirement_key)}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_requirement_claims unavailable for tenant %s key %s: %s",
                    tenant_id, requirement_key, exc)
        return {"available": False, "claims": []}


# --- read: the latest S3 job for the requirement (in-flight progress) --------

def _read_latest_job(conn, requirement_key: str) -> dict | None:
    """Pure: the most-recent S3 job for a requirement key on an open tenant conn."""
    row = conn.execute(text(_LATEST_JOB_SQL), {"rk": requirement_key}).mappings().first()
    if row is None:
        return None
    return {
        "id": row["id"], "status": row["status"],
        "active": row["status"] in _ACTIVE,
        "progress_pct": row["progress_pct"] or 0,
        "progress_msg": row["progress_msg"],
        "error_code": row["error_code"], "error_message": row["error_message"],
        "created_at": _iso(row["created_at"]), "completed_at": _iso(row["completed_at"]),
    }


def read_latest_s3_job(tenant_id: int, requirement_key: str) -> dict:
    """Best-effort read of the requirement's most-recent S3 job. Never raises.
    Returns ``{available, job}`` — ``job=None`` when none exists yet;
    ``available=False`` on any read error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            return {"available": True, "job": _read_latest_job(conn, requirement_key)}
    except Exception as exc:
        log.warning("read_latest_s3_job unavailable for tenant %s key %s: %s",
                    tenant_id, requirement_key, exc)
        return {"available": False, "job": None}


# --- read: a single claim's semantic detail + its recipes (2b) ---------------

def _dump(body):
    """A Pydantic body (asserted_truth / a recipe leg) → a JSON-safe dict for the
    template. Tolerant: returns ``{}`` for None / a non-model value."""
    if body is not None and hasattr(body, "model_dump"):
        return body.model_dump(mode="json")
    return body if isinstance(body, dict) else {}


def _read_claim_detail(session, test_id) -> dict | None:
    """Pure: the current claim version + its active recipes, bodies dumped to
    JSON-safe dicts. Returns ``None`` when no live claim matches ``test_id``.
    Directly testable on the generation harness."""
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    coord = SemanticTransactionCoordinator()
    claim = coord.get_latest_claim(session, test_id)
    if claim is None:
        return None
    recipes = coord.list_active_recipes(session, test_id)
    return {
        "test_id": str(test_id),
        "archetype": claim.archetype,
        "claim_kind": claim.claim_kind,
        "status": claim.status,
        "version_seq": claim.version_seq,
        "valid_from": _iso(claim.valid_from),
        "identity_hash": claim.identity_hash,
        "asserted_truth": _dump(claim.asserted_truth),
        "semantic_conditions": _dump(claim.semantic_conditions),
        "recipes": [{
            "recipe_id": str(r.recipe_id),
            "trigger_kind": r.trigger_kind,
            "recipe_kind": r.recipe_kind,
            "priority": r.priority,
            "status": r.status,
            "version_seq": r.version_seq,
            "causal_initiation": _dump(r.causal_initiation),
            "observation_realization": _dump(r.observation_realization),
            "execution_environment": _dump(r.execution_environment),
        } for r in recipes],
    }


def read_claim_detail(tenant_id: int, test_id) -> dict:
    """Best-effort read of a single claim's semantic detail + recipes. Never
    raises. Returns ``{available, found, claim}`` — ``found=False`` when no live
    claim matches; ``available=False`` on any read error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                detail = _read_claim_detail(session, test_id)
                return {"available": True, "found": detail is not None, "claim": detail}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_claim_detail unavailable for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"available": False, "found": False, "claim": None}

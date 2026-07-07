"""S1-sync console — v1's UI bridge to the Substrate-1 metadata sync (D-164, UI Area 1).

v1 owns the bridge (the allowed v1→substrate direction, like ``substrate_insights``).
Two operations for the ``/environments`` admin surface:

  - :func:`trigger_s1_sync` — provision the ``connected_orgs`` sync target for an
    environment (``ensure_connected_org_for_environment``) + enqueue a sync job
    (``SyncJobStore.create_or_get_job``). The worker's ``s1_sync_tick`` runs it
    async; there is no synchronous path.
  - :func:`read_s1_sync_status` — the env's ``connected_orgs`` row → its latest
    ``s1_sync_jobs`` + ``sync_runs``, flattened for the panel/poll.
    ``provisioned=False`` when the env has no sync target yet.

Both are **best-effort** — never raise; a substrate hiccup returns
``available=False`` / ``ok=False`` rather than breaking the env page. Tenant-scoped
via ``get_tenant_connection``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

log = logging.getLogger(__name__)

# The env's connected_org id resolves inside each query (a correlated subquery on
# environment_id), so we only ever bind the int env id — no UUID round-trip.
_ORG_SQL = (
    "SELECT id, last_sync_completed_at FROM connected_orgs WHERE environment_id = :eid")
_JOB_SQL = (
    "SELECT status, error_code, error_message, created_at, "
    "EXTRACT(EPOCH FROM (NOW() - COALESCE(heartbeat_at, claimed_at, created_at))) "
    "  AS heartbeat_age_s "
    "FROM s1_sync_jobs "
    "WHERE connected_org_id = (SELECT id FROM connected_orgs WHERE environment_id = :eid) "
    "ORDER BY created_at DESC LIMIT 1")

# D-178: a job claimed/running whose heartbeat is older than this is orphaned — the
# worker died mid-sync. Matches the reaper window so the panel and the reaper agree.
_ORPHAN_THRESHOLD_S = 10 * 60
_RUN_SQL = (
    "SELECT status, last_completed_phase, started_at, completed_at, "
    "entities_inserted, edges_inserted, error_message "
    "FROM sync_runs "
    "WHERE source_org_id = (SELECT id FROM connected_orgs WHERE environment_id = :eid) "
    "ORDER BY started_at DESC LIMIT 1")


def _iso(v):
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


def _read_status(conn, environment_id: int) -> dict:
    """Pure: read S1 sync status on an already-open **tenant-scoped** connection.
    Directly testable on the semantic ``conn`` fixture (no own connection)."""
    org = conn.execute(text(_ORG_SQL), {"eid": environment_id}).mappings().first()
    if org is None:
        return {"available": True, "provisioned": False}
    job = conn.execute(text(_JOB_SQL), {"eid": environment_id}).mappings().first()
    run = conn.execute(text(_RUN_SQL), {"eid": environment_id}).mappings().first()
    # D-178: distinguish an actively-running sync from an ORPHANED one (worker died
    # mid-sync, heartbeat gone stale). Without this the panel showed "running"
    # identically for a live sync and a 15h-dead one. The reaper will fail+resume an
    # orphan within the window; the panel says so instead of implying progress.
    hb_age = job["heartbeat_age_s"] if job else None
    orphaned = bool(job and job["status"] in ("claimed", "running")
                    and hb_age is not None and hb_age > _ORPHAN_THRESHOLD_S)
    # D-180: count of this org's terminal-failed enrichment rows — drives the panel's
    # conditional "Re-run enrichment (N)" button. Best-effort; a count error -> 0.
    try:
        from primeqa.sync.readiness import count_failed_enrichment
        failed_enrichment = count_failed_enrichment(conn, org["id"])
    except Exception:
        failed_enrichment = 0
    # UI Pass 2: the enrichment lane's done/total for the live progress bar.
    # Best-effort; a count error -> 0/0 (the bar renders an empty enrichment lane).
    try:
        from primeqa.sync.readiness import count_enrichment_progress
        enrichment = count_enrichment_progress(conn, org["id"])
    except Exception:
        enrichment = {"done": 0, "total": 0}
    return {
        "available": True,
        "provisioned": True,
        "last_sync_completed_at": _iso(org["last_sync_completed_at"]),
        "orphaned": orphaned,
        "failed_enrichment": failed_enrichment,
        "enrichment": enrichment,
        "job": ({"status": job["status"], "error_code": job["error_code"],
                 "error_message": job["error_message"],
                 "created_at": _iso(job["created_at"]),
                 "heartbeat_age_s": (int(hb_age) if hb_age is not None else None),
                 "orphaned": orphaned} if job else None),
        "run": ({"status": run["status"],
                 "last_completed_phase": run["last_completed_phase"],
                 "started_at": _iso(run["started_at"]),
                 "completed_at": _iso(run["completed_at"]),
                 "entities_inserted": run["entities_inserted"],
                 "edges_inserted": run["edges_inserted"],
                 "error_message": run["error_message"]} if run else None),
    }


def read_s1_sync_status(tenant_id: int, environment_id: int) -> dict:
    """Best-effort S1-sync status for an environment. Never raises.

    Returns ``{available, provisioned, last_sync_completed_at, job, run}`` —
    ``provisioned=False`` when no ``connected_orgs`` row exists yet;
    ``available=False`` on any read error.
    """
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            return _read_status(conn, environment_id)
    except Exception as exc:                     # absent schema / read error
        log.warning("s1 sync status unavailable for tenant %s env %s: %s",
                    tenant_id, environment_id, exc)
        return {"available": False, "provisioned": False}


def phase_order() -> list:
    """The ordered structural sync phases (the single source — S1's
    ``ENTITY_ORDER``), for the UI Pass 2 progress bar's "N of 11" lane. Best-effort:
    ``[]`` if the substrate module can't be imported (the bar degrades to no phase
    lane). Lazy import keeps this bridge import-time-decoupled from the engine."""
    try:
        from primeqa.sync.fk_assertion import ENTITY_ORDER
        return list(ENTITY_ORDER)
    except Exception as exc:
        log.warning("phase_order unavailable: %s", exc)
        return []


_FRESHNESS_FAIL = {"available": False, "provisioned": False, "last_success_at": None,
                   "age_hours": None, "current_version_seq": None, "usable": False}


def _read_freshness(conn, environment_id: int) -> dict:
    """Pure: S1 freshness/health on an already-open **tenant-scoped** conn (D-183).
    Directly testable on the semantic ``conn`` fixture (no own connection).

    Returns ``{available, provisioned, last_success_at, age_hours,
    current_version_seq, usable}``:
      - ``provisioned`` — a ``connected_orgs`` row exists for the env.
      - ``current_version_seq`` / ``last_success_at`` / ``age_hours`` — read from
        **this env's own** latest ``sync_runs`` with a terminal-success status
        (``success`` / ``partial_success``), non-NULL ``completed_at``, and a
        non-NULL ``logical_version_seq``. ENV-SCOPED via ``source_org_id`` — NOT
        the tenant-global ``MAX(logical_versions.version_seq)``, which would let a
        never-synced env in a multi-env tenant inherit a sibling's version and
        wrongly pass the gate (D-183 review). ``None`` when this env has no
        successful sync yet (a first sync still running counts as not-yet-synced).
        ``last_success_at`` deliberately NOT ``connected_orgs.last_sync_completed_at``
        (observed unreliable / NULL in prod even post-success).
      - ``age_hours`` — hours since ``last_success_at`` (``None`` when not usable).
      - ``usable`` — ``provisioned AND current_version_seq is not None`` (this env's
        own org model has been synced + is queryable for a run).
    """
    org = conn.execute(text(
        "SELECT id FROM connected_orgs WHERE environment_id = :eid"),
        {"eid": environment_id}).first()
    if org is None:
        return {"available": True, "provisioned": False, "last_success_at": None,
                "age_hours": None, "current_version_seq": None, "usable": False}
    last = conn.execute(text(
        "SELECT completed_at, logical_version_seq FROM sync_runs "
        "WHERE source_org_id = :oid "
        "  AND status IN ('success', 'partial_success') "
        "  AND completed_at IS NOT NULL "
        "  AND logical_version_seq IS NOT NULL "
        "ORDER BY completed_at DESC LIMIT 1"),
        {"oid": str(org[0])}).first()
    last_success_at = last[0] if last else None
    current_version_seq = int(last[1]) if last else None
    age_hours = None
    if last_success_at is not None:
        age_hours = (datetime.now(timezone.utc)
                     - last_success_at).total_seconds() / 3600.0
    return {
        "available": True,
        "provisioned": True,
        "last_success_at": _iso(last_success_at),
        "age_hours": age_hours,
        "current_version_seq": current_version_seq,
        "usable": current_version_seq is not None,
    }


def read_s1_freshness(tenant_id: int, environment_id: int) -> dict:
    """Best-effort S1 freshness/health for an environment — the GAP-2 (D-183)
    substitute for v1 metadata freshness in Preflight. Never raises;
    ``available=False`` on any read error (caller falls back to ``meta_*`` during
    the parallel window). Shape per :func:`_read_freshness`."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            return _read_freshness(conn, environment_id)
    except Exception as exc:                     # absent schema / read error
        log.warning("read_s1_freshness unavailable for tenant %s env %s: %s",
                    tenant_id, environment_id, exc)
        return dict(_FRESHNESS_FAIL)


def trigger_s1_sync(tenant_id: int, environment_id: int, sf_instance_url: str, *,
                    created_by=None) -> dict:
    """Provision the env's ``connected_orgs`` sync target + enqueue a sync job. The
    worker runs it async. Best-effort — returns ``{ok: False, error: ...}`` on any
    failure (never raises). Idempotent: a re-trigger while a job is active returns
    that active job."""
    if not sf_instance_url:
        return {"ok": False, "error": "environment has no Salesforce instance URL"}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.sync.credentials import ensure_connected_org_for_environment
        from primeqa.sync.jobs import SyncJobStore

        with get_tenant_connection(tenant_id) as conn:          # commits on exit
            cid = ensure_connected_org_for_environment(
                conn, environment_id, sf_instance_url)
        job = SyncJobStore(tenant_id).create_or_get_job(        # opens + commits its own conn
            connected_org_id=cid, environment_id=environment_id, created_by=created_by)
        return {"ok": True, "connected_org_id": str(cid),
                "job_id": job.id, "status": job.status}
    except Exception as exc:
        log.warning("trigger_s1_sync failed for tenant %s env %s: %s",
                    tenant_id, environment_id, exc)
        return {"ok": False, "error": str(exc)}


def requeue_s1_enrichment(tenant_id: int, environment_id: int) -> dict:
    """Reset the env's connected-org ``failed_permanent`` enrichment rows back to
    ``pending`` (D-180) so the worker re-attempts them under the per-env provider
    keys (D-179). Best-effort — returns ``{ok: False, error: ...}`` on any failure
    (never raises). The worker's ``enrichment_tick`` drains the requeued rows; this
    bridge does no embedding itself.

    Returns ``{ok: True, requeued: N}`` (``N`` may be 0 if nothing was failed)."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.sync.readiness import requeue_failed_enrichment

        with get_tenant_connection(tenant_id) as conn:          # commits on exit
            org = conn.execute(text(_ORG_SQL),
                               {"eid": environment_id}).mappings().first()
            if org is None:
                return {"ok": False, "error": "environment has no connected org"}
            n = requeue_failed_enrichment(conn, org["id"])
        return {"ok": True, "requeued": int(n)}
    except Exception as exc:
        log.warning("requeue_s1_enrichment failed for tenant %s env %s: %s",
                    tenant_id, environment_id, exc)
        return {"ok": False, "error": str(exc)}


# --- org-model browser (D-164, UI 1c) ----------------------------------------
# Read-only view of the synced S1 org model (objects -> fields / VRs) at the
# tenant's current version. Edge directions match the S7 impact recipe + the
# metadata S1-reader: fields are BELONGS_TO-inbound to an object, VRs are
# APPLIES_TO-inbound.

def _read_org_model(model, object_api_name=None) -> dict:
    """Pure: read the current S1 org model on a ``SemanticOrgModel``. Raises
    ``VersionNotFoundError`` (caught by the wrapper) when nothing is synced yet."""
    seq = model.current_version_seq()
    if object_api_name:
        objs = model.get_entities("Object", at_seq=seq,
                                  filters={"sf_api_name": object_api_name})
        if not objs:
            return {"available": True, "synced": True, "version_seq": seq, "object": None}
        obj = objs[0]
        fields, vrs = [], []
        for r in model.get_related(obj.id, edge_types=["BELONGS_TO"],
                                   direction="inbound", at_seq=seq):
            if r.entity.entity_type == "Field":
                fields.append({"api_name": r.entity.sf_api_name,
                               "display_name": r.entity.display_name})
        for r in model.get_related(obj.id, edge_types=["APPLIES_TO"],
                                   direction="inbound", at_seq=seq):
            if r.entity.entity_type == "ValidationRule":
                vrs.append({"api_name": r.entity.sf_api_name})
        return {"available": True, "synced": True, "version_seq": seq, "object": {
            "api_name": obj.sf_api_name, "display_name": obj.display_name,
            "fields": sorted(fields, key=lambda x: x["api_name"] or ""),
            "validation_rules": sorted(vrs, key=lambda x: x["api_name"] or "")}}
    objects = sorted(
        ({"api_name": o.sf_api_name, "display_name": o.display_name}
         for o in model.get_entities("Object", at_seq=seq)),
        key=lambda x: x["api_name"] or "")
    return {"available": True, "synced": True, "version_seq": seq, "objects": objects}


def read_org_model(tenant_id: int, object_api_name=None,
                   environment_id=None) -> dict:
    """Best-effort read of the tenant's current S1 org model. Never raises.
    ``synced=False`` when no S1 version exists yet; ``available=False`` on error.

    ``environment_id`` scopes the read to that env's connected org — the only
    correct mode on a multi-org tenant, where the unscoped model is the UNION
    of every org's entities (ambiguous when two orgs share an Object api-name).
    ``None`` keeps the org-blind read (vacuously correct on a single org). An
    env with no connected org reads as ``synced=False``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.semantic.query import SemanticOrgModel, VersionNotFoundError
        with get_tenant_connection(tenant_id) as conn:
            org_id = None
            if environment_id is not None:
                from primeqa.sync.credentials import (
                    get_connected_org_for_environment,
                )
                org_id = get_connected_org_for_environment(conn, environment_id)
                if org_id is None:
                    return {"available": True, "synced": False}
            try:
                return _read_org_model(
                    SemanticOrgModel(conn, connected_org_id=org_id),
                    object_api_name)
            except VersionNotFoundError:
                return {"available": True, "synced": False}
    except Exception as exc:
        log.warning("org-model read unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "synced": False}


# --- sync history (UI Pass 1) -------------------------------------------------
# The first PLURAL sync_runs reader: every other read is LIMIT 1. Paginated, per
# env (its connected_org). Cost + the model THAT run used ride a SEPARATE batched
# read of public.llm_usage_log (cross-schema: sync_runs is per-tenant, the usage
# log is public) — stitched in Python, never JOINed.

def _duration_s(started, completed):
    """Whole seconds between two datetimes; ``None`` when either is missing (a
    still-running row has no completed_at)."""
    if started is None or completed is None:
        return None
    try:
        return int((completed - started).total_seconds())
    except Exception:
        return None


# Only columns the recon proved are populated; ``error_message IS NOT NULL`` ->
# has_error (we never expose the raw text on the list row). describe_calls /
# failure_category / sf_error_code / permission_gaps are conditional (rendered
# only when present) — landed recently, no backfill, so old rows show blank.
_HISTORY_SQL = (
    "SELECT id, status, started_at, completed_at, skipped, "
    "entities_inserted, entities_superseded, entities_unchanged, edges_inserted, "
    "embeddings_generated, summaries_generated, describe_calls, "
    "failure_category, sf_error_code, permission_gaps, "
    "(error_message IS NOT NULL) AS has_error "
    "FROM sync_runs WHERE source_org_id = CAST(:org AS uuid) "
    "ORDER BY started_at DESC LIMIT :limit OFFSET :offset")


def _ai_cost(cost) -> float:
    """AI cost = embeddings + summaries USD (NOT total_cost — the run's own LLM
    spend). ``0.0`` when the run has no attributed usage (e.g. a skipped run)."""
    if not cost:
        return 0.0
    return float(cost.get("embedding", {}).get("cost_usd", 0.0)) + \
        float(cost.get("summary", {}).get("cost_usd", 0.0))


def _shape_history_run(row, cost) -> dict:
    """Pure: one ``sync_runs`` row + its cost/model roll-up (or ``None``) -> the
    list-row dict. No I/O — unit-testable on plain dicts. ``summary_models`` is the
    model THAT run used (from llm_usage_log); ``[]`` -> the UI renders "-"."""
    models = (cost or {}).get("summary_models") or []
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "started_at": _iso(row["started_at"]),
        "completed_at": _iso(row["completed_at"]),
        "duration_s": _duration_s(row["started_at"], row["completed_at"]),
        "skipped": bool(row["skipped"]),
        "entities_inserted": row["entities_inserted"],
        "entities_superseded": row["entities_superseded"],
        "entities_unchanged": row["entities_unchanged"],
        "edges_inserted": row["edges_inserted"],
        "embeddings_generated": row["embeddings_generated"],
        "summaries_generated": row["summaries_generated"],
        "describe_calls": row["describe_calls"],
        "failure_category": row["failure_category"],
        "sf_error_code": row["sf_error_code"],
        "permission_gaps": row["permission_gaps"],
        "has_error": bool(row["has_error"]),
        "ai_cost_usd": _ai_cost(cost),
        "summary_models": models,
    }


def _history_envelope(runs, total, page, per_page) -> dict:
    """Pure: the paginated envelope (mirrors ``list_claims``)."""
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {"available": True, "provisioned": True, "runs": runs,
            "total": total, "page": page, "per_page": per_page,
            "total_pages": total_pages}


def read_s1_sync_history(tenant_id: int, environment_id: int, *, page: int = 1,
                         per_page: int = 20) -> dict:
    """Best-effort paginated sync-run history for an environment (UI Pass 1).

    Returns ``{available, provisioned, runs, total, page, per_page, total_pages}``
    — ``provisioned=False`` (no rows) when the env has no connected_org yet;
    ``available=False`` on any read error. ``per_page`` capped at 50. Never raises.
    The cost/model columns ride a batched ``llm_usage_log`` read keyed by the page's
    run-ids (cross-schema, stitched in Python — never one query per row)."""
    page = max(1, page)
    per_page = max(1, min(per_page, 50))
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            org = conn.execute(text(
                "SELECT CAST(id AS text) FROM connected_orgs WHERE environment_id = :eid"),
                {"eid": environment_id}).scalar()
            if not org:
                return {"available": True, "provisioned": False, "runs": [],
                        "total": 0, "page": page, "per_page": per_page,
                        "total_pages": 1}
            total = int(conn.execute(text(
                "SELECT COUNT(*) FROM sync_runs WHERE source_org_id = CAST(:org AS uuid)"),
                {"org": org}).scalar() or 0)
            # Snap an over-range page back to the last real page so the table +
            # pagination still render (else an empty page reads as "no syncs").
            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            rows = conn.execute(text(_HISTORY_SQL), {
                "org": org, "limit": per_page,
                "offset": (page - 1) * per_page}).mappings().all()
        # Cross-schema cost/model — own session on the public engine, stitched here.
        from primeqa.intelligence.llm.usage import get_sync_run_costs_batch
        costs = get_sync_run_costs_batch(
            [str(r["id"]) for r in rows], tenant_id=tenant_id)
        runs = [_shape_history_run(r, costs.get(str(r["id"]))) for r in rows]
        return _history_envelope(runs, total, page, per_page)
    except Exception as exc:
        log.warning("s1 sync history unavailable for tenant %s env %s: %s",
                    tenant_id, environment_id, exc)
        return {"available": False, "provisioned": False, "runs": [],
                "total": 0, "page": page, "per_page": per_page, "total_pages": 1}


# --- run detail (UI Pass 1, the gap/error drilldown) -------------------------
# OWNERSHIP is enforced IN SQL: the row must match BOTH the run id AND this env's
# connected_org (correlated subquery). A run from a different env's org -> no match
# -> found=False -> the route 404s. There is no code path that renders a run the
# env does not own.

_RUN_DETAIL_SQL = (
    "SELECT id, status, started_at, completed_at, skipped, phase, "
    "last_completed_phase, logical_version_seq, "
    "entities_inserted, entities_superseded, entities_unchanged, "
    "edges_inserted, edges_superseded, embeddings_generated, summaries_generated, "
    "summaries_failed, describe_calls, failure_category, sf_error_code, "
    "permission_gaps, gap_details, error_message "
    "FROM sync_runs "
    "WHERE id = CAST(:run_id AS uuid) "
    "  AND source_org_id = (SELECT id FROM connected_orgs WHERE environment_id = :eid)")


def _shape_run_detail(row, cost) -> dict:
    """Pure: a full ``sync_runs`` row + cost/model -> the detail dict. ``gap_details``
    passes through as the list of ``{site, category, sf_error_code, context}`` the
    Phase-2 writer stored (or ``[]``)."""
    models = (cost or {}).get("summary_models") or []
    # gap_details is JSONB -> psycopg2 returns a parsed list (or None); anything
    # else is treated as "no gaps".
    gaps = row["gap_details"] if isinstance(row["gap_details"], list) else []
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "started_at": _iso(row["started_at"]),
        "completed_at": _iso(row["completed_at"]),
        "duration_s": _duration_s(row["started_at"], row["completed_at"]),
        "skipped": bool(row["skipped"]),
        "phase": row["phase"],
        "last_completed_phase": row["last_completed_phase"],
        "logical_version_seq": row["logical_version_seq"],
        "entities_inserted": row["entities_inserted"],
        "entities_superseded": row["entities_superseded"],
        "entities_unchanged": row["entities_unchanged"],
        "edges_inserted": row["edges_inserted"],
        "edges_superseded": row["edges_superseded"],
        "embeddings_generated": row["embeddings_generated"],
        "summaries_generated": row["summaries_generated"],
        "summaries_failed": row["summaries_failed"],
        "describe_calls": row["describe_calls"],
        "failure_category": row["failure_category"],
        "sf_error_code": row["sf_error_code"],
        "permission_gaps": row["permission_gaps"],
        "gap_details": gaps,
        "error_message": row["error_message"],
        "ai_cost_usd": _ai_cost(cost),
        "summary_models": models,
    }


def read_s1_run_detail(tenant_id: int, environment_id: int, run_id) -> dict:
    """Best-effort single-run detail, OWNERSHIP-GATED to ``environment_id``.

    Returns ``{available, found, run}``. ``found=False`` when the run does not
    belong to this env's connected_org (or does not exist) — the route renders a
    404, never the run. Fail-closed: any read error -> ``found=False`` (we never
    render a run we could not confirm ownership of). Never raises."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            row = conn.execute(text(_RUN_DETAIL_SQL),
                               {"run_id": str(run_id),
                                "eid": environment_id}).mappings().first()
        if row is None:
            return {"available": True, "found": False, "run": None}
        from primeqa.intelligence.llm.usage import get_sync_run_costs_batch
        costs = get_sync_run_costs_batch([str(row["id"])])
        return {"available": True, "found": True,
                "run": _shape_run_detail(row, costs.get(str(row["id"])))}
    except Exception as exc:
        log.warning("s1 run detail unavailable for tenant %s env %s run %s: %s",
                    tenant_id, environment_id, run_id, exc)
        return {"available": False, "found": False, "run": None}


# --- org picker + model diff (UI Pass 1) -------------------------------------

def list_connected_orgs(tenant_id: int) -> list:
    """Best-effort: the tenant's connected orgs as the diff picker needs them —
    ``[{id, label, environment_id, sf_instance_url}]``, EXCLUDING fixture rows with
    a NULL ``environment_id``. ``id`` is the connected_org UUID (model-diff axis);
    ``environment_id`` is the result-diff axis. Ordered by env id. ``[]`` on error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT CAST(id AS text) AS id, label, environment_id, sf_instance_url "
                "FROM connected_orgs WHERE environment_id IS NOT NULL "
                "ORDER BY environment_id"), {}).mappings().all()
        return [{"id": r["id"], "label": r["label"],
                 "environment_id": r["environment_id"],
                 "sf_instance_url": r["sf_instance_url"]} for r in rows]
    except Exception as exc:
        log.warning("list_connected_orgs unavailable for tenant %s: %s",
                    tenant_id, exc)
        return []


def model_diff_for_envs(tenant_id: int, env_a: int, env_b: int) -> dict:
    """Best-effort MODEL diff between two environments' orgs (wraps
    :func:`primeqa.semantic.diff.diff_org_models`, resolving each env -> its
    connected_org UUID). Returns the diff dict with ``available=True``, or
    ``{available: False, reason}`` when an env is not synced / on error. Pure read."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.sync.credentials import get_connected_org_for_environment
        from primeqa.semantic.diff import diff_org_models
        with get_tenant_connection(tenant_id) as conn:
            org_a = get_connected_org_for_environment(conn, env_a)
            org_b = get_connected_org_for_environment(conn, env_b)
            if not org_a or not org_b:
                return {"available": False,
                        "reason": "one or both environments are not synced to the substrate yet"}
            d = diff_org_models(conn, org_a, org_b)
        d["available"] = True
        return d
    except Exception as exc:
        log.warning("model_diff_for_envs unavailable for tenant %s envs %s/%s: %s",
                    tenant_id, env_a, env_b, exc)
        return {"available": False, "reason": "diff unavailable right now"}

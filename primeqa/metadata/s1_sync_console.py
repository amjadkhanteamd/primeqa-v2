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
    return {
        "available": True,
        "provisioned": True,
        "last_sync_completed_at": _iso(org["last_sync_completed_at"]),
        "orphaned": orphaned,
        "failed_enrichment": failed_enrichment,
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


def read_org_model(tenant_id: int, object_api_name=None) -> dict:
    """Best-effort read of the tenant's current S1 org model. Never raises.
    ``synced=False`` when no S1 version exists yet; ``available=False`` on error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.semantic.query import SemanticOrgModel, VersionNotFoundError
        with get_tenant_connection(tenant_id) as conn:
            try:
                return _read_org_model(SemanticOrgModel(conn), object_api_name)
            except VersionNotFoundError:
                return {"available": True, "synced": False}
    except Exception as exc:
        log.warning("org-model read unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "synced": False}

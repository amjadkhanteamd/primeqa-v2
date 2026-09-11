"""Step 6a — the Results reader (LLD_STEP_6A_WORK_SURFACES §d).

Two things the runs list needs and did not have:

  - **the conformance lane**, so one list can span both kinds. A conformance
    row is an ``s6_ui_processing_runs`` row — the unit a person recognises —
    and its readiness is **never** the org axis: a browser-plane run is current
    or not against its claim set and inventory version, not against an org
    sequence (D-488's lane split).
  - **provenance**: what a functional run came FROM. Step 4 stamped `plan_id`
    on the execution job and run, so the row can name the schedule or the plan;
    where there is none the cell says "no plan (pre-planner)" and guesses
    nothing.

Both best-effort: the page renders without either.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

NO_PLAN = "no plan (pre-planner)"


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _with_session(tenant_id: int, session, fn):
    if session is not None:
        return fn(session)
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        s = Session(bind=conn)
        try:
            return fn(s)
        finally:
            s.close()


def list_conformance_runs(tenant_id: int, *, limit: int = 20, session=None) -> dict:
    """The browser plane's processing runs, newest first.

    ``currency`` is the conformance lane's readiness word — LATEST when this is
    the newest run for its claim set, SUPERSEDED when a newer one exists. The
    org axis is not consulted, because a conformance claim records no org reads
    (the fact behind D-488)."""
    def _read(s):
        rows = s.execute(text("""
            SELECT CAST(p.job_id AS text), CAST(p.claim_set_id AS text), p.engine,
                   p.engine_version, p.verdict_counts, p.processed_at,
                   cs.inventory_version, cs.persona_scope,
                   (p.processed_at = MAX(p.processed_at) OVER (PARTITION BY p.claim_set_id)) AS is_latest,
                   j.payload->'scope' AS scope
            FROM s6_ui_processing_runs p
            LEFT JOIN claim_sets cs ON cs.id = p.claim_set_id
            LEFT JOIN s4_ui_inspection_jobs j ON j.id = p.job_id
            ORDER BY p.processed_at DESC
            LIMIT :n
        """), {"n": int(limit)}).fetchall()
        out = []
        for r in rows:
            counts = r[4] or {}
            out.append({
                "kind": "conformance", "job_id": r[0], "claim_set_id": r[1],
                "engine": f"{r[2]} {r[3]}".strip(), "verdict_counts": counts,
                "processed_at": _iso(r[5]), "inventory_version": r[6],
                "persona_scope": r[7],
                "currency": "LATEST" if r[8] else "SUPERSEDED",
                "currency_sentence": (
                    "the newest processing run for this claim set" if r[8]
                    else "a newer processing run exists for this claim set"),
                "from": _scope_sentence(r[9]),
                "outcome_sentence": " · ".join(
                    f"{v} {k.lower()}" for k, v in sorted(counts.items()) if v) or "no verdict",
            })
        return {"available": True, "runs": out}
    try:
        return _with_session(tenant_id, session, _read)
    except Exception as exc:  # noqa: BLE001
        log.warning("conformance runs unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "runs": []}


def _scope_sentence(scope) -> str:
    """What a conformance job recorded about why it ran.

    Step 4 stamped `plan_id` on the EXECUTION job and run, never on the UI
    inspection job, so a conformance run's provenance survives only as whatever
    `payload.scope` recorded. Absent → "no plan (pre-planner)", never a guess.
    (FIX PLAN: the successor is a Step 4 change stamping the inspection job.)"""
    if not scope:
        return NO_PLAN
    if isinstance(scope, dict):
        kind = scope.get("scope_kind") or scope.get("kind")
        ref = scope.get("scope_ref") or scope.get("release_id") or scope.get("key")
        if kind and ref:
            return f"{kind} {ref}"
        if kind:
            return str(kind)
    return str(scope)[:60]


def attach_provenance(tenant_id: int, runs, *, session=None) -> None:
    """Add ``from_sentence`` / ``plan_id`` to functional run rows, in one read.

    A run carrying a plan names the schedule when the plan's scope IS a
    schedule, else the plan; a run with no plan id predates the planner and
    says so."""
    ids = [str(r.get("plan_id")) for r in (runs or []) if r.get("plan_id")]
    plans: dict = {}
    if ids:
        def _read(s):
            return {r[0]: (r[1], r[2]) for r in s.execute(text("""
                SELECT CAST(id AS text), scope_kind, scope_ref FROM run_plans
                WHERE CAST(id AS text) = ANY(:ids)
            """), {"ids": sorted(set(ids))}).fetchall()}
        try:
            plans = _with_session(tenant_id, session, _read)
        except Exception as exc:  # noqa: BLE001
            log.warning("run provenance unavailable for tenant %s: %s", tenant_id, exc)
    for r in (runs or []):
        pid = str(r.get("plan_id")) if r.get("plan_id") else None
        if not pid:
            r["from_sentence"], r["from_plan_id"] = NO_PLAN, None
            continue
        kind, ref = plans.get(pid, (None, None))
        r["from_plan_id"] = pid
        r["from_sentence"] = (f"schedule {ref}" if kind == "schedule"
                              else (f"{kind} {ref}" if kind else f"plan {pid[:8]}"))

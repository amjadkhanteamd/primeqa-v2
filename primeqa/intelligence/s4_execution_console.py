"""S4 execution console — v1's UI bridge to Substrate-4 execution + the S6 read of
the result (D-168, UI Area 3 slice 3a).

v1 owns the bridge (the allowed v1→substrate direction, like `s3_generation_console`
/ `s1_sync_console`). Area 3 leads with the **synchronous** run path (user decision,
D-168): one blocking call runs the claim's eligible recipe (any kind) and returns the
outcome + verdict in one shot — no queue, no poll, no job→run correlation gap.

  - :func:`trigger_claim_run` — run the eligible recipe for a claim `test_id` on an
    environment via `run_recipe_execution_for_tenant` (it owns its own tenant
    connection + commit, and self-resolves the SF client per recipe-kind). Returns
    the mapped outcome/verdict. Best-effort — never raises.
  - :func:`read_claim_runs` — the claim's recent run results via the S6 read API
    (`list_interpretations(claim_test_id=…)`), which carries outcome + verdict.

**Production safety is the caller's job** (D-168): the substrate has no prod guard
and a data-recipe run *mutates the org*, so the route gates with v1's
`environment_can_bulk_run` BEFORE calling :func:`trigger_claim_run`.
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text

log = logging.getLogger(__name__)


def _iso(v):
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


# --- run a claim (sync; returns outcome + verdict) ---------------------------

def _map_run_result(result) -> dict:
    """Pure: a ``RunPathResult`` → the template/flash dict. Duck-typed (reads
    ``ran`` / ``reason`` / ``selected_recipe_id`` / ``evidence.outcome`` /
    ``interpretation.verdict``) so it is directly unit-testable with stand-ins."""
    if not getattr(result, "ran", False):
        return {"ok": True, "ran": False, "reason": getattr(result, "reason", None)}
    ev = getattr(result, "evidence", None)
    interp = getattr(result, "interpretation", None)
    rid = getattr(result, "selected_recipe_id", None)
    return {
        "ok": True, "ran": True,
        "recipe_id": str(rid) if rid else None,
        "outcome": getattr(ev, "outcome", None) if ev is not None else None,
        # the semantic verdict lives on S6 (None if the best-effort interpret
        # step failed — the run outcome is still authoritative).
        "verdict": getattr(interp, "verdict", None) if interp is not None else None,
    }


def trigger_claim_run(tenant_id: int, test_id, environment_id: int, *,
                      client=None) -> dict:
    """Run the eligible recipe for ``test_id`` on ``environment_id`` (synchronous).
    Best-effort — returns ``{ok: False, error}`` on any failure (never raises).
    On success: ``{ok: True, ran, outcome, verdict, recipe_id}`` (``ran=False`` +
    ``reason`` when the claim has no approved/eligible recipe for the env — a
    first-class non-error result). ``client`` is injectable for tests; the route
    passes ``None`` so the engine self-resolves the SF client per recipe-kind."""
    try:
        from primeqa.execution_engine.run import run_recipe_execution_for_tenant
        result = run_recipe_execution_for_tenant(
            tenant_id, UUID(str(test_id)), environment_id=environment_id, client=client)
        return _map_run_result(result)
    except Exception as exc:                      # credential / SF / execution error
        log.warning("trigger_claim_run failed for tenant %s test %s env %s: %s",
                    tenant_id, test_id, environment_id, exc)
        return {"ok": False, "error": str(exc)}


# --- read the claim's recent runs (S6 verdict surface) -----------------------

# Read S4 runs (authoritative — they carry finished_at) LEFT JOINed to the S6
# verdict, newest-first. The S6 read alone has no time axis (it orders by the
# random-uuid run_id) and drops interpret-failed runs; reading S4 as the base
# fixes both (true recency + a run with a failed interpret still shows, verdict
# NULL). Tenant-scoped by the connection's search_path — no tenant_id column.
_CLAIM_RUNS_SQL = (
    "SELECT CAST(r.run_id AS text) AS run_id, CAST(r.recipe_id AS text) AS recipe_id, "
    "r.outcome::text AS outcome, r.finished_at, i.verdict::text AS verdict "
    "FROM s4_execution_runs r "
    "LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
    "WHERE r.claim_test_id = CAST(:tid AS uuid) "
    "ORDER BY r.finished_at DESC LIMIT :limit")


def _read_claim_runs(session, test_id, *, limit: int = 50) -> list[dict]:
    """Pure: the claim's runs newest-first — S4 outcome + finished_at LEFT JOINed
    to the S6 verdict (verdict NULL when the best-effort interpret step failed)."""
    rows = session.execute(
        text(_CLAIM_RUNS_SQL), {"tid": str(test_id), "limit": limit}).mappings().all()
    return [{
        "run_id": r["run_id"], "recipe_id": r["recipe_id"],
        "outcome": r["outcome"], "verdict": r["verdict"],
        "finished_at": _iso(r["finished_at"]),
    } for r in rows]


def read_claim_runs(tenant_id: int, test_id) -> dict:
    """Best-effort read of the claim's recent run results. Never raises. Returns
    ``{available, runs}`` — ``available=False`` on any read error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                return {"available": True, "runs": _read_claim_runs(session, test_id)}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_claim_runs unavailable for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"available": False, "runs": []}


# --- read one run's detail (S4 evidence steps + S6 verdict/cause) (3c) --------

_RUN_DETAIL_SQL = (
    "SELECT CAST(run_id AS text) AS run_id, CAST(claim_test_id AS text) AS claim_test_id, "
    "CAST(recipe_id AS text) AS recipe_id, recipe_version_seq, environment_id, "
    "outcome::text AS outcome, started_at, finished_at, duration_ms, evidence "
    "FROM s4_execution_runs WHERE run_id = CAST(:rid AS uuid)")


def _read_run_detail(session, run_id) -> dict | None:
    """Pure: one S4 run row (the evidence trace) joined to its S6 interpretation
    (verdict / attribution / cause), or None when no run matches ``run_id``."""
    row = session.execute(
        text(_RUN_DETAIL_SQL), {"rid": str(run_id)}).mappings().first()
    if row is None:
        return None
    ev = row["evidence"] if isinstance(row["evidence"], dict) else {}
    interp = None
    try:
        from primeqa.interpretation.result_store import read_interpretation
        ir = read_interpretation(session, UUID(str(run_id)))
        if ir is not None:
            interp = {
                "verdict": ir.verdict,
                "attribution": ir.attribution,
                "cause": ({"cause_kind": ir.cause.cause_kind, "vr_name": ir.cause.vr_name}
                          if ir.cause is not None else None),
                "phrasing": ir.phrasing,
            }
    except Exception:                                  # S6 read is best-effort
        interp = None
    return {
        "run_id": row["run_id"], "claim_test_id": row["claim_test_id"],
        "recipe_id": row["recipe_id"], "recipe_version_seq": row["recipe_version_seq"],
        "environment_id": row["environment_id"], "outcome": row["outcome"],
        "started_at": _iso(row["started_at"]), "finished_at": _iso(row["finished_at"]),
        "duration_ms": row["duration_ms"],
        "api_choice": ev.get("api_choice"),
        "steps": ev.get("steps") or [],
        "error": ev.get("error"),
        "interpretation": interp,
    }


def read_run_detail(tenant_id: int, run_id) -> dict:
    """Best-effort read of one run's detail. Never raises. Returns
    ``{available, found, run}`` — ``found=False`` when no run matches;
    ``available=False`` on any read error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                d = _read_run_detail(session, run_id)
                return {"available": True, "found": d is not None, "run": d}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_run_detail unavailable for tenant %s run %s: %s",
                    tenant_id, run_id, exc)
        return {"available": False, "found": False, "run": None}


# --- list all runs (the global runs index) (3b) ------------------------------

_LIST_RUNS_SQL = (
    "SELECT CAST(r.run_id AS text) AS run_id, CAST(r.claim_test_id AS text) AS claim_test_id, "
    "r.outcome::text AS outcome, r.finished_at, r.duration_ms, r.environment_id, "
    "i.verdict::text AS verdict "
    "FROM s4_execution_runs r LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
    "ORDER BY r.finished_at DESC LIMIT :limit OFFSET :offset")


def _list_runs(conn, *, limit: int, offset: int):
    """Pure: (total, page-rows) of all runs newest-first on an open tenant conn —
    S4 outcome/timing LEFT JOINed to the S6 verdict (verdict NULL when absent)."""
    total = conn.execute(text("SELECT COUNT(*) FROM s4_execution_runs")).scalar() or 0
    rows = conn.execute(
        text(_LIST_RUNS_SQL), {"limit": limit, "offset": offset}).mappings().all()
    runs = [{"run_id": r["run_id"], "claim_test_id": r["claim_test_id"],
             "outcome": r["outcome"], "verdict": r["verdict"],
             "finished_at": _iso(r["finished_at"]), "duration_ms": r["duration_ms"],
             "environment_id": r["environment_id"]} for r in rows]
    return total, runs


def list_runs(tenant_id: int, *, page: int = 1, per_page: int = 20) -> dict:
    """Best-effort paginated read of the tenant's S4 runs (newest-first). Never
    raises. ``per_page`` capped at 50. Returns
    ``{available, runs, total, page, per_page, total_pages}``."""
    page = max(1, page)
    per_page = max(1, min(per_page, 50))
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            total, runs = _list_runs(conn, limit=per_page, offset=(page - 1) * per_page)
        total_pages = max(1, (total + per_page - 1) // per_page)
        return {"available": True, "runs": runs, "total": total,
                "page": page, "per_page": per_page, "total_pages": total_pages}
    except Exception as exc:
        log.warning("list_runs unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "runs": [], "total": 0,
                "page": page, "per_page": per_page, "total_pages": 1}


# --- approve a claim (the run-enabler) ---------------------------------------
# S3 generates claims as `draft` and recipes as `generated_unapproved`, neither
# of which `select_recipe_for_execution` will run (it needs an APPROVED claim +
# an active/approved recipe). This is the minimal human-approval step that makes
# a claim runnable — the generate→approve→run loop. (A richer review UI is the
# Area-2 deferred "reviews need a semantic rethink" bucket; this is the seam.)

def _approve_claim(session, test_id) -> dict:
    """Pure: promote the current claim version to ``approved`` and its
    unapproved current recipes to ``approved`` on an open session (humans-only
    per D-ε-1). Idempotent — an already-approved claim/recipe is a no-op."""
    from primeqa.test_representation import SemanticTransactionCoordinator
    coord = SemanticTransactionCoordinator()
    tid = UUID(str(test_id))
    claim = coord.get_latest_claim(session, tid)
    if claim is None:
        return {"ok": False, "error": "No current claim for this id."}
    coord.promote_claim_to_approved(
        session, actor="human", test_id=tid, version_seq=claim.version_seq)
    promoted = 0
    for r in coord.list_active_recipes(session, tid):
        if r.status not in ("active", "approved"):
            coord.promote_recipe_to_approved(
                session, actor="human", recipe_id=r.recipe_id, version_seq=r.version_seq)
            promoted += 1
    return {"ok": True, "status": "approved", "recipes_approved": promoted}


def approve_claim(tenant_id: int, test_id) -> dict:
    """Best-effort: approve the claim + its recipes so it becomes runnable. Never
    raises. ``get_tenant_connection`` commits on clean exit (the promotes are one
    atomic transaction). Returns ``{ok, status, recipes_approved}`` or
    ``{ok: False, error}``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                out = _approve_claim(session, test_id)
                session.flush()
                return out
            finally:
                session.close()
    except Exception as exc:
        log.warning("approve_claim failed for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"ok": False, "error": str(exc)}

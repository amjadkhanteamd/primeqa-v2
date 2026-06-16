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

from primeqa.execution_engine.errors import UnexecutableClaimError

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
                      client=None, field_overrides=None, caller_tier=None) -> dict:
    """Run the eligible recipe for ``test_id`` on ``environment_id`` (synchronous).
    Best-effort — returns ``{ok: False, error}`` on any failure (never raises).
    On success: ``{ok: True, ran, outcome, verdict, recipe_id}`` (``ran=False`` +
    ``reason`` when the claim has no approved/eligible recipe for the env — a
    first-class non-error result). ``client`` is injectable for tests; the route
    passes ``None`` so the engine self-resolves the SF client per recipe-kind.

    ``field_overrides`` (D-235, run-time test-data injection) is an optional
    ``{bare_field_name: value}`` map passed straight to the engine; it applies only
    to the positive vertical's subject create (the executor enforces this)."""
    try:
        from primeqa.execution_engine.run import run_recipe_execution_for_tenant
        result = run_recipe_execution_for_tenant(
            tenant_id, UUID(str(test_id)), environment_id=environment_id,
            client=client, field_overrides=field_overrides or None,
            caller_tier=caller_tier)
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
            # D-201: the substrate agent's deterministic, human-gated repair
            # suggestions over the S6 vocabulary (empty for passing verdicts).
            from primeqa.evolution.repair import suggest_repairs
            interp = {
                "verdict": ir.verdict,
                "attribution": ir.attribution,
                "cause": ({"cause_kind": ir.cause.cause_kind, "vr_name": ir.cause.vr_name}
                          if ir.cause is not None else None),
                "phrasing": ir.phrasing,
                "repair_suggestions": suggest_repairs(
                    ir.verdict,
                    cause_kind=(ir.cause.cause_kind if ir.cause else None),
                    vr_name=(ir.cause.vr_name if ir.cause else None)),
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

# D-231: the runs index is filterable (the failures front door). The page query
# and the COUNT share ONE FROM + WHERE so pagination totals match the filtered set.
# The LEFT JOIN to s6_interpretations is 1:0-or-1 (one S6 reading per run), so the
# COUNT cardinality is unaffected whether or not a verdict filter is applied.
_RUNS_FROM = ("FROM s4_execution_runs r "
              "LEFT JOIN s6_interpretations i ON i.run_id = r.run_id")

# The run_outcome enum surface — the caller validates against this before passing
# an outcome filter (a bad value would just match nothing, but validating keeps the
# chips honest).
_RUN_OUTCOMES = frozenset({"passed", "failed", "errored", "skipped"})


def _runs_where(outcome, verdict, environment_id, since):
    """Build the shared WHERE fragment + bind params from the non-None filters
    (D-231). Only a supplied filter contributes a clause; an empty filter set
    yields no WHERE (the original newest-first-all behavior)."""
    clauses, params = [], {}
    if outcome:
        clauses.append("r.outcome::text = :outcome")
        params["outcome"] = outcome
    if verdict:
        clauses.append("i.verdict::text = :verdict")
        params["verdict"] = verdict
    if environment_id is not None:
        clauses.append("r.environment_id = :env")
        params["env"] = environment_id
    if since is not None:
        clauses.append("r.finished_at >= :since")
        params["since"] = since
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _list_runs(conn, *, limit: int, offset: int, outcome=None, verdict=None,
               environment_id=None, since=None):
    """Pure: (total, page-rows) of runs newest-first on an open tenant conn — S4
    outcome/timing LEFT JOINed to the S6 verdict (verdict NULL when absent),
    filtered by the optional (outcome / verdict / environment_id / since) facets
    (D-231). The COUNT uses the SAME FROM+WHERE so the total reflects the filter."""
    where, params = _runs_where(outcome, verdict, environment_id, since)
    total = conn.execute(
        text(f"SELECT COUNT(*) {_RUNS_FROM}{where}"), params).scalar() or 0
    sql = (
        "SELECT CAST(r.run_id AS text) AS run_id, "
        "CAST(r.claim_test_id AS text) AS claim_test_id, "
        "r.outcome::text AS outcome, r.finished_at, r.duration_ms, r.environment_id, "
        "i.verdict::text AS verdict "
        f"{_RUNS_FROM}{where} "
        "ORDER BY r.finished_at DESC LIMIT :limit OFFSET :offset")
    rows = conn.execute(
        text(sql), {**params, "limit": limit, "offset": offset}).mappings().all()
    runs = [{"run_id": r["run_id"], "claim_test_id": r["claim_test_id"],
             "outcome": r["outcome"], "verdict": r["verdict"],
             "finished_at": _iso(r["finished_at"]), "duration_ms": r["duration_ms"],
             "environment_id": r["environment_id"]} for r in rows]
    return total, runs


def list_runs(tenant_id: int, *, page: int = 1, per_page: int = 20,
              outcome=None, verdict=None, environment_id=None, since=None) -> dict:
    """Best-effort paginated read of the tenant's S4 runs (newest-first), with
    optional triage filters (D-231: outcome / verdict / environment_id / since —
    the failures front door). Never raises. ``per_page`` capped at 50. Returns
    ``{available, runs, total, page, per_page, total_pages, filters}`` (``filters``
    echoes the applied facets so the surface can render active state)."""
    page = max(1, page)
    per_page = max(1, min(per_page, 50))
    filters = {"outcome": outcome, "verdict": verdict,
               "environment_id": environment_id, "since": since}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            total, runs = _list_runs(
                conn, limit=per_page, offset=(page - 1) * per_page,
                outcome=outcome, verdict=verdict,
                environment_id=environment_id, since=since)
        total_pages = max(1, (total + per_page - 1) // per_page)
        return {"available": True, "runs": runs, "total": total,
                "page": page, "per_page": per_page, "total_pages": total_pages,
                "filters": filters}
    except Exception as exc:
        log.warning("list_runs unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "runs": [], "total": 0,
                "page": page, "per_page": per_page, "total_pages": 1,
                "filters": filters}


# --- approve a claim (the run-enabler) ---------------------------------------
# S3 generates claims as `draft` and recipes as `generated_unapproved`, neither
# of which `select_recipe_for_execution` will run (it needs an APPROVED claim +
# an active/approved recipe). This is the minimal human-approval step that makes
# a claim runnable — the generate→approve→run loop. (A richer review UI is the
# Area-2 deferred "reviews need a semantic rethink" bucket; this is the seam.)

def _approve_claim(session, test_id) -> dict:
    """Pure: promote the current claim version to ``approved`` and its
    unapproved current recipes to ``approved`` on an open session (humans-only
    per D-ε-1). Idempotent — an already-approved claim/recipe is a no-op.

    D-226: a DEPRECATED claim is refused — deprecation required an explicit
    human reason (D-ε-5); the generic Approve button must not silently undo it
    (and auto-enqueue runs of a superseded recipe). Reinstatement stays a
    deliberate coordinator capability (``promote_claim_to_approved`` keeps the
    documented deprecated→approved transition for explicit callers). The
    recipe loop likewise never resurrects a deprecated recipe."""
    from primeqa.test_representation import SemanticTransactionCoordinator
    coord = SemanticTransactionCoordinator()
    tid = UUID(str(test_id))
    claim = coord.get_latest_claim(session, tid)
    if claim is None:
        return {"ok": False, "error": "No current claim for this id."}
    if claim.status == "deprecated":
        return {"ok": False,
                "error": ("This claim is deprecated (a recorded human "
                          "decision). It cannot be re-approved from here — "
                          "regenerate the requirement for a fresh claim, or "
                          "reinstate it deliberately via the coordinator.")}
    coord.promote_claim_to_approved(
        session, actor="human", test_id=tid, version_seq=claim.version_seq)
    promoted = 0
    for r in coord.list_active_recipes(session, tid):
        if r.status == "deprecated":
            continue                    # D-226: never silently un-deprecate
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
            finally:
                session.close()
        # D-199 trigger 1: the approval transaction has committed (the context
        # exit above) — best-effort auto-enqueue on the auto-verify envs. An
        # enqueue failure never un-approves.
        if out.get("ok"):
            auto = auto_enqueue_on_approval(tenant_id, test_id)
            out["auto_enqueued"] = len(auto["enqueued"])
        return out
    except Exception as exc:
        log.warning("approve_claim failed for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"ok": False, "error": str(exc)}


def _deprecate_claim(session, test_id, reason: str) -> dict:
    """Pure: deprecate the current claim version with a REQUIRED human reason
    (D-228 / F3 — supersession is a human judgment, never auto-derived). The
    reason lands in provenance per D-ε-5. Claim-only: deprecating the claim
    makes every recipe unselectable (selection requires a current APPROVED
    claim), so the recipes keep their own status untouched. Idempotent — an
    already-deprecated claim is a no-op."""
    from primeqa.test_representation import SemanticTransactionCoordinator
    if not isinstance(reason, str) or not reason.strip():
        return {"ok": False, "error": "A reason is required to deprecate."}
    coord = SemanticTransactionCoordinator()
    tid = UUID(str(test_id))
    claim = coord.get_latest_claim(session, tid)
    if claim is None:
        return {"ok": False, "error": "No current claim for this id."}
    if claim.status == "deprecated":
        return {"ok": True, "status": "deprecated", "already": True}
    coord.deprecate_claim(session, actor="human", test_id=tid,
                          version_seq=claim.version_seq, reason=reason.strip())
    return {"ok": True, "status": "deprecated", "already": False}


def deprecate_claim(tenant_id: int, test_id, reason: str) -> dict:
    """Best-effort: deprecate the claim (with reason) so it stops grading
    releases and stops being selectable for runs. Never raises;
    ``get_tenant_connection`` commits on clean exit."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                out = _deprecate_claim(session, test_id, reason)
                session.flush()
            finally:
                session.close()
        return out
    except Exception as exc:
        log.warning("deprecate_claim failed for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"ok": False, "error": str(exc)}


# --- D-199 trigger 1: auto-enqueue on approval --------------------------------

def auto_verify_environment_ids(db, tenant_id: int) -> list:
    """The D-199 env-selection policy: every ACTIVE, NON-PRODUCTION environment
    with a Salesforce connection — "verify everywhere it is safe." Production is
    structurally excluded (prod runs keep the human confirm_production path)."""
    from primeqa.core.models import Environment
    rows = (db.query(Environment.id)
            .filter(Environment.tenant_id == tenant_id,
                    Environment.is_active.is_(True),
                    Environment.is_production.is_(False),
                    Environment.connection_id.isnot(None))
            .all())
    return [r[0] for r in rows]


def auto_enqueue_on_approval(tenant_id: int, test_id, *, created_by=None) -> dict:
    """D-199 trigger 1: after a claim is approved, queue its execution on every
    auto-verify environment. Best-effort — never raises, never blocks the
    approval. Idempotent per env (the queue's active-set dedup). Returns
    ``{enqueued: [...job ids...], environments: [...ids...]}``."""
    try:
        from primeqa.db import get_db
        from primeqa.execution_engine.intake import enqueue_s4_execution
        db = next(get_db())
        try:
            env_ids = auto_verify_environment_ids(db, tenant_id)
        finally:
            db.close()
        jobs = []
        unexecutable = None
        for eid in env_ids:
            try:
                job = enqueue_s4_execution(
                    tenant_id=tenant_id, test_id=test_id,
                    environment_id=eid, created_by=created_by)
                jobs.append(job.id)
            except UnexecutableClaimError as exc:
                # D-223: a shape refusal is env-independent — record once,
                # stop trying (the caller surfaces it; approval stands).
                log.info("auto-enqueue gated (unexecutable) tenant %s test %s: %s",
                         tenant_id, test_id, exc)
                unexecutable = str(exc)
                break
            except Exception as exc:                       # one env never blocks the rest
                log.warning("auto-enqueue failed for tenant %s test %s env %s: %s",
                            tenant_id, test_id, eid, exc)
        out = {"enqueued": jobs, "environments": env_ids}
        if unexecutable is not None:
            out["unexecutable"] = unexecutable
        return out
    except Exception as exc:
        log.warning("auto-enqueue skipped for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"enqueued": [], "environments": []}


# --- D-199 trigger 3: bulk-enqueue a release's claims (the CI gate re-verify) --

def enqueue_claims_for_keys(tenant_id: int, external_keys, environment_id: int,
                            *, created_by=None) -> dict:
    """Enqueue every ``generated_from`` claim behind ``external_keys`` for
    execution on ``environment_id`` (deduped; best-effort per claim). The CI
    webhook's re-verify path — CI then polls /status for the D-198 substrate
    verdict over FRESH evidence. Never raises."""
    keys = [k for k in (external_keys or []) if k]
    if not keys:
        return {"enqueued": [], "claim_count": 0}
    try:
        from sqlalchemy.orm import Session

        from primeqa.execution_engine.intake import enqueue_s4_execution
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.test_representation.coordinator import (
            SemanticTransactionCoordinator,
        )
        coord = SemanticTransactionCoordinator()
        test_ids, seen = [], set()
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                for key in keys:
                    for m in coord.list_tests_by_requirement(
                            session, external_system="jira", external_key=key,
                            link_kind="generated_from"):
                        sid = str(m.test_id)
                        if sid not in seen:
                            seen.add(sid)
                            test_ids.append(m.test_id)
            finally:
                session.close()
        jobs = []
        skipped_unexecutable = 0
        for tid in test_ids:
            try:
                job = enqueue_s4_execution(
                    tenant_id=tenant_id, test_id=tid,
                    environment_id=environment_id, created_by=created_by)
                jobs.append(job.id)
            except UnexecutableClaimError as exc:
                # D-223: shape refusal — skip this claim, never the batch.
                log.info("release-claim enqueue gated tenant %s test %s: %s",
                         tenant_id, tid, exc)
                skipped_unexecutable += 1
            except Exception as exc:
                log.warning("release-claim enqueue failed tenant %s test %s: %s",
                            tenant_id, tid, exc)
        return {"enqueued": jobs, "claim_count": len(test_ids),
                "skipped_unexecutable": skipped_unexecutable}
    except Exception as exc:
        log.warning("enqueue_claims_for_keys failed for tenant %s: %s",
                    tenant_id, exc)
        return {"enqueued": [], "claim_count": 0}


# --- D-214 trigger: enqueue EVERY approved claim (the scheduled regression) ---

def enqueue_all_approved_claims(tenant_id: int, environment_id: int,
                                *, created_by=None) -> dict:
    """Enqueue every currently-APPROVED claim for execution on
    ``environment_id`` — the scheduled-regression trigger's body (D-214).
    Deduped by the job store's active-set semantics; best-effort per claim.
    Never raises."""
    try:
        from primeqa.execution_engine.intake import enqueue_s4_execution
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            test_ids = [r[0] for r in conn.execute(text(
                "SELECT test_id FROM test_claims "
                "WHERE status = 'approved' AND valid_to IS NULL "
                "ORDER BY test_id")).fetchall()]
        jobs = []
        skipped_unexecutable = 0
        for tid in test_ids:
            try:
                job = enqueue_s4_execution(
                    tenant_id=tenant_id, test_id=tid,
                    environment_id=environment_id, created_by=created_by)
                jobs.append(job.id)
            except UnexecutableClaimError as exc:
                # D-223: shape refusal — skip this claim, never the batch.
                log.info("scheduled enqueue gated tenant %s test %s: %s",
                         tenant_id, tid, exc)
                skipped_unexecutable += 1
            except Exception as exc:
                log.warning("scheduled enqueue failed tenant %s test %s: %s",
                            tenant_id, tid, exc)
        return {"enqueued": jobs, "claim_count": len(test_ids),
                "skipped_unexecutable": skipped_unexecutable}
    except Exception as exc:
        log.warning("enqueue_all_approved_claims failed for tenant %s: %s",
                    tenant_id, exc)
        return {"enqueued": [], "claim_count": 0}

# --- D-219 slice 3: the substrate run page's requirement list --------------

def list_runnable_requirements(tenant_id: int) -> dict:
    """Best-effort: every requirement external key with >=1 APPROVED claim,
    with the approved-claim count (the /run page's picker rows). Never
    raises. Returns ``{available, rows: [{key, approved_claims}]}``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT l.external_key AS key, "
                "       COUNT(DISTINCT l.test_id) AS approved_claims "
                "FROM test_requirement_links l "
                "JOIN test_claims c ON c.test_id = l.test_id "
                "  AND c.valid_to IS NULL AND c.status = 'approved' "
                "WHERE l.link_kind = 'generated_from' "
                "GROUP BY l.external_key ORDER BY l.external_key"
            )).mappings().all()
            return {"available": True,
                    "rows": [dict(r) for r in rows]}
    except Exception as exc:
        log.warning("list_runnable_requirements unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "rows": []}

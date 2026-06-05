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

log = logging.getLogger(__name__)


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

def _read_claim_runs(session, test_id) -> list[dict]:
    """Pure: the claim's runs (outcome + verdict per run) via the S6 read API.
    A run whose best-effort interpret step failed has no S6 row and won't appear."""
    from primeqa.interpretation.result_store import list_interpretations
    rows = list_interpretations(session, claim_test_id=UUID(str(test_id)))
    return [{
        "run_id": str(r.run_id),
        "recipe_id": str(r.recipe_id),
        "outcome": r.outcome,
        "verdict": r.verdict,
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

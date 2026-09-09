"""Run-plan console (Step 4, LLD_STEP_4_RUN_PLANNER §d): the best-effort read
bridge the PLAN view renders from and the acts the entry points call —
each act one tenant transaction (the connection helper commits on clean
exit, rolls back on any exception); never raises into a render."""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)


def _with_session(tenant_id: int, session, fn):
    if session is not None:
        return fn(session)
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        s = Session(bind=conn)
        try:
            out = fn(s)
            s.flush()
            return out
        finally:
            s.close()


def _user_names(session, tenant_id: int, ids: set) -> dict:
    ids = {int(i) for i in ids if i is not None}
    if not ids:
        return {}
    rows = session.execute(text(
        "SELECT id, full_name, email FROM public.users WHERE tenant_id = :t AND id = ANY(:ids)"),
        {"t": tenant_id, "ids": list(ids)}).fetchall()
    return {r[0]: (r[1] or r[2] or f"user {r[0]}") for r in rows}


def read_plan(tenant_id: int, plan_id: str, *, session=None) -> dict:
    """``{available, plan}`` — the recorded row, decorated with names and the
    reason sentences; never recomputed."""
    try:
        from primeqa.execution_engine import planner

        def _read(s):
            row = planner.get_plan(s, plan_id)
            if row is None:
                return {"available": True, "plan": None}
            names = _user_names(s, tenant_id, {row["planned_by"], row["authorised_by"], row["executed_by"]})
            row["planned_by_name"] = names.get(row["planned_by"]) if row["planned_by"] else None
            row["authorised_by_name"] = names.get(row["authorised_by"]) if row["authorised_by"] else None
            row["executed_by_name"] = names.get(row["executed_by"]) if row["executed_by"] else None
            counts = (row["exclusions"] or {}).get("counts") or {}
            row["exclusion_lines"] = [{"reason": k, "count": v, "sentence": planner.REASON_SENTENCES.get(k, k)}
                                      for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
            row["excluded_total"] = sum(counts.values())
            return {"available": True, "plan": row}
        return _with_session(tenant_id, session, _read)
    except Exception as exc:  # noqa: BLE001 — render boundary
        log.warning("read_plan unavailable for tenant %s plan %s: %s", tenant_id, plan_id, exc)
        return {"available": False, "plan": None}


def plans_for_scope(tenant_id: int, scope_kind: str, scope_ref: str, *, limit=5, session=None) -> dict:
    try:
        from primeqa.execution_engine import planner

        def _read(s):
            return {"available": True,
                    "plans": planner.list_plans(s, scope_kind=scope_kind, scope_ref=str(scope_ref), limit=limit)}
        return _with_session(tenant_id, session, _read)
    except Exception as exc:  # noqa: BLE001
        log.warning("plans_for_scope unavailable for tenant %s %s/%s: %s", tenant_id, scope_kind, scope_ref, exc)
        return {"available": False, "plans": []}


def targets_for_release(tenant_id: int, release_id: int, *, session=None) -> dict:
    try:
        from primeqa.execution_engine import planner

        def _read(s):
            rows = planner.list_targets(s, release_id)
            names = _user_names(s, tenant_id, {r["declared_by"] for r in rows})
            for r in rows:
                r["declared_by_name"] = names.get(r["declared_by"], f"user {r['declared_by']}")
            return {"available": True, "targets": rows}
        return _with_session(tenant_id, session, _read)
    except Exception as exc:  # noqa: BLE001
        log.warning("targets_for_release unavailable for tenant %s release %s: %s", tenant_id, release_id, exc)
        return {"available": False, "targets": []}


def create_plan(tenant_id: int, *, scope: dict, user_id: Optional[int], user_role: Optional[str] = None,
                session=None) -> dict:
    """Record a plan for ``scope`` as ``user_id``. ``{ok, plan_id, plan}`` or
    ``{ok: False, reason, sentence}``."""
    from primeqa.execution_engine import planner
    try:
        from primeqa.core.authz import rank

        def _act(s):
            tier = rank(user_role) if user_role else None
            row = planner.plan(s, tenant_id=tenant_id, scope=scope, planned_by=user_id, planner_tier=tier)
            return {"ok": True, "plan_id": row["id"], "plan": row}
        return _with_session(tenant_id, session, _act)
    except planner.PlanRefused as exc:
        return {"ok": False, "reason": exc.reason, "sentence": planner.REFUSAL_SENTENCES.get(exc.reason, str(exc)),
                "detail": exc.detail}
    except Exception as exc:  # noqa: BLE001 — route boundary
        log.warning("create_plan failed for tenant %s scope %s: %s", tenant_id, scope, exc)
        return {"ok": False, "reason": "error", "sentence": "Could not plan right now.", "detail": str(exc)[:200]}


def run_plan(tenant_id: int, *, plan_id: str, user_id: int, user_role: str, session=None) -> dict:
    """"Run this plan": execute the recorded row, once. ``{ok, receipt}`` or
    ``{ok: False, reason, sentence}``."""
    from primeqa.execution_engine import planner
    try:
        subject = {"user_id": user_id, "tenant_id": tenant_id, "role": user_role}

        def _act(s):
            receipt = planner.execute_plan(s, tenant_id=tenant_id, plan_id=plan_id,
                                           executed_by=user_id, subject=subject)
            return {"ok": True, "receipt": receipt}
        return _with_session(tenant_id, session, _act)
    except planner.PlanRefused as exc:
        return {"ok": False, "reason": exc.reason, "sentence": planner.REFUSAL_SENTENCES.get(exc.reason, str(exc))}
    except Exception as exc:  # noqa: BLE001
        log.warning("run_plan failed for tenant %s plan %s: %s", tenant_id, plan_id, exc)
        return {"ok": False, "reason": "error", "sentence": "Could not run the plan right now.", "detail": str(exc)[:200]}


def declare_target(tenant_id: int, *, release_id: int, environment_id: int, user_id: int, session=None) -> dict:
    from primeqa.execution_engine import planner
    try:
        return _with_session(tenant_id, session, lambda s: {"ok": True, **planner.declare_target(
            s, tenant_id=tenant_id, release_id=release_id, environment_id=environment_id, actor_user_id=user_id)})
    except Exception as exc:  # noqa: BLE001
        log.warning("declare_target failed: %s", exc)
        return {"ok": False, "sentence": "Could not declare the target right now."}


def remove_target(tenant_id: int, *, release_id: int, environment_id: int, user_id: int, reason: str = "",
                  session=None) -> dict:
    from primeqa.execution_engine import planner
    try:
        return _with_session(tenant_id, session, lambda s: {"ok": True, **planner.remove_target(
            s, tenant_id=tenant_id, release_id=release_id, environment_id=environment_id,
            actor_user_id=user_id, reason=reason)})
    except Exception as exc:  # noqa: BLE001
        log.warning("remove_target failed: %s", exc)
        return {"ok": False, "sentence": "Could not remove the target right now."}

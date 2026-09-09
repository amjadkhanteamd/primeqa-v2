"""Requirement → surface console (Step 3, LLD_STEP_3_REQUIREMENT_SURFACE §c).

The best-effort read bridge the requirement page's "Conformance surfaces"
card and its picker render from, and the two acts (declare / unlink) the
page's routes call — the s3-console pattern: ``{available: bool}``, never
raises into a render; every act is one tenant transaction (the connection
helper commits on clean exit, rolls back on any exception).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _user_names(session, tenant_id: int, ids: set) -> dict:
    if not ids:
        return {}
    rows = session.execute(text(
        "SELECT id, full_name, email FROM public.users "
        "WHERE tenant_id = :t AND id = ANY(:ids)"),
        {"t": tenant_id, "ids": [int(i) for i in ids]}).fetchall()
    return {r[0]: (r[1] or r[2] or f"user {r[0]}") for r in rows}


def _verdict_split(session, *, claim_set_id: Optional[str],
                   surface_keys: list[str]) -> tuple[Optional[dict], dict]:
    """The latest processing run over ``claim_set_id`` and the per-surface
    PASS / FAIL / NOT_DETERMINED counts on it. ``(run, {surface_key: split})``;
    ``(None, {})`` when the set has never been processed."""
    if claim_set_id is None or not surface_keys:
        return None, {}
    run = session.execute(text("""
        SELECT CAST(job_id AS text), processed_at
        FROM s6_ui_processing_runs
        WHERE claim_set_id = CAST(:c AS uuid)
        ORDER BY processed_at DESC LIMIT 1
    """), {"c": claim_set_id}).first()
    if run is None:
        return None, {}
    rows = session.execute(text("""
        SELECT surface_key,
               COUNT(*) FILTER (WHERE verdict = 'PASS'),
               COUNT(*) FILTER (WHERE verdict = 'FAIL'),
               COUNT(*) FILTER (WHERE verdict NOT IN ('PASS', 'FAIL'))
        FROM s6_ui_verdicts
        WHERE job_id = CAST(:j AS uuid) AND surface_key = ANY(:keys)
        GROUP BY surface_key
    """), {"j": run[0], "keys": surface_keys}).fetchall()
    split = {r[0]: {"pass": int(r[1]), "fail": int(r[2]),
                    "not_determined": int(r[3])} for r in rows}
    return {"job_id": run[0], "processed_at": _iso(run[1])}, split


def _with_session(tenant_id: int, session, fn):
    """Run ``fn(session)`` on the caller's session when one is given (tests,
    composed acts — the caller owns the transaction), else on a fresh tenant
    transaction that commits on clean exit."""
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


def read_requirement_surfaces(tenant_id: int, requirement_key: str, *,
                              session=None) -> dict:
    """``{available, active_inventory_version, active_claim_set_id, latest_run,
    declared: [...], counts: {surfaces, checks, failures, human_review}}``.
    ``available=False`` on any read error (the tables not yet migrated, a
    connection failure) — the card then says so instead of 500ing."""
    try:
        from primeqa.test_representation import surface_links as sl

        def _read(session):
            v = sl.active_inventory_version(session)
            cs = sl.active_claim_set(session, v) if v is not None else None
            declared = sl.declared_surfaces(session, requirement_key=requirement_key)
            names = _user_names(session, tenant_id, {d.declared_by for d in declared})
            latest_run, split = _verdict_split(
                session, claim_set_id=cs,
                surface_keys=[d.surface_key for d in declared
                              if d.inventory_version == v])
            rows, counts = [], {"surfaces": 0, "checks": 0, "failures": 0,
                                "human_review": 0}
            for d in declared:
                on_active = d.inventory_version == v
                claims = (sl.surface_claims(session, claim_set_id=cs,
                                            surface_key=d.surface_key)
                          if (on_active and cs) else [])
                human = sum(1 for c in claims if c.applicability == "HUMAN_REVIEW")
                sp = split.get(d.surface_key)
                rows.append({
                    "link_id": d.link_id, "surface_key": d.surface_key,
                    "display_name": d.display_name or d.path,
                    "path": d.path, "site": d.site,
                    "persona_scope": d.persona_scope,
                    "inventory_version": d.inventory_version,
                    "is_active_inventory": on_active,
                    "source": d.source,
                    "declared_by": d.declared_by,
                    "declared_by_name": names.get(d.declared_by, f"user {d.declared_by}"),
                    "declared_at": _iso(d.declared_at),
                    "checks": len(claims), "human_review": human,
                    "split": sp, "no_run": sp is None,
                })
                if on_active:
                    counts["surfaces"] += 1
                    counts["checks"] += len(claims)
                    counts["human_review"] += human
                    counts["failures"] += (sp or {}).get("fail", 0)
            return {"available": True, "active_inventory_version": v,
                    "active_claim_set_id": cs, "latest_run": latest_run,
                    "declared": rows, "counts": counts}
        return _with_session(tenant_id, session, _read)
    except Exception as exc:  # noqa: BLE001 — render boundary
        log.warning("read_requirement_surfaces unavailable for tenant %s key %s: %s",
                    tenant_id, requirement_key, exc)
        return {"available": False, "declared": [], "counts": None,
                "active_inventory_version": None, "active_claim_set_id": None,
                "latest_run": None}


def picker_candidates(tenant_id: int, requirement_key: str, *, session=None) -> dict:
    """``{available, active_inventory_version, candidates: [...]}`` — the
    ACTIVE inventory's members minus declared minus record-context."""
    try:
        from primeqa.test_representation import surface_links as sl

        def _read(session):
            v, cands = sl.picker_candidates(session, requirement_key=requirement_key)
            return {"available": True, "active_inventory_version": v,
                    "candidates": [c.__dict__ for c in cands]}
        return _with_session(tenant_id, session, _read)
    except Exception as exc:  # noqa: BLE001
        log.warning("picker_candidates unavailable for tenant %s key %s: %s",
                    tenant_id, requirement_key, exc)
        return {"available": False, "active_inventory_version": None, "candidates": []}


def declare_surface(tenant_id: int, *, requirement_key: str, surface_key: str,
                    user_id: int, session=None) -> dict:
    """One tenant transaction: the declaration row + the S2 links + the
    ledger + the audit row commit together or not at all. Never raises:
    ``{ok, link_id, created, materialised, display_name}`` or
    ``{ok: False, reason, error}``."""
    from primeqa.test_representation import surface_links as sl
    try:
        def _act(session):
            res = sl.declare(session, requirement_key=requirement_key,
                             surface_key=surface_key, actor_user_id=user_id,
                             tenant_id=tenant_id)
            link = sl.get_link(session, res.link_id)
            return {"ok": True, "link_id": res.link_id, "created": res.created,
                    "materialised": res.materialised,
                    "already_linked": res.already_linked,
                    "display_name": (link.display_name or link.path) if link else surface_key}
        return _with_session(tenant_id, session, _act)
    except sl.SurfaceLinkError as exc:
        return {"ok": False, "reason": exc.reason, "error": str(exc),
                "sentence": sl.REASON_SENTENCES.get(exc.reason, str(exc))}
    except Exception as exc:  # noqa: BLE001 — route boundary
        log.warning("declare_surface failed for tenant %s key %s surface %s: %s",
                    tenant_id, requirement_key, surface_key, exc)
        return {"ok": False, "reason": "error", "error": str(exc),
                "sentence": "Could not declare the surface right now."}


def unlink_surface(tenant_id: int, *, link_id: str, requirement_key: str,
                   user_id: int, reason: str, session=None) -> dict:
    """Deactivate one of THIS requirement's declarations (a link of another
    requirement is refused as unknown). Never raises."""
    from primeqa.test_representation import surface_links as sl
    try:
        def _act(session):
            link = sl.get_link(session, link_id)
            if link is None or link.requirement_key != requirement_key:
                return {"ok": False, "reason": "unknown_link",
                        "sentence": "That surface link does not belong to this requirement."}
            res = sl.unlink(session, link_id=link_id, actor_user_id=user_id,
                            reason=reason, tenant_id=tenant_id)
            return {"ok": True, "link_id": res.link_id,
                    "removed_links": res.removed_links,
                    "kept_links": res.kept_links,
                    "already_inactive": res.already_inactive,
                    "display_name": link.display_name or link.path}
        return _with_session(tenant_id, session, _act)
    except Exception as exc:  # noqa: BLE001
        log.warning("unlink_surface failed for tenant %s link %s: %s",
                    tenant_id, link_id, exc)
        return {"ok": False, "reason": "error", "error": str(exc),
                "sentence": "Could not unlink the surface right now."}

"""Step 5 — the quality-decision console (LLD_STEP_5_QUALITY_POLICY §b, §c,
§e): the best-effort read bridge the decision tab renders from (the live
PREVIEW — no row written) and the acts the routes call (waivers), each one
tenant transaction through the ``session=`` seam; never raises into a
render. The RECORDED decision is the composer's
(:func:`primeqa.release.decision_composer.evaluate_and_record`), which calls
:func:`grade_release` here and writes the public row itself.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from primeqa.intelligence import quality_policy as qp
from primeqa.intelligence.quality_evidence import assemble

log = logging.getLogger(__name__)

NO_ACTIVE_POLICY = "no_active_policy"
NO_ACTIVE_POLICY_SENTENCE = ("No active quality policy — activate one (Settings → Quality policy; CLI in v1) "
                             "before a release grades.")


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


def _user_names(session, tenant_id: int, ids) -> dict:
    ids = {int(i) for i in ids if i is not None}
    if not ids:
        return {}
    try:
        rows = session.execute(text(
            "SELECT id, full_name, email FROM public.users WHERE tenant_id = :t AND id = ANY(:ids)"),
            {"t": tenant_id, "ids": list(ids)}).fetchall()
        return {r[0]: (r[1] or r[2] or f"user {r[0]}") for r in rows}
    except Exception:  # noqa: BLE001 — the tenant-only harness has no public.users
        return {}


def grade_release(session, *, tenant_id: int, release_id: Optional[int], keys, now: Optional[datetime] = None,
                  **seams) -> dict:
    """Assemble + evaluate under the ACTIVE policy over one session. Returns
    ``{ok, policy, evidence, decision}`` or ``{ok: False, reason, sentence,
    evidence}`` when no policy is active (ruling 3 — never a silent default)."""
    now = now or datetime.now(timezone.utc)
    policy = qp.active_policy(session)
    evidence = assemble(session, tenant_id=tenant_id, keys=keys, release_id=release_id, now=now, **seams)
    if policy is None:
        return {"ok": False, "reason": NO_ACTIVE_POLICY, "sentence": NO_ACTIVE_POLICY_SENTENCE,
                "policy": None, "evidence": evidence, "decision": None}
    decision = qp.evaluate(policy, evidence, now=now)
    return {"ok": True, "policy": policy.as_dict(), "evidence": evidence, "decision": decision}


def preview_release_decision(tenant_id: int, release_id: int, keys, *, session=None) -> dict:
    """The live PREVIEW for the decision tab — read-only, nothing recorded."""
    try:
        def _read(s):
            out = grade_release(s, tenant_id=tenant_id, release_id=release_id, keys=keys)
            names = _user_names(s, tenant_id, {a.get("reviewer") for a in (out["evidence"].get("waivers") or {}).get("items", [])})
            out["available"] = True
            out["user_names"] = names
            return out
        return _with_session(tenant_id, session, _read)
    except Exception as exc:  # noqa: BLE001
        log.warning("quality decision preview unavailable for tenant %s release %s: %s", tenant_id, release_id, exc)
        return {"available": False, "ok": False, "reason": "unavailable", "sentence": str(exc)[:200],
                "policy": None, "evidence": None, "decision": None}


def waivers_for_release(tenant_id: int, release_id: int, *, session=None) -> dict:
    try:
        def _read(s):
            rows = qp.waivers_for_release(s, release_id)
            names = _user_names(s, tenant_id, {r["reviewer_user_id"] for r in rows} | {r["created_by"] for r in rows})
            for r in rows:
                r["reviewer_name"] = names.get(r["reviewer_user_id"], f"user {r['reviewer_user_id']}")
                r["created_by_name"] = names.get(r["created_by"], f"user {r['created_by']}")
            return {"available": True, "waivers": rows}
        return _with_session(tenant_id, session, _read)
    except Exception as exc:  # noqa: BLE001
        log.warning("waivers unavailable for tenant %s release %s: %s", tenant_id, release_id, exc)
        return {"available": False, "waivers": []}


def record_waiver(tenant_id: int, *, release_id: int, item_kind: str, item_ref: str, axis: str,
                  reviewer_user_id: int, reason: str, expires_at: datetime, user_id: int, session=None) -> dict:
    try:
        return _with_session(tenant_id, session, lambda s: {"ok": True, "waiver": qp.record_waiver(
            s, release_id=release_id, item_kind=item_kind, item_ref=item_ref, axis=axis,
            reviewer_user_id=reviewer_user_id, reason=reason, expires_at=expires_at, created_by=user_id,
            tenant_id=tenant_id)})
    except qp.PolicyError as exc:
        return {"ok": False, "sentence": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("record_waiver failed for tenant %s release %s: %s", tenant_id, release_id, exc)
        return {"ok": False, "sentence": f"Could not record the waiver: {str(exc)[:160]}"}


def revoke_waiver(tenant_id: int, *, waiver_id: str, user_id: int, reason: str = "", session=None) -> dict:
    try:
        return _with_session(tenant_id, session, lambda s: {"ok": True, "waiver": qp.revoke_waiver(
            s, waiver_id=waiver_id, user_id=user_id, reason=reason, tenant_id=tenant_id)})
    except qp.PolicyError as exc:
        return {"ok": False, "sentence": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("revoke_waiver failed for tenant %s: %s", tenant_id, exc)
        return {"ok": False, "sentence": f"Could not revoke the waiver: {str(exc)[:160]}"}


def policy_overview(tenant_id: int, *, session=None) -> dict:
    """The read-only Settings view (§f): the active policy with its rules,
    the other versions, the vocabulary."""
    try:
        def _read(s):
            policies = [p.as_dict() for p in qp.list_policies(s)]
            active = next((p for p in policies if p["status"] == "active"), None)
            names = _user_names(s, tenant_id, {p.get("created_by") for p in policies} | {p.get("activated_by") for p in policies})
            return {"available": True, "active": active, "policies": policies, "user_names": names,
                    "vocabulary": {a: {c: v[1] for c, v in cs.items()} for a, cs in qp.CONDITIONS.items()},
                    "effects": list(qp.EFFECTS)}
        return _with_session(tenant_id, session, _read)
    except Exception as exc:  # noqa: BLE001
        log.warning("policy overview unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "active": None, "policies": [], "user_names": {}, "vocabulary": {}, "effects": []}

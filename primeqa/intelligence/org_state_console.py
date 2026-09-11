"""Step 6a — the org-state band (LLD_STEP_6A_WORK_SURFACES §b).

One read-only reader behind the strip under the nav on Requirements, Results
and Releases: what org this tenant is looking at and how current it is, which
quality policy grades its releases, and what cadence runs its plans.

Three groups, each independently ``available``: a group that cannot be read
renders "unavailable" in place. Nothing here raises into a render, and nothing
here writes. Cached per request in ``flask.g`` so three includes cost one
query path.

**Last sync is read from ``s1_sync_jobs``, not from
``connected_orgs.last_sync_completed_at``** — that column is NULL for both
environment-bound production orgs while syncs complete daily, so reading it
would print "never synced" under a live daily sync (ruling 5; ledgered in the
FIX PLAN as a data-hygiene defect for its own slice).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

_G_KEY = "_plimsol_org_state"


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def read_org_state(tenant_id: int, *, session=None) -> dict:
    """``{orgs: {...}, policy: {...}, cadence: {...}}`` — each group carries its
    own ``available`` flag, its own sentence when it cannot be read, and its
    rows under ``entries``.

    ``entries``, never ``items``: a dict key called "items" is the D-487 hazard
    by construction — Jinja's ``x.items`` resolves to the dict's METHOD, and a
    template that iterates it dies at request time. The name is avoided at the
    source rather than worked around in the template."""
    orgs = {"available": False, "entries": [], "sentence": "unavailable"}
    policy = {"available": False, "sentence": "unavailable"}
    cadence = {"available": False, "entries": [], "sentence": "unavailable"}

    def _read(s):
        _fill_orgs(s, orgs)
        _fill_policy(s, policy)
        _fill_cadence(s, cadence)

    try:
        if session is not None:
            _read(session)
        else:
            from sqlalchemy.orm import Session

            from primeqa.semantic.connection import get_tenant_connection
            with get_tenant_connection(tenant_id) as conn:
                sess = Session(bind=conn)
                try:
                    _read(sess)
                finally:
                    sess.close()
    except Exception as exc:  # noqa: BLE001 — the band never breaks a page
        log.warning("org state unavailable for tenant %s: %s", tenant_id, exc)
    return {"orgs": orgs, "policy": policy, "cadence": cadence}


def _fill_orgs(s, orgs: dict) -> None:
    """Every org bound to an environment: name, the GRADED current sequence
    (the resolver's value, not ``max(version_seq)``), and the newest sync job."""
    try:
        rows = s.execute(text("""
            SELECT CAST(o.id AS text), o.label, o.environment_id, o.org_type,
                   (SELECT j.status FROM s1_sync_jobs j WHERE j.connected_org_id = o.id
                    ORDER BY COALESCE(j.completed_at, j.started_at, j.created_at) DESC LIMIT 1),
                   (SELECT COALESCE(j.completed_at, j.started_at, j.created_at)
                    FROM s1_sync_jobs j WHERE j.connected_org_id = o.id
                    ORDER BY COALESCE(j.completed_at, j.started_at, j.created_at) DESC LIMIT 1)
            FROM connected_orgs o
            WHERE o.environment_id IS NOT NULL
            ORDER BY o.environment_id
        """)).fetchall()
    except Exception as exc:  # noqa: BLE001
        orgs["sentence"] = f"the org list could not be read ({str(exc)[:60]})"
        return
    from primeqa.sync.readiness import SEQ_CURRENT, resolve_current_sequence
    entries = []
    for r in rows:
        seq, seq_state = None, "CANNOT_DETERMINE"
        try:
            with s.begin_nested():
                res = resolve_current_sequence(s, connected_org_id=r[0])
            seq_state = res.state
            seq = res.current_seq if res.state == SEQ_CURRENT else None
        except Exception:  # noqa: BLE001 — one org's sequence, not the band
            pass
        entries.append({
            "connected_org_id": r[0], "name": r[1] or f"org {r[0][:8]}",
            "environment_id": r[2], "org_type": r[3],
            "sequence": seq, "sequence_state": seq_state,
            "last_sync_status": r[4], "last_sync_at": _iso(r[5]),
            "sentence": (f"{r[1]} · seq {seq}" if seq is not None
                         else f"{r[1]} · sequence {seq_state.lower().replace('_', ' ')}"),
        })
    orgs.update(available=True, entries=entries,
                sentence=("no org is bound to an environment" if not entries else ""))


def _fill_policy(s, policy: dict) -> None:
    try:
        from primeqa.intelligence import quality_policy as qp
        p = qp.active_policy(s)
    except Exception as exc:  # noqa: BLE001
        policy["sentence"] = f"the policy could not be read ({str(exc)[:60]})"
        return
    if p is None:
        policy.update(available=True, active=None,
                      sentence="no active quality policy — nothing grades")
        return
    policy.update(available=True,
                  active={"id": p.id, "name": p.name, "version": p.version,
                          "label": p.label, "rules": len(p.rules),
                          "first_used_at": p.first_used_at},
                  sentence=f"{p.label} · {len(p.rules)} rules")


def _fill_cadence(s, cadence: dict) -> None:
    try:
        rows = s.execute(text("""
            SELECT id, environment_id, cron_expr, enabled, last_fired_at,
                   CAST(last_plan_id AS text), last_refusal, authorised_by, created_by
            FROM s4_run_schedules WHERE enabled ORDER BY id
        """)).fetchall()
    except Exception as exc:  # noqa: BLE001
        cadence["sentence"] = f"the cadence could not be read ({str(exc)[:60]})"
        return
    entries = [{
        "id": r[0], "environment_id": r[1], "cron_expr": r[2], "enabled": r[3],
        "last_fired_at": _iso(r[4]), "last_plan_id": r[5], "last_refusal": r[6],
        "authority": (r[7] or r[8]),
        "sentence": (f"{r[2]} on env {r[1]}"
                     + (f" · last plan {r[5][:8]}" if r[5] else "")
                     + (f" · REFUSED: {r[6]}" if r[6] and not r[5] else "")),
    } for r in rows]
    cadence.update(available=True, entries=entries,
                   sentence=("no enabled schedule" if not entries else ""))


def org_state_for_request(tenant_id: int) -> dict:
    """The per-request cache: three includes, one query path."""
    try:
        from flask import g
    except Exception:  # noqa: BLE001 — outside a request context
        return read_org_state(tenant_id)
    cached = getattr(g, _G_KEY, None)
    if cached is None:
        cached = read_org_state(tenant_id)
        setattr(g, _G_KEY, cached)
    return cached

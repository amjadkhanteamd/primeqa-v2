"""Worker-side security-event audit (LLD_PRODUCTIONISATION §d).

Events flow into ``public.activity_log`` through the tenant session
(reachable via search_path — the 3A-3 approval-write precedent) so the
tenant admin sees them in the existing activity feed. The write is
BEST-EFFORT: a job is never failed over audit plumbing, but a failed
write is logged loudly.

For the two SECURITY events (``ui.tenant_boundary_refused``,
``ui.login_failed``) the structured LOG LINE is a MANDATORY second
channel, emitted unconditionally BEFORE the DB attempt — a DB hiccup
may lose the row, never the signal (the GO amendment).

Actor semantics: worker-emitted events are SYSTEM-AS-ACTOR —
``user_id`` NULL with ``details.actor = "browser-worker"``. Vault and
enqueue events carry the REAL user id (their writers pass it).
Details never carry secrets: classes, keys' PREFIX SHAPES, persona
keys, job ids — never a credential, a code, or key material.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text

_log = logging.getLogger("plimsol.browser_worker.audit")


def tenant_id_of(session) -> int:
    """The session's tenant id from its tenant context (DE-19 posture —
    connection-derived, never caller-passed)."""
    schema = (getattr(session, "info", None) or {}).get("tenant_schema")
    if not schema or not schema.startswith("tenant_"):
        raise RuntimeError("session carries no tenant_schema")
    return int(schema.split("_", 1)[1])


def record_event(session, *, action: str, details: dict,
                 user_id: int | None = None,
                 mandatory_log: bool = False,
                 tenant_id: int | None = None) -> None:
    if mandatory_log:
        # The unconditional channel — emitted FIRST, survives any DB state.
        _log.warning("%s %s", action,
                     json.dumps(details, sort_keys=True, default=str))
    try:
        payload = dict(details)
        if user_id is None:
            payload.setdefault("actor", "browser-worker")
        session.execute(text("""
            INSERT INTO public.activity_log
                (tenant_id, user_id, action, entity_type, entity_id,
                 details)
            VALUES (:t, :u, :a, 'ui_worker', NULL, CAST(:d AS JSONB))
        """), {"t": tenant_id if tenant_id is not None
                    else tenant_id_of(session),
               "u": user_id, "a": action,
               "d": json.dumps(payload, sort_keys=True, default=str)})
    except Exception:  # noqa: BLE001 — best-effort by design, loud on failure
        _log.error("activity_log write FAILED for %s (event preserved in "
                   "logs%s)", action,
                   "" if mandatory_log else " — non-security event lost")

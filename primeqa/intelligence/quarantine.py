"""Persisted flaky-test quarantine — the operator pin/unpin ledger (D-232).

D-200's flake quarantine is storage-free + decision-time only; this is the durable
ledger so a human can pin/unpin a claim (test_id) and the quarantine survives across
decision recomputes. Best-effort throughout: a missing ``claim_quarantine`` table
(pre-migration) reads as "no quarantine" and writes no-op, so the decision + UI degrade
gracefully and this module deploys before the additive migration is applied (D-230 order).

Harmonization (D-232): a MANUAL pin wins over the live ``_is_flaky`` signal in the release
decision; a MANUAL lift overrides it the other way; absent a manual entry, the live signal
governs. :func:`manual_states` feeds that override map to the decision.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)


def manual_states(tenant_id: int) -> dict:
    """The MANUAL override map for the decision: ``{test_id_str: 'pinned'|'lifted'}``
    over ``source='manual'`` rows (active→pinned, inactive→lifted). Best-effort → ``{}``
    on any error (incl. a missing table). ``auto`` rows are NOT returned — they are
    redundant with the live signal, never an override."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT CAST(test_id AS text) AS test_id, active "
                "FROM claim_quarantine WHERE source = 'manual'")).mappings().all()
        return {r["test_id"]: ("pinned" if r["active"] else "lifted") for r in rows}
    except Exception as exc:
        log.warning("quarantine.manual_states unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {}


def is_quarantined(tenant_id: int, test_id) -> bool:
    """True iff the claim has an ACTIVE quarantine row (any source) — the UI badge.
    Best-effort → False on any error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            row = conn.execute(text(
                "SELECT active FROM claim_quarantine "
                "WHERE test_id = CAST(:tid AS uuid)"),
                {"tid": str(test_id)}).mappings().first()
        return bool(row and row["active"])
    except Exception as exc:
        log.warning("quarantine.is_quarantined unavailable for tenant %s claim %s: %s",
                    tenant_id, test_id, exc)
        return False


def list_quarantined(tenant_id: int) -> list:
    """The active quarantine rows (for a list/dashboard). Best-effort → ``[]``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT CAST(test_id AS text) AS test_id, reason, source, "
                "pinned_at, pinned_by FROM claim_quarantine "
                "WHERE active = true ORDER BY pinned_at DESC")).mappings().all()
        return [{"test_id": r["test_id"], "reason": r["reason"],
                 "source": r["source"],
                 "pinned_at": r["pinned_at"].isoformat() if r["pinned_at"] else None,
                 "pinned_by": r["pinned_by"]} for r in rows]
    except Exception as exc:
        log.warning("quarantine.list_quarantined unavailable for tenant %s: %s",
                    tenant_id, exc)
        return []


def pin(tenant_id: int, test_id, *, reason: Optional[str] = None,
        actor: Optional[int] = None, source: str = "manual") -> bool:
    """Pin (quarantine) a claim — upsert ``active=true`` on test_id. Re-pinning an
    already-active claim refreshes reason/actor + clears any prior lift. Returns True
    on a committed write, False best-effort on any error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            conn.execute(text(
                "INSERT INTO claim_quarantine "
                "  (test_id, active, reason, source, pinned_by, lifted_at, lifted_by) "
                "VALUES (CAST(:tid AS uuid), true, :reason, :source, :actor, NULL, NULL) "
                "ON CONFLICT (test_id) DO UPDATE SET "
                "  active = true, reason = EXCLUDED.reason, source = EXCLUDED.source, "
                "  pinned_at = now(), pinned_by = EXCLUDED.pinned_by, "
                "  lifted_at = NULL, lifted_by = NULL"),
                {"tid": str(test_id), "reason": reason, "source": source,
                 "actor": actor})
        return True
    except Exception as exc:
        log.warning("quarantine.pin failed for tenant %s claim %s: %s",
                    tenant_id, test_id, exc)
        return False


def unpin(tenant_id: int, test_id, *, actor: Optional[int] = None) -> bool:
    """Lift a claim's quarantine — ``active=false`` + stamp lifted_*. An unpin is a
    durable MANUAL override (it suppresses the live auto-signal in the decision); if no
    row exists, insert an inactive manual lift so the override persists. Returns True on
    a committed write, False best-effort on error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            conn.execute(text(
                "INSERT INTO claim_quarantine "
                "  (test_id, active, source, pinned_by, lifted_at, lifted_by) "
                "VALUES (CAST(:tid AS uuid), false, 'manual', NULL, now(), :actor) "
                "ON CONFLICT (test_id) DO UPDATE SET "
                "  active = false, source = 'manual', "
                "  lifted_at = now(), lifted_by = EXCLUDED.lifted_by"),
                {"tid": str(test_id), "actor": actor})
        return True
    except Exception as exc:
        log.warning("quarantine.unpin failed for tenant %s claim %s: %s",
                    tenant_id, test_id, exc)
        return False


__all__ = ["manual_states", "is_quarantined", "list_quarantined", "pin", "unpin"]

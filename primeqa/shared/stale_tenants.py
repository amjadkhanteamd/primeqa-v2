"""The stale-tenant posture for per-tenant background ticks.

A tenant-registry row (``public.tenants`` / ``shared.tenants``) without a
provisioned schema — or with a schema that predates a table's migration —
must be SKIPPED loudly-once per process, never an exception per 60s tick.
Unguarded, ~14 unprovisioned ``public.tenants`` rows raised UndefinedTable
per tenant per tick and flooded the scheduler log past Railway's rate
limit (FIX PLAN 2026-09-03; the ``ui_schedules`` guard @0862c5e is the
inline precedent this module generalises for the remaining ticks).
"""
from __future__ import annotations

from sqlalchemy import text

# (tenant_id, table) pairs already warned — loudly-once per process.
_WARNED_UNPROVISIONED: set = set()


def skip_unprovisioned(conn, tenant_id: int, table: str, log) -> bool:
    """True when ``table`` does not resolve on ``conn`` — the caller skips
    this tenant. ``conn`` must be the TENANT-scoped connection:
    ``to_regclass`` resolves through its search_path, so a missing schema
    and a missing table both read as unprovisioned. Warns once per
    (tenant, table) per process via the caller's ``log`` so the skip stays
    visible without flooding."""
    if conn.execute(text("SELECT to_regclass(:t)"),
                    {"t": table}).scalar() is not None:
        return False
    key = (tenant_id, table)
    if key not in _WARNED_UNPROVISIONED:
        _WARNED_UNPROVISIONED.add(key)
        log.warning("tenant %s has no %s table — skipped (warned once "
                    "per process)", tenant_id, table)
    return True

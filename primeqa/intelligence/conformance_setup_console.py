"""Step 6b — the Conformance-setup readers (LLD_STEP_6B_DECISION_SETUP §b).

Five surfaces that have no UI at all today, each a thin READ over a service
that already exists. Read-only in v1, ruled: the acts they would carry
(registering a site, cutting an inventory, ratifying a map set, authoring a
rule) are CLI or service-level, and giving them buttons is a separate design.

**Sites & personas is read-only for a stronger reason than "not in v1": a
registration form would be a credential-entry surface, and the web tier holds
no portal cryptography.** Key material and portal secrets live in the vault and
are reached by the CLI and the worker; no route here accepts them, and this
module never reads them.

Every reader is best-effort and returns ``{available, ...}``; a page renders
"unavailable" rather than failing.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _read(tenant_id: int, fn, label: str, empty: dict) -> dict:
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection
    try:
        with get_tenant_connection(tenant_id) as conn:
            s = Session(bind=conn)
            try:
                return {"available": True, **fn(s)}
            finally:
                s.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("%s unavailable for tenant %s: %s", label, tenant_id, exc)
        return {"available": False, **empty}


def sites_and_personas(tenant_id: int) -> dict:
    """The distinct sites and persona scopes the ACTIVE inventory covers.

    Registration is a CLI act and stays one: a form here would collect the
    credentials a site is reached with, and no web route in this product
    accepts credentials or holds portal cryptography."""
    def _fn(s):
        from primeqa.test_representation import surface_links as sl
        v = sl.active_inventory_version(s)
        rows = s.execute(text("""
            SELECT site, persona_scope, count(*) AS surfaces,
                   count(*) FILTER (WHERE auth_required) AS auth_required,
                   count(*) FILTER (WHERE record_context_ref IS NOT NULL) AS record_context
            FROM ui_surface_inventory_members
            WHERE inventory_version = :v
            GROUP BY site, persona_scope ORDER BY site, persona_scope
        """), {"v": v}).mappings().all() if v is not None else []
        return {"active_inventory_version": v, "rows": [dict(r) for r in rows]}
    return _read(tenant_id, _fn, "sites and personas",
                 {"active_inventory_version": None, "rows": []})


def surface_inventory(tenant_id: int) -> dict:
    """Every inventory version, which one is ACTIVE, and why it is — the
    derivation D-485 recorded (the highest version carrying an approved claim
    set), whose successor is the inventory lifecycle flag."""
    def _fn(s):
        from primeqa.test_representation import surface_links as sl
        active = sl.active_inventory_version(s)
        rows = s.execute(text("""
            SELECT i.inventory_version, i.created_at, i.created_by,
                   (SELECT count(*) FROM ui_surface_inventory_members m
                     WHERE m.inventory_version = i.inventory_version) AS surfaces,
                   (SELECT count(*) FROM claim_sets cs
                     WHERE cs.inventory_version = i.inventory_version) AS claim_sets,
                   (SELECT count(*) FROM claim_sets cs
                     WHERE cs.inventory_version = i.inventory_version
                       AND cs.status = 'approved') AS approved_sets
            FROM ui_surface_inventories i
            ORDER BY i.inventory_version DESC LIMIT 25
        """)).mappings().all()
        return {"active_inventory_version": active,
                "derivation": ("the highest inventory version carrying an APPROVED claim set "
                               "— inventories carry no lifecycle flag today (D-485); the flag "
                               "is this derivation's recorded successor"),
                "rows": [dict(r, created_at=_iso(r["created_at"])) for r in rows]}
    return _read(tenant_id, _fn, "surface inventory",
                 {"active_inventory_version": None, "derivation": None, "rows": []})


def claim_sets(tenant_id: int) -> dict:
    """Claim sets with their state, size and inventory. Approve is the ONE
    conformance act with a route today, and it is already audited."""
    def _fn(s):
        rows = s.execute(text("""
            SELECT CAST(cs.id AS text) AS id, cs.persona_scope, cs.inventory_version,
                   cs.standard_profile, cs.status, cs.created_at, cs.created_by,
                   cs.approved_by, cs.approved_at, cs.member_count,
                   (SELECT count(*) FROM claim_set_members m
                     WHERE m.claim_set_id = cs.id AND m.revoked_at IS NULL) AS live_members,
                   (SELECT count(*) FROM s6_ui_processing_runs p
                     WHERE p.claim_set_id = cs.id) AS processing_runs
            FROM claim_sets cs ORDER BY cs.created_at DESC LIMIT 25
        """)).mappings().all()
        return {"rows": [dict(r, created_at=_iso(r["created_at"]),
                              approved_at=_iso(r["approved_at"])) for r in rows]}
    return _read(tenant_id, _fn, "claim sets", {"rows": []})


def standards_and_catalogues(tenant_id: int) -> dict:
    """The standard map sets with their lifecycle state, and for the ACTIVE set
    of each standard the rule count by criterion level. Coverage (§d) rides
    this page because it is a property of the catalogue, not of a release."""
    def _fn(s):
        sets = s.execute(text("""
            SELECT id, standard, standard_version, state, activated_at, reviewed_at,
                   content_hash, notes
            FROM public.s5_standard_map_sets ORDER BY standard, state, id DESC
        """)).mappings().all()
        levels = {}
        for r in s.execute(text("""
            SELECT ms.standard, m.level, count(*) AS n
            FROM public.s5_standard_maps m
            JOIN public.s5_standard_map_sets ms ON ms.id = m.map_set_id
            WHERE ms.state = 'ACTIVE' GROUP BY ms.standard, m.level
        """)).fetchall():
            levels.setdefault(r[0], {})[r[1] or "unmapped"] = r[2]
        return {"rows": [dict(r, activated_at=_iso(r["activated_at"]),
                              reviewed_at=_iso(r["reviewed_at"])) for r in sets],
                "levels": levels}
    return _read(tenant_id, _fn, "standards and catalogues", {"rows": [], "levels": {}})


def custom_rules(tenant_id: int) -> dict:
    """The rule registry: each rule's newest version and its lifecycle state.
    Authoring is S5's grammar, not a form."""
    def _fn(s):
        rows = s.execute(text("""
            SELECT DISTINCT ON (v.rule_id) v.rule_id, v.version, v.name,
                   v.automation_capability, v.created_at
            FROM public.s5_rule_versions v
            ORDER BY v.rule_id, v.version DESC LIMIT 100
        """)).mappings().all()
        return {"rows": [dict(r, created_at=_iso(r["created_at"])) for r in rows]}
    return _read(tenant_id, _fn, "custom rules", {"rows": []})

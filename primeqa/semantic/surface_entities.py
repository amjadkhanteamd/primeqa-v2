"""S1 Surface entities — DECLARED, never synced (LLD 3A-5 §b).

A Surface is a declared entity: rows materialize from inventory
declarations, not from the org, so the sync engine never touches this
type (no phase, not in ENTITY_ORDER, never legal in
sync_runs.last_completed_phase). One entity per DISTINCT canonical
surface key across inventory versions — re-declaring the same key in a
later version REUSES the entity (the entity is the continuity object;
declared membership and its D-281 immutability stay inventory-side).

Claim identity is UNCHANGED (the 3A-2 decision): the frozen natural key
remains the only hash input; the entity id appears nowhere in any claim
body — it fills the identity-EXCLUDED ``surface_entity_ref`` slot on
the inventory member rows.
"""
from __future__ import annotations

import json
import uuid as _uuid_mod

from sqlalchemy import text
from sqlalchemy.orm import Session


def materialize_surface_entities(
    session: Session, *, inventory_version: int, connected_org_id: str,
) -> dict:
    """Create (or reuse) one Surface entity per member of the inventory
    version and fill each member row's ``surface_entity_ref``. Runs in
    the caller's transaction. Idempotent: existing entities are reused;
    already-filled refs are left untouched.

    Step 2 (§d): the CHECKPOINT this act mints is ORG-BOUND —
    ``connected_org_id`` is REQUIRED (the environment whose portal the
    inventory is cut for, resolved through the D-286 seam by the caller). The
    org-less checkpoint path is gone: it minted a tenant-wide
    ``logical_versions`` row at MAX+1 (the four rows Step B found). The
    Surface ENTITIES themselves stay org-less: S1's own CHECK
    ``entities_org_only_for_sync`` (20260622_0020) admits an org only on
    sync-origin rows — a declared, manual-curation entity is "legitimately
    org-less" by that rule, and a Surface is not org metadata. Under the
    targeted staleness predicate a checkpoint that touches no covered read
    moves nothing; without R-ground this binding would flip every grounding
    of the org stale — the two land together.
    """
    if not connected_org_id:
        raise ValueError("materialize_surface_entities requires connected_org_id "
                         "(the org-less checkpoint mint was removed — Step 2 §d)")
    members = session.execute(text("""
        SELECT surface_key, site, path, persona_scope,
               record_context_ref, viewport, display_name,
               surface_entity_ref
        FROM ui_surface_inventory_members
        WHERE inventory_version = :v ORDER BY surface_key
    """), {"v": inventory_version}).fetchall()
    if not members:
        raise ValueError(
            f"inventory version {inventory_version} has no members")

    existing = {r[0]: str(r[1]) for r in session.execute(text("""
        SELECT sf_api_name, id FROM entities
        WHERE entity_type = 'Surface' AND valid_to_seq IS NULL
    """)).fetchall()}

    version_seq = None
    created = 0
    reused = 0
    for m in members:
        key = m[0]
        entity_id = existing.get(key)
        if entity_id is None:
            if version_seq is None:
                # Declared entities need a valid_from_seq like any S1 row;
                # a manual_checkpoint logical version anchors the act.
                version_seq = session.execute(text("""
                    INSERT INTO logical_versions
                        (version_name, version_type, description, connected_org_id)
                    VALUES (:n, 'manual_checkpoint', :d, CAST(:org AS uuid))
                    RETURNING version_seq
                """), {"n": f"surface-materialization-inv{inventory_version}",
                       "d": "3A-5 declared Surface entities",
                       "org": str(connected_org_id)}).scalar_one()
            entity_id = str(_uuid_mod.uuid4())
            session.execute(text("""
                INSERT INTO entities
                    (id, entity_type, sf_id, sf_api_name, display_name,
                     attributes, valid_from_seq, valid_to_seq, tenant_id,
                     entity_origin, created_at, last_synced_at)
                VALUES (:id, 'Surface', NULL, :key, :dn,
                        CAST(:attr AS JSONB), :vfs, NULL,
                        current_setting('app.tenant_id')::INT,
                        'manual_curation', NOW(), NOW())
            """), {"id": entity_id, "key": key,
                   "dn": m[6] or key,
                   "attr": json.dumps({
                       "site": m[1], "path": m[2], "persona_scope": m[3],
                       "record_context_ref": m[4], "viewport": m[5],
                       "display_name": m[6],
                   }),
                   "vfs": version_seq})
            existing[key] = entity_id
            created += 1
        else:
            reused += 1
        if m[7] is None:
            session.execute(text("""
                UPDATE ui_surface_inventory_members
                SET surface_entity_ref = :e
                WHERE inventory_version = :v AND surface_key = :k
            """), {"e": entity_id, "v": inventory_version, "k": key})
    session.flush()
    return {"inventory_version": inventory_version,
            "entities_created": created, "entities_reused": reused}

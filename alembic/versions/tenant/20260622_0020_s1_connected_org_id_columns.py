"""per-org S1 Slice 1: connected_org_id on the spine + per-org active-unique index.

Gives the S1 semantic model an org DIMENSION without changing read behaviour.
Today there is ONE canonical model per TENANT (entities/edges/logical_versions
carry tenant_id but no org); the active-unique index ``idx_entities_unique_active``
keys on ``sf_id`` ALONE — so two orgs sharing an sf_id cannot both be active
(D-030's "single canonical entity per sf_id"). This migration:

  1. Adds ``connected_org_id UUID REFERENCES connected_orgs(id)`` (NULLABLE) to
     the three spine tables: entities, edges, logical_versions.
       - Detail tables (object_details … validation_rule_details, the
         validation_rule_field_refs hot-ref table) get NO column: they are 1:1
         with their entity (entity_id PRIMARY KEY FK ON DELETE CASCADE, no own
         validity — D-025), so org is inherited transitively via entity_id.
       - change_log gets NO column: it is the diff stream, scoped transitively
         via target_id + version_seq, not part of the model spine.

  2. Back-fills every existing row to its org (idempotent, IS DISTINCT FROM guards):
       - entities       ← last_synced_from_org_id  (the per-entity provenance
                            stamp; non-NULL only for entity_origin='sync', D-040)
       - logical_versions ← created_by_sync_run_id → sync_runs.source_org_id
       - edges          ← source entity's connected_org_id (run AFTER entities;
                            an edge is intra-org, so source.org == target.org)
     For env-59 (the only synced org, D-059) this is single-org → no conflicts.

  3. Adds CHECK ``entities_org_only_for_sync`` mirroring the existing
     ``entities_synced_from_only_for_sync``: an org may be present only on
     sync-origin rows (a non-sync 'requirements'/'manual_curation' entity is
     legitimately org-less). One-directional, so it never forces a sync row to
     carry an org — the sync WRITER guarantees that, not the schema.

  4. RE-KEYS the active-unique index from ``(sf_id)`` to
     ``(sf_id, connected_org_id)`` (same partial WHERE). After this, two orgs
     CAN hold the same sf_id active, while two active rows for the SAME
     (sf_id, org) still violate — the per-org invariant.
     Only ``idx_entities_unique_active`` is re-keyed: both edge unique indexes
     (idx_edges_unique_containment on source_entity_id, and
     idx_edges_unique_active_non_references on source+target) already key on
     entity UUIDs, which are per-org by construction.

Columns are NULLABLE this slice (NOT NULL deferred), justified per table:
  - entities: future non-sync rows are org-less (mirror of the existing
    provenance CHECK); the partition for sync rows is enforced by the writer
    stamp + the re-keyed unique index, not by NOT NULL.
  - logical_versions: a 'genesis'/bootstrap or test-fixture version (no
    created_by_sync_run_id) is legitimately org-less.
  - edges: live sync edges are always stamped by the writer (zero NULLs on real
    data, verified), but auxiliary/test edge writers (semantic/derivation.py's
    integrity primitive, the generation/eval harness) carry no org; nullable
    keeps the slice non-breaking. NOT NULL belongs in a later slice once those
    writers are routed through an org-aware helper or confirmed retired.

The READER (SemanticOrgModel: query.py / edges.py / connection.py) is UNTOUCHED
and stays org-blind this slice, so no consumer behaviour changes. The sync
WRITER is updated in the same change to stamp connected_org_id on every
entity/edge/logical_version it inserts (materialize.py, engine.py).

Idempotent (ADD COLUMN IF NOT EXISTS, DROP CONSTRAINT IF EXISTS + ADD,
DROP/CREATE INDEX IF [NOT] EXISTS, IS DISTINCT FROM backfill guards). Per-tenant
(tenant-branch migration). Apply with:

    alembic -x mode=all_tenants upgrade 20260622_0020
  (or -x mode=tenant -x tenant_id=N upgrade 20260622_0020)

Revision ID: 20260622_0020
Revises: 20260622_0010
Create Date: 2026-06-22
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260622_0020"
down_revision = "20260622_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add connected_org_id (NULLABLE) + FK to the three spine tables.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE entities "
        "ADD COLUMN IF NOT EXISTS connected_org_id UUID REFERENCES connected_orgs(id)"
    )
    op.execute(
        "ALTER TABLE edges "
        "ADD COLUMN IF NOT EXISTS connected_org_id UUID REFERENCES connected_orgs(id)"
    )
    op.execute(
        "ALTER TABLE logical_versions "
        "ADD COLUMN IF NOT EXISTS connected_org_id UUID REFERENCES connected_orgs(id)"
    )

    # ------------------------------------------------------------------
    # 2. Back-fill every existing row to its org (idempotent).
    #    Order matters: entities first, then edges (which read the
    #    entities backfill via source_entity_id).
    # ------------------------------------------------------------------
    # entities: the per-entity provenance stamp IS the org (sync rows only;
    # non-sync rows have NULL last_synced_from_org_id and stay org-less).
    op.execute(
        "UPDATE entities "
        "SET connected_org_id = last_synced_from_org_id "
        "WHERE entity_origin = 'sync' "
        "  AND last_synced_from_org_id IS NOT NULL "
        "  AND connected_org_id IS DISTINCT FROM last_synced_from_org_id"
    )
    # logical_versions: the org is the sync_run that created the version.
    op.execute(
        "UPDATE logical_versions v "
        "SET connected_org_id = s.source_org_id "
        "FROM sync_runs s "
        "WHERE v.created_by_sync_run_id = s.id "
        "  AND v.connected_org_id IS DISTINCT FROM s.source_org_id"
    )
    # edges: an edge's org is its source entity's org (intra-org relationship).
    op.execute(
        "UPDATE edges e "
        "SET connected_org_id = src.connected_org_id "
        "FROM entities src "
        "WHERE e.source_entity_id = src.id "
        "  AND src.connected_org_id IS NOT NULL "
        "  AND e.connected_org_id IS DISTINCT FROM src.connected_org_id"
    )

    # ------------------------------------------------------------------
    # 3. CHECK: an org may be present only on sync-origin entities
    #    (mirror of entities_synced_from_only_for_sync). Drop-then-add
    #    is the idempotent pattern for a CHECK constraint.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_org_only_for_sync"
    )
    op.execute(
        "ALTER TABLE entities ADD CONSTRAINT entities_org_only_for_sync CHECK ("
        "(entity_origin = 'sync') OR (connected_org_id IS NULL))"
    )

    # ------------------------------------------------------------------
    # 4. Re-key the active-unique index: per-org now.
    #    Two orgs CAN hold the same sf_id active; two active rows for the
    #    SAME (sf_id, org) still violate.
    # ------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS idx_entities_unique_active")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_unique_active "
        "ON entities(sf_id, connected_org_id) "
        "WHERE valid_to_seq IS NULL AND sf_id IS NOT NULL"
    )


def downgrade() -> None:
    # Restore the sf_id-only active-unique index (single-canonical). NOTE: this
    # only succeeds while the data is still single-canonical per sf_id — once a
    # second org has synced overlapping metadata, two active rows share an sf_id
    # and the sf_id-only unique index cannot be rebuilt (downgrade is best-effort
    # pre-multi-org, by design).
    op.execute("DROP INDEX IF EXISTS idx_entities_unique_active")
    op.execute(
        "ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_org_only_for_sync"
    )
    op.execute("ALTER TABLE logical_versions DROP COLUMN IF EXISTS connected_org_id")
    op.execute("ALTER TABLE edges DROP COLUMN IF EXISTS connected_org_id")
    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS connected_org_id")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_unique_active "
        "ON entities(sf_id) "
        "WHERE valid_to_seq IS NULL AND sf_id IS NOT NULL"
    )

"""substrate-1 field CRUD flags: field_details.is_createable/is_updateable (D-160)

Revision ID: 20260604_0030
Revises: 20260604_0020
Create Date: 2026-06-04

Per DECISIONS_LOG.md D-160 (cutover Step 3, slice 3.3) — the first sync-engine /
S1-schema touch of the cutover. The v1 validator's ``field_not_createable``
(CRITICAL) / ``field_not_updateable`` (WARNING) checks need per-field CRUD
writability, which ``field_details`` did not carry (only ``object_details`` had the
object-level flag). The Salesforce describe **already fetches**
``createable``/``updateable`` per field and they **survive normalization** (not in
``semantic/normalization.py::_VOLATILE_KEYS``); ``detail_mappers._map_field_details``
now maps them, and the dynamic detail-INSERT (``materialize.py`` builds columns from
the mapper dict's keys) carries them through.

**Default TRUE** — Salesforce's permissive default + the v1 ``MetaField``
server_default — so **no backfill**: existing ``field_details`` rows read True until
the next sync repopulates the real value (acceptable in the parallel-run window; a
permissive default never *adds* a false CRITICAL).

Additive, idempotent (ADD COLUMN IF NOT EXISTS) per the migrations-016+ convention.
Tenant-branch migration; ``field_details`` is per-tenant. Schema only — no behaviour.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260604_0030'
down_revision = '20260604_0020'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE field_details "
        "ADD COLUMN IF NOT EXISTS is_createable BOOLEAN NOT NULL DEFAULT true")
    op.execute(
        "ALTER TABLE field_details "
        "ADD COLUMN IF NOT EXISTS is_updateable BOOLEAN NOT NULL DEFAULT true")
    op.execute(
        "COMMENT ON COLUMN field_details.is_createable IS "
        "'D-160: per-field createability (DescribeFieldResult.createable). Drives "
        "the v1 validator field_not_createable CRITICAL check via the S1 read-switch "
        "(cutover Step 3). Default true = SF permissive default; real value on the "
        "next sync.'")
    op.execute(
        "COMMENT ON COLUMN field_details.is_updateable IS "
        "'D-160: per-field updateability (DescribeFieldResult.updateable). Drives the "
        "v1 validator field_not_updateable WARNING check. Default true.'")


def downgrade():
    op.execute("ALTER TABLE field_details DROP COLUMN IF EXISTS is_updateable")
    op.execute("ALTER TABLE field_details DROP COLUMN IF EXISTS is_createable")

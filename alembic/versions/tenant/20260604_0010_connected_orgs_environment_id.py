"""substrate-1 sync trigger: connected_orgs.environment_id (D-150)

Revision ID: 20260604_0010
Revises: 20260603_0030
Create Date: 2026-06-04

Per DECISIONS_LOG.md D-150 (cutover Step 0 / S0.1). The S1-sync production
trigger targets a ``connected_orgs`` row but resolves Salesforce credentials
from a v1 ``environment`` (environment → connection → ``_oauth_token``).
``connected_orgs`` had no link to ``environments``; this adds ``environment_id``
— a **nullable loose int → environments.id**, **no cross-schema FK** (the
substrate convention: ``environments`` lives in the shared/public schema, the
substrate tables in the per-tenant schema) — so
``ensure_connected_org_for_environment`` can upsert one sync target per
environment.

Idempotent (ADD COLUMN IF NOT EXISTS) per the migrations-016+ convention.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260604_0010'
down_revision = '20260603_0030'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE connected_orgs "
        "ADD COLUMN IF NOT EXISTS environment_id INTEGER")
    # The provisioning lookup: "the connected_org for this environment".
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_connected_orgs_environment_id "
        "ON connected_orgs(environment_id) WHERE environment_id IS NOT NULL")
    op.execute(
        "COMMENT ON COLUMN connected_orgs.environment_id IS "
        "'D-150: the v1 environment this org was provisioned from (loose int -> "
        "environments.id, no cross-schema FK). The S1-sync trigger resolves SF "
        "credentials from this environment; the connected_org is the sync target.'")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_connected_orgs_environment_id")
    op.execute("ALTER TABLE connected_orgs DROP COLUMN IF EXISTS environment_id")

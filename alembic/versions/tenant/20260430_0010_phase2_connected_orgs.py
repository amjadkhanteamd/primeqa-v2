"""Phase 2 tenant schema: connected_orgs (per Phase 2 plan §3.2)

First Phase 2 migration. Adds the per-tenant connected_orgs table that
captures which Salesforce orgs this tenant has connected. Any registered
org can be a sync source (D-030); the customer chooses at sync invocation.

Schema notes:
  - id UUID PK with gen_random_uuid() default. pgcrypto is in the tenant
    schema (per Phase 0 bootstrap), so the function resolves on
    search_path without explicit qualification.
  - org_type VARCHAR(20) NOT NULL with CHECK enum {production, sandbox,
    scratch, developer}. Names match Salesforce taxonomy.
  - sf_org_id VARCHAR(18) — the 18-char Salesforce org ID. Nullable
    until first successful OAuth handshake captures it.
  - sf_instance_url VARCHAR(255) NOT NULL — required at registration.
  - label VARCHAR(255) NOT NULL — customer-chosen display name.
  - release_label VARCHAR(100) — free-form tag per D-041 (multi-release
    support deferred; column is a future-extensibility hook only,
    not consumed for any logic in Phase 2).
  - oauth_access_token / oauth_refresh_token TEXT — plaintext per D-034
    (encryption is Phase 5 work; sandboxes only until Phase 5 ships).
    DB-level COMMENT ON COLUMN flags both for Phase 5 (visible via psql
    describe).
  - oauth_token_expires_at TIMESTAMPTZ — nullable; populated after first
    OAuth handshake.
  - last_sync_completed_at TIMESTAMPTZ — nullable until first sync.
  - last_sync_run_id UUID — NO FK in this migration; sync_runs is
    created in 1C, the FK constraint is added then.
  - created_at TIMESTAMPTZ NOT NULL DEFAULT NOW().

Per D-038 withdrawal: NO is_seed_source column and NO protective trigger.
Any registered org can serve as a sync source.

Revision ID: 20260430_0010
Revises: 20260427_0150
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260430_0010"
down_revision: Union[str, Sequence[str], None] = "20260427_0150"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE connected_orgs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_type VARCHAR(20) NOT NULL,
            sf_org_id VARCHAR(18),
            sf_instance_url VARCHAR(255) NOT NULL,
            label VARCHAR(255) NOT NULL,
            release_label VARCHAR(100),
            oauth_access_token TEXT,
            oauth_refresh_token TEXT,
            oauth_token_expires_at TIMESTAMPTZ,
            last_sync_completed_at TIMESTAMPTZ,
            last_sync_run_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT connected_orgs_org_type_known CHECK (
                org_type IN ('production', 'sandbox', 'scratch', 'developer')
            )
        )
    """)

    op.execute("""
        COMMENT ON COLUMN connected_orgs.oauth_access_token IS
            'TODO Phase 5 (D-034): encrypt at rest'
    """)

    op.execute("""
        COMMENT ON COLUMN connected_orgs.oauth_refresh_token IS
            'TODO Phase 5 (D-034): encrypt at rest'
    """)


def downgrade() -> None:
    op.execute("DROP TABLE connected_orgs")

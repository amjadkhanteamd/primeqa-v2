"""Phase 0 tenant schema: logical_versions, entities, change_log

Creates the three foundation tables of Substrate 1 in a tenant schema, per
SPEC §6.1, §6.2, §6.4. Verbatim to the SPEC except for two narrow choices:

  - `valid_to_seq IS NULL` semantic for "currently valid" is preserved per
    SPEC. The validity_range CHECK uses `valid_to_seq > valid_from_seq`
    (strict inequality), which means the smallest valid range is one
    version_seq increment. A "spot" entity that's created and immediately
    superseded in the same logical_version still gets valid_to_seq from
    the next version_seq, never the same one.

  - `change_log.target_table` is VARCHAR(20) per SPEC. The current values
    fit (max is 'logical_versions' at 16 chars). Tightening the column
    later requires a migration; loosening it doesn't. We follow the SPEC.

This migration does NOT create:
  - `edges` (Phase 1)
  - the 10 detail tables (Phase 1)
  - `effective_field_permissions` materialized view (Phase 2)

Revision ID: 20260427_0010
Revises: (none — branches at "tenant" label root)
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0010"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = ("tenant",)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The tenant schema must already exist (provisioned by
    # shared.provision_tenant_schema). search_path is set to the schema by
    # env.py before this runs, so unqualified table names land in the
    # tenant schema.
    #
    # The CHECK constraints reference `current_setting('app.tenant_id')::INT`,
    # which is set by env.py during migration (and by get_tenant_connection
    # at runtime). If the GUC is missing at constraint-evaluation time, the
    # CHECK raises 'unrecognized configuration parameter' — by design, fail
    # loud.

    # ------------------------------------------------------------------
    # 6.1 logical_versions — version anchor for everything S1
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE logical_versions (
            version_seq BIGSERIAL PRIMARY KEY,
            version_name VARCHAR(100) NOT NULL UNIQUE,
            version_type VARCHAR(40) NOT NULL,
            description TEXT,
            parent_version_seq BIGINT REFERENCES logical_versions(version_seq),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_sync_run_id UUID,

            CONSTRAINT logical_versions_version_type_known CHECK (
                version_type IN (
                    'genesis',
                    'deploy_detected',
                    'sandbox_refresh',
                    'manual_checkpoint',
                    'scheduled_milestone'
                )
            )
        )
    """)

    op.execute("""
        CREATE INDEX idx_versions_type_created
            ON logical_versions(version_type, created_at DESC)
    """)

    # ------------------------------------------------------------------
    # 6.2 entities — UUID primary key, attributes JSONB, bitemporal
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE entities (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_type VARCHAR(40) NOT NULL,
            sf_id VARCHAR(18),
            sf_api_name VARCHAR(255),
            display_name VARCHAR(255),
            attributes JSONB NOT NULL DEFAULT '{}',

            valid_from_seq BIGINT NOT NULL REFERENCES logical_versions(version_seq),
            valid_to_seq BIGINT REFERENCES logical_versions(version_seq),

            tenant_id INT NOT NULL DEFAULT current_setting('app.tenant_id')::INT,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_synced_at TIMESTAMPTZ NOT NULL,

            CONSTRAINT entities_validity_range CHECK (
                valid_to_seq IS NULL OR valid_to_seq > valid_from_seq
            ),
            CONSTRAINT entities_tenant_assertion CHECK (
                tenant_id = current_setting('app.tenant_id')::INT
            ),
            CONSTRAINT entities_attributes_is_object CHECK (
                jsonb_typeof(attributes) = 'object'
            )
        )
    """)

    # Current-state indexes (partial: only the "currently valid" rows)
    op.execute("CREATE INDEX idx_entities_current_type ON entities(entity_type) WHERE valid_to_seq IS NULL")
    op.execute("CREATE INDEX idx_entities_current_sf_id ON entities(sf_id) WHERE valid_to_seq IS NULL")
    op.execute("CREATE INDEX idx_entities_current_api_name ON entities(entity_type, sf_api_name) WHERE valid_to_seq IS NULL")

    # As-of-version indexes (full: needed for historical queries)
    op.execute("CREATE INDEX idx_entities_version_range ON entities(valid_from_seq, valid_to_seq)")
    op.execute("CREATE INDEX idx_entities_type_version ON entities(entity_type, valid_from_seq)")

    # Uniqueness invariant: at most one currently-valid entity per sf_id.
    # sf_id is nullable (some entities don't have one) — partial index excludes those.
    op.execute("""
        CREATE UNIQUE INDEX idx_entities_unique_active
            ON entities(sf_id)
            WHERE valid_to_seq IS NULL AND sf_id IS NOT NULL
    """)

    # ------------------------------------------------------------------
    # 6.4 change_log — diff stream for Substrate 1
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE change_log (
            id BIGSERIAL PRIMARY KEY,
            change_type VARCHAR(30) NOT NULL,

            target_table VARCHAR(20) NOT NULL,
            target_id UUID NOT NULL,
            before_state JSONB,
            after_state JSONB,
            changed_field_names TEXT[],

            version_seq BIGINT NOT NULL REFERENCES logical_versions(version_seq),
            sync_run_id UUID,

            tenant_id INT NOT NULL DEFAULT current_setting('app.tenant_id')::INT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT change_log_tenant_assertion CHECK (
                tenant_id = current_setting('app.tenant_id')::INT
            ),
            CONSTRAINT change_log_change_type_known CHECK (
                change_type IN (
                    'entity_created',
                    'entity_field_modified',
                    'entity_attributes_modified',
                    'entity_deleted',
                    'edge_created',
                    'edge_properties_modified',
                    'edge_deleted',
                    'detail_field_modified',
                    'detail_added',
                    'detail_removed'
                )
            ),
            CONSTRAINT change_log_target_table_known CHECK (
                target_table IN (
                    'entities',
                    'edges',
                    'logical_versions',
                    'object_details',
                    'field_details',
                    'validation_rule_details',
                    'flow_details',
                    'trigger_details',
                    'record_type_details',
                    'profile_details',
                    'permission_set_details',
                    'page_layout_details',
                    'sharing_rule_details'
                )
            )
        )
    """)

    op.execute("CREATE INDEX idx_change_log_target ON change_log(target_id, version_seq)")
    op.execute("CREATE INDEX idx_change_log_version ON change_log(version_seq)")
    op.execute("CREATE INDEX idx_change_log_sync_run ON change_log(sync_run_id) WHERE sync_run_id IS NOT NULL")
    op.execute("CREATE INDEX idx_change_log_type_version ON change_log(change_type, version_seq)")
    op.execute("CREATE INDEX idx_change_log_field_names ON change_log USING GIN(changed_field_names)")


def downgrade() -> None:
    # Drop in reverse dependency order. change_log refs logical_versions,
    # entities refs logical_versions. logical_versions is the leaf.
    op.execute("DROP TABLE IF EXISTS change_log CASCADE")
    op.execute("DROP TABLE IF EXISTS entities CASCADE")
    op.execute("DROP TABLE IF EXISTS logical_versions CASCADE")

"""Phase 1 tenant schema: edges table per SPEC §6.3 verbatim

Creates the edges table with:
  - UUID primary key (matches entities.id type)
  - source_entity_id, target_entity_id FKs to entities
  - edge_type discriminator (no enum constraint — TIER_1_EDGES registry
    is application-layer; new edge types arrive via code, not migration)
  - edge_category enum CHECK (4 values per D-017)
  - properties JSONB (jsonb_typeof = 'object' enforced; per-edge-type
    schemas enforced at application layer via Pydantic — D-016 discipline)
  - bitemporal valid_from_seq / valid_to_seq referencing logical_versions
  - tenant_id assertion via current_setting('app.tenant_id') — D-016
  - no_self_loop CHECK
  - validity_range CHECK
  - 6 indexes for hot traversal paths (current state, as-of-version)
  - containment uniqueness: UNIQUE on (source, edge_type, valid_from_seq)
    WHERE edge_category = 'STRUCTURAL' — per D-017

This migration does NOT:
  - Insert any edge rows (sync engine is Phase 2)
  - Create the 10 detail tables (separate Phase 1 migration)
  - Wire up containment-as-column derivation (Phase 1, after detail tables)
  - Touch entities or change_log (already in 20260427_0010)

Revision ID: 20260427_0020
Revises: 20260427_0010
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0020"
down_revision: Union[str, Sequence[str], None] = "20260427_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --------------------------------------------------------------
    # 6.3 edges — uniform graph layer per D-014, D-017, D-018, D-019
    # --------------------------------------------------------------
    op.execute("""
        CREATE TABLE edges (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_entity_id UUID NOT NULL REFERENCES entities(id),
            target_entity_id UUID NOT NULL REFERENCES entities(id),
            edge_type VARCHAR(60) NOT NULL,
            edge_category VARCHAR(20) NOT NULL CHECK (
                edge_category IN ('STRUCTURAL', 'CONFIG', 'PERMISSION', 'BEHAVIOR')
            ),
            properties JSONB NOT NULL DEFAULT '{}',

            valid_from_seq BIGINT NOT NULL REFERENCES logical_versions(version_seq),
            valid_to_seq BIGINT REFERENCES logical_versions(version_seq),

            tenant_id INT NOT NULL DEFAULT current_setting('app.tenant_id')::INT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT edges_validity_range CHECK (
                valid_to_seq IS NULL OR valid_to_seq > valid_from_seq
            ),
            CONSTRAINT edges_no_self_loop CHECK (
                source_entity_id != target_entity_id
            ),
            CONSTRAINT edges_tenant_assertion CHECK (
                tenant_id = current_setting('app.tenant_id')::INT
            ),
            CONSTRAINT edges_properties_is_object CHECK (
                jsonb_typeof(properties) = 'object'
            )
        )
    """)

    # Current-state traversal indexes (partial: only currently-valid rows)
    op.execute("""
        CREATE INDEX idx_edges_current_source
            ON edges(source_entity_id, edge_type)
            WHERE valid_to_seq IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_edges_current_target
            ON edges(target_entity_id, edge_type)
            WHERE valid_to_seq IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_edges_current_type
            ON edges(edge_type)
            WHERE valid_to_seq IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_edges_current_category
            ON edges(edge_category, source_entity_id)
            WHERE valid_to_seq IS NULL
    """)

    # As-of-version traversal indexes (full: needed for historical queries)
    op.execute("""
        CREATE INDEX idx_edges_source_version
            ON edges(source_entity_id, edge_type, valid_from_seq)
    """)
    op.execute("""
        CREATE INDEX idx_edges_target_version
            ON edges(target_entity_id, edge_type, valid_from_seq)
    """)

    # Containment uniqueness (D-017): only for STRUCTURAL containment edges.
    # Prevents duplicate BELONGS_TO entries for the same source at the same version.
    op.execute("""
        CREATE UNIQUE INDEX idx_edges_unique_containment
            ON edges(source_entity_id, edge_type, valid_from_seq)
            WHERE edge_category = 'STRUCTURAL'
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS edges CASCADE")

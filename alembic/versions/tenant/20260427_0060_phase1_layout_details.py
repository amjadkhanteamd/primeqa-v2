"""Phase 1 tenant schema: layout_details (per D-025)

Fourth detail table. Hot/queryable attributes for entity_type='Layout'.

Per D-025:
  (1) Per-entity-version: entity_id PK, FK to entities ON DELETE CASCADE.
  (2) Hot columns capture queryable Layout metadata.
  (3) No tenant_id column.

Containment (D-017):
  - object_entity_id UUID NOT NULL — every Layout belongs to exactly one
    Object. The BELONGS_TO STRUCTURAL edge is auto-derived from this column.

Hot columns (5 total + PK):
  Containment (1):     object_entity_id
  Type (1):            layout_type — Salesforce types: 'Standard', 'Console',
                       'Compact', 'Search', 'Path', 'Lightning'. Filtered in
                       generation paths ("show me console layouts").
  API name (1):        layout_api_name — Salesforce layout names like
                       'Account-Account Layout', heavily filtered in
                       deployment-driven tooling. Redundant with entities.sf_api_name
                       per D-025 promotion rule (cross-entity queries warrant
                       column promotion); query convenience > storage cost.
  Boolean flags (1):   is_active — most layouts are active; the column exists
                       to filter the rare deactivated layout out of generation paths.
  Audit (1):           created_at

Layout's structural weight is in INCLUDES_FIELD edges (D-019, with section_name,
section_order, row, column, is_required, is_readonly properties). Field
placement is NOT stored on this detail table; it lives on edges.

JSONB attributes (sparse, in entities.attributes via LayoutAttributes Pydantic):
  description.

Indexes:
  - Containment lookup (BELONGS_TO derivation): object_entity_id
  - Type filter (generation paths): layout_type

Revision ID: 20260427_0060
Revises: 20260427_0050
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0060"
down_revision: Union[str, Sequence[str], None] = "20260427_0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE layout_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            -- Containment (auto-derives BELONGS_TO STRUCTURAL edge)
            object_entity_id UUID NOT NULL REFERENCES entities(id),

            -- Type discriminator
            layout_type VARCHAR(20) NOT NULL,

            -- API name (redundant with entities.sf_api_name; D-025 promotion)
            layout_api_name VARCHAR(255) NOT NULL,

            -- Boolean flags
            is_active BOOLEAN NOT NULL DEFAULT TRUE,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX idx_layout_details_object
            ON layout_details(object_entity_id)
    """)

    op.execute("""
        CREATE INDEX idx_layout_details_type
            ON layout_details(layout_type)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS layout_details CASCADE")

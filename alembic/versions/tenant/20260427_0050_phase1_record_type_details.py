"""Phase 1 tenant schema: record_type_details (per D-025)

Third detail table. Hot/queryable attributes for entity_type='RecordType'.

Per D-025:
  (1) Per-entity-version: entity_id PK, FK to entities ON DELETE CASCADE.
  (2) Hot columns capture queryable RecordType metadata.
  (3) No tenant_id column.

Containment (D-017):
  - object_entity_id UUID NOT NULL — RecordType belongs to exactly one
    Object. The BELONGS_TO STRUCTURAL edge is auto-derived from this column.

Hot columns (5 total):
  Containment (1):       object_entity_id
  Boolean flags (2):     is_active, is_master
                         is_active — generation/diff filter ("active types only")
                         is_master — distinguishes the implicit Master record
                                     type Salesforce maintains per Object
  Audit (1):             created_at
  (entity_id PK is the 5th if counting; depending on how you count it)

JSONB attributes (sparse, in entities.attributes via RecordTypeAttributes):
  description, business_process_id.

RecordType has unusually little hot metadata; most of its semantic weight
lives in outgoing edges (CONSTRAINS_PICKLIST_VALUES to PicklistValueSets,
ASSIGNED_TO_PROFILE_RECORDTYPE to Profiles).

Indexes:
  - Containment lookup (BELONGS_TO derivation): object_entity_id
  - Active filter (common): is_active partial on TRUE

Revision ID: 20260427_0050
Revises: 20260427_0040
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0050"
down_revision: Union[str, Sequence[str], None] = "20260427_0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE record_type_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            -- Containment (auto-derives BELONGS_TO STRUCTURAL edge)
            object_entity_id UUID NOT NULL REFERENCES entities(id),

            -- Boolean flags
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_master BOOLEAN NOT NULL DEFAULT FALSE,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # Containment lookup: all record types contained by Object X.
    op.execute("""
        CREATE INDEX idx_record_type_details_object
            ON record_type_details(object_entity_id)
    """)

    # Active-only filter is common in generation paths.
    op.execute("""
        CREATE INDEX idx_record_type_details_active
            ON record_type_details(entity_id)
            WHERE is_active = TRUE
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS record_type_details CASCADE")

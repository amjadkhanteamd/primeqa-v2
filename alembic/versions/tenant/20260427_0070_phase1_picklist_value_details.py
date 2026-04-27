"""Phase 1 tenant schema: picklist_value_details (per D-025)

Fifth detail table. Hot/queryable attributes for entity_type='PicklistValue'.

Per D-025:
  (1) Per-entity-version: entity_id PK, FK to entities ON DELETE CASCADE.
  (2) Hot columns capture queryable picklist value metadata.
  (3) No tenant_id column.

Containment (D-017):
  picklist_value_set_entity_id UUID NOT NULL — every value belongs to
  exactly one PicklistValueSet. Drives the BELONGS_TO STRUCTURAL edge.
  First detail table where containment is NOT to Object — pattern still
  holds (FK to entities, BELONGS_TO derives), but parent type is
  PicklistValueSet rather than Object.

Hot columns:
  Containment: picklist_value_set_entity_id
  Identity:    value_label (user-visible), value_api_name (API value;
               redundant with entities.sf_api_name per D-025 promotion;
               generation/validation queries lookup by api_name).
  Flags:       is_active (heavily filtered), is_default (at most one per set,
               Salesforce-enforced).
  Ordering:    sort_order (display order within the set).

Uniqueness invariant — NOT enforced at DB level:
  (picklist_value_set_entity_id, value_api_name) must be unique among
  rows whose corresponding entities row is currently active
  (entities.valid_to_seq IS NULL).

  This is a temporal invariant. UNIQUE(set, api_name) at DB level would
  block legitimate sync writes when Salesforce reuses an api_name after
  deactivation. The sync engine enforces this with version awareness:
  on write, look up existing active row for the same (set, api_name);
  if exists, supersede or update; else insert.

JSONB attributes (sparse, in entities.attributes via PicklistValueAttributes):
  color_code (Optional VARCHAR(7) hex; rare).

Indexes:
  Containment lookup (BELONGS_TO derivation): picklist_value_set_entity_id
  Composite for ordered active traversal: (set_entity_id, is_active, sort_order)
    — supports the most common picklist read: currently-active values for
    set X in display order.
  API name lookup: value_api_name. Supports cross-set lookup by API name.

Revision ID: 20260427_0070
Revises: 20260427_0060
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0070"
down_revision: Union[str, Sequence[str], None] = "20260427_0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE picklist_value_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            picklist_value_set_entity_id UUID NOT NULL REFERENCES entities(id),

            value_label VARCHAR(255) NOT NULL,
            value_api_name VARCHAR(40) NOT NULL,

            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,

            sort_order INT NOT NULL DEFAULT 0,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX idx_picklist_value_set
            ON picklist_value_details(picklist_value_set_entity_id)
    """)

    op.execute("""
        CREATE INDEX idx_picklist_value_set_active_order
            ON picklist_value_details(picklist_value_set_entity_id, is_active, sort_order)
    """)

    op.execute("""
        CREATE INDEX idx_picklist_value_api_name
            ON picklist_value_details(value_api_name)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS picklist_value_details CASCADE")

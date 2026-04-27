"""Phase 1 tenant schema: field_details (per D-025)

Second detail table. Hot/queryable attributes for entity_type='Field'.

Per D-025:
  (1) Per-entity-version: entity_id PK, FK to entities ON DELETE CASCADE.
  (2) Hot columns capture frequently-queried Salesforce field metadata.
  (3) No tenant_id column.

Containment columns (D-017):
  - object_entity_id UUID NOT NULL — every field belongs to exactly one
    object. The BELONGS_TO STRUCTURAL edge is auto-derived from this column
    by the future containment-derivation logic. NOT NULL because Field
    without an Object is meaningless.
  - references_object_entity_id UUID NULLABLE — populated for lookup,
    master-detail, and similar relationship-bearing field types. Drives
    the HAS_RELATIONSHIP_TO STRUCTURAL edge (D-019). NULL for non-relationship
    fields. No DB-level CHECK enforcing "only relationship types can have
    this set" — Pydantic FieldDetails model enforces it at write time, where
    field_type and references are both visible.

Hot columns chosen (14 total):
  Containment (2):       object_entity_id, references_object_entity_id
  Type discriminator (1): field_type — 'text', 'number', 'date', 'lookup',
                          'picklist', 'boolean', 'currency', 'percent', etc.
                          Heavily filtered ("find all currency fields").
  Boolean flags (7):     is_custom, is_unique, is_external_id, is_nillable,
                          is_calculated, is_filterable, is_sortable
  Numeric metadata (3):  length, precision, scale (nullable, type-dependent)
  Audit (1):             created_at

JSONB attributes (sparse, in entities.attributes via FieldAttributes Pydantic):
  is_required, is_groupable, is_aggregatable, is_case_sensitive,
  is_html_formatted, default_value, formula, inline_help_text, help_text,
  relationship_name, controller_name.

Indexes:
  - Containment lookup (BELONGS_TO derivation): object_entity_id
  - Relationship lookup (HAS_RELATIONSHIP_TO derivation): references_object_entity_id partial
  - Field-type filter (very common): field_type
  - Custom filter (common): is_custom partial

Revision ID: 20260427_0040
Revises: 20260427_0030
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0040"
down_revision: Union[str, Sequence[str], None] = "20260427_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE field_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            -- Containment (auto-derives BELONGS_TO STRUCTURAL edge)
            object_entity_id UUID NOT NULL REFERENCES entities(id),

            -- Relationship (auto-derives HAS_RELATIONSHIP_TO STRUCTURAL edge)
            references_object_entity_id UUID REFERENCES entities(id),

            -- Type
            field_type VARCHAR(40) NOT NULL,

            -- Boolean flags
            is_custom BOOLEAN NOT NULL DEFAULT FALSE,
            is_unique BOOLEAN NOT NULL DEFAULT FALSE,
            is_external_id BOOLEAN NOT NULL DEFAULT FALSE,
            is_nillable BOOLEAN NOT NULL DEFAULT TRUE,
            is_calculated BOOLEAN NOT NULL DEFAULT FALSE,
            is_filterable BOOLEAN NOT NULL DEFAULT TRUE,
            is_sortable BOOLEAN NOT NULL DEFAULT TRUE,

            -- Numeric metadata (type-dependent; NULL for non-numeric/non-text fields)
            length INT,
            precision INT,
            scale INT,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # Containment lookup. Used by BELONGS_TO derivation: "all fields where
    # object_entity_id = X" yields all fields contained in Object X.
    op.execute("""
        CREATE INDEX idx_field_details_object
            ON field_details(object_entity_id)
    """)

    # Relationship lookup. Used by HAS_RELATIONSHIP_TO derivation:
    # "all fields where references_object_entity_id = X" yields all fields
    # that point at Object X via lookup/master-detail. Partial because most
    # fields don't reference (saves index size).
    op.execute("""
        CREATE INDEX idx_field_details_references
            ON field_details(references_object_entity_id)
            WHERE references_object_entity_id IS NOT NULL
    """)

    # Field-type filter. Very common in generation: "find all picklist
    # fields", "find all currency fields", etc.
    op.execute("""
        CREATE INDEX idx_field_details_type
            ON field_details(field_type)
    """)

    # Custom-only filter. Partial on TRUE since custom fields are the smaller
    # population in most orgs; "WHERE is_custom" is more common than
    # "WHERE NOT is_custom".
    op.execute("""
        CREATE INDEX idx_field_details_custom
            ON field_details(entity_id)
            WHERE is_custom = TRUE
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS field_details CASCADE")

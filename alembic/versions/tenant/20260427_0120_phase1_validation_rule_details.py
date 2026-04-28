"""Phase 1 tenant schema: validation_rule_details + validation_rule_field_refs

Tenth and eleventh tables of Phase 1 detail-table work; final migration
before containment-derivation logic.

validation_rule_details follows D-025 (per-entity-version, joined by
entity_id, hot columns + JSONB attributes split, no tenant_id column).

validation_rule_field_refs is a NEW pattern — D-018 calls it a "hot
reference table." It is NOT a D-025 detail table:
  - Not 1:1 with entities; one row per (rule, field, reference_type) tuple
  - No entity_id PK — composite PK instead
  - No JSONB attributes column; no Pydantic schema in entity_attributes.py
  - No registry entry in TIER_1_ENTITIES
This pattern is recognized via D-018's explicit naming; no D-026 is added
for it because the migration's docstring suffices as documentation.
Future tables of this shape (if any arise) can reference this migration.

----------------------------------------------------------------------
validation_rule_details
----------------------------------------------------------------------

Per D-025:
  (1) Per-entity-version: entity_id PK, FK to entities ON DELETE CASCADE.
  (2) Hot columns capture queryable validation rule metadata.
  (3) No tenant_id column.

Containment (D-017):
  object_entity_id UUID NOT NULL — every validation rule belongs to
  exactly one Object. The BELONGS_TO STRUCTURAL edge is auto-derived
  from this column. APPLIES_TO edge per D-019 also derives from here
  (BEHAVIOR-category, same source column — APPLIES_TO is the
  rule-applies-to-object semantic, distinct from BELONGS_TO containment).

Hot columns (3 + PK + audit):
  Containment (1):  object_entity_id
  Boolean flags (1): is_active — heavily filtered ("active rules only")
  Audit (1):         created_at

JSONB attributes (sparse, in entities.attributes via ValidationRuleAttributes):
  error_message — message displayed to users on validation failure
  error_display_field — which field the error anchors to in the UI
                        (NULL = top-of-page)
  formula_text — raw formula text. Tier 1 stores raw; future Tier 2
                 may parse. Per-rule, not queried across population.

Indexes:
  - Containment lookup (BELONGS_TO + APPLIES_TO derivation): object_entity_id
  - Active-only filter: is_active partial WHERE TRUE

----------------------------------------------------------------------
validation_rule_field_refs (hot reference table)
----------------------------------------------------------------------

Each validation rule's formula references one or more fields. This table
materializes those references as queryable rows so impact analysis
("if I rename Field X, what rules break") can answer in one indexed
lookup rather than scanning formula text.

Per D-019: REFERENCES edge (BEHAVIOR-category) derives from this table.
Each row produces one REFERENCES edge from validation_rule -> field
with reference_type and convenience flags as edge properties.

Schema:
  validation_rule_entity_id  UUID NOT NULL — the referencing rule;
                             CASCADE on rule deletion (rule gone -> refs gone).
  field_entity_id            UUID NOT NULL — the referenced field;
                             NO CASCADE (deleting a referenced field while
                             a rule still references it is a real problem;
                             FK should block until the rule is deactivated
                             or the reference is removed).
  reference_type             VARCHAR(20) NOT NULL — 'read' | 'priorvalue'
                             | 'ischanged' | 'isnew'. CHECK enforces enum
                             at DB level (mirrors D-019 ReferencesProperties
                             enum).
  is_priorvalue, is_ischanged, is_isnew  BOOLEAN convenience flags;
                             redundant with reference_type but cheap and
                             query-friendly. Mutually exclusive: at most
                             one is TRUE per row (not enforced as CHECK
                             because reference_type already serves that
                             role; redundant CHECK would denormalize the
                             enforcement).

PRIMARY KEY: (validation_rule_entity_id, field_entity_id, reference_type)
  Composite key — semantically meaningful and naturally unique.
  No surrogate UUID needed since nothing FKs into this table.
  PK serves the forward query "what fields does rule X reference?" via
  its leading column.

INDEX (field_entity_id):
  Reverse lookup for impact analysis. Primary motivating query for the
  whole table — "what validation rules reference field X?" — without
  this, that query becomes a sequential scan.

NOT included intentionally:
  - object_entity_id denormalization (rejected): the rule's object is on
    validation_rule_details; the field's object is on field_details;
    duplicating it here would store the same fact in three places and
    require sync code to keep aligned. Cross-object queries go through
    the natural JOIN (typical query is single JOIN, both sides indexed).
  - Composite (field_entity_id, reference_type) index (rejected for now):
    speculative; D-025 promotion rule says add indexes when queries
    demand them. Field_entity_id alone narrows results sufficiently
    (typically <50 rules per field).
  - Pydantic class for field_refs rows: rejected. DB constraints
    (CHECK on reference_type, NOT NULL columns, composite PK)
    sufficient. Sync engine can construct rows from edge property
    schemas.

Revision ID: 20260427_0120
Revises: 20260427_0110
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0120"
down_revision: Union[str, Sequence[str], None] = "20260427_0110"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------
    # validation_rule_details (D-025 detail table)
    # ------------------------------------------------------------
    op.execute("""
        CREATE TABLE validation_rule_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            object_entity_id UUID NOT NULL REFERENCES entities(id),

            is_active BOOLEAN NOT NULL DEFAULT TRUE,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX idx_validation_rule_details_object
            ON validation_rule_details(object_entity_id)
    """)

    op.execute("""
        CREATE INDEX idx_validation_rule_details_active
            ON validation_rule_details(entity_id)
            WHERE is_active = TRUE
    """)

    # ------------------------------------------------------------
    # validation_rule_field_refs (hot reference table — new pattern)
    # ------------------------------------------------------------
    op.execute("""
        CREATE TABLE validation_rule_field_refs (
            validation_rule_entity_id UUID NOT NULL
                REFERENCES entities(id) ON DELETE CASCADE,

            field_entity_id UUID NOT NULL REFERENCES entities(id),

            reference_type VARCHAR(20) NOT NULL,

            is_priorvalue BOOLEAN NOT NULL DEFAULT FALSE,
            is_ischanged BOOLEAN NOT NULL DEFAULT FALSE,
            is_isnew BOOLEAN NOT NULL DEFAULT FALSE,

            PRIMARY KEY (validation_rule_entity_id, field_entity_id, reference_type),

            CONSTRAINT validation_rule_field_refs_type_known CHECK (
                reference_type IN ('read', 'priorvalue', 'ischanged', 'isnew')
            )
        )
    """)

    op.execute("""
        CREATE INDEX idx_validation_rule_field_refs_field
            ON validation_rule_field_refs(field_entity_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS validation_rule_field_refs CASCADE")
    op.execute("DROP TABLE IF EXISTS validation_rule_details CASCADE")

"""Phase 1 tenant schema: add picklist_value_set_entity_id to field_details

Closes the source-column gap for HAS_PICKLIST_VALUES derived edge (D-019).

Each picklist-typed Field in Salesforce has exactly one PicklistValueSet
(the set of allowed values, e.g., a global value set or an inline-defined
set). Cardinality is 1:1 — column on field_details is the right shape, no
join table needed (unlike RecordType -> PicklistValueSet which is 1:many
and gets its own hot reference table in 8B).

NULLABLE because the column is only populated for picklist-typed fields.
Most fields (Text, Number, Date, etc.) have no picklist value set; they
get NULL.

Pydantic FieldDetails-row validators (sync engine, Phase 2) will enforce
the type-vs-FK coupling: picklist_value_set_entity_id must be NULL when
field_type NOT IN ('picklist', 'multipicklist'); must be non-NULL when
field_type IN ('picklist', 'multipicklist'). Not enforced as DB CHECK
because the field_type list is a moving target (Salesforce introduces
new types) and Pydantic is more flexible to evolve.

Index: partial on NOT NULL — supports HAS_PICKLIST_VALUES derivation
("for set X, which fields use it?") and the much more common reverse
("for field X, what's its set?" — already served by the 1:1 lookup
through the column itself).

Revision ID: 20260427_0130
Revises: 20260427_0120
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0130"
down_revision: Union[str, Sequence[str], None] = "20260427_0120"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE field_details
        ADD COLUMN picklist_value_set_entity_id UUID
        REFERENCES entities(id)
    """)

    op.execute("""
        CREATE INDEX idx_field_details_picklist_set
            ON field_details(picklist_value_set_entity_id)
            WHERE picklist_value_set_entity_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_field_details_picklist_set")
    op.execute("ALTER TABLE field_details DROP COLUMN IF EXISTS picklist_value_set_entity_id")

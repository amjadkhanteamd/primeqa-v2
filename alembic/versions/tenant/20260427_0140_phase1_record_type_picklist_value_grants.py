"""Phase 1 tenant schema: record_type_picklist_value_grants (hot reference table)

Closes the source-row gap for the CONSTRAINS_PICKLIST_VALUES derived edge
per D-019. Second instance of the hot reference table pattern after
validation_rule_field_refs.

Salesforce's RecordType controls which subset of a PicklistValueSet is
available for record-type-controlled picklist fields on records of that
type. The relationship is per-(RecordType, allowed PicklistValue) — one
RecordType grants many specific values across potentially multiple
picklist value sets. Salesforce models this as RecordTypePicklistValue
junction records.

This is a hot reference table, NOT a D-025 detail table:
  - Not 1:1 with entities; one row per (rule, value) tuple
  - No entity_id PK — composite PK (record_type_entity_id, picklist_value_entity_id)
  - No JSONB attributes column; no Pydantic schema in entity_attributes.py
  - No registry entry in TIER_1_ENTITIES

Schema:
  record_type_entity_id     UUID NOT NULL — granting RecordType;
                            CASCADE on RecordType deletion (rule gone -> grants gone).
  picklist_value_entity_id  UUID NOT NULL — granted PicklistValue;
                            NO CASCADE (deleting a referenced PicklistValue while
                            a RecordType still grants it is a real problem; FK
                            should block until the grant is removed).

PRIMARY KEY: (record_type_entity_id, picklist_value_entity_id)
  Composite key. Forward query "what values does RecordType X grant?" served
  by leading column. No separate forward-lookup index needed.

INDEX (picklist_value_entity_id):
  Reverse lookup: "which RecordTypes grant this value?" — symmetric to
  validation_rule_field_refs.field_entity_id. Primary motivating reverse
  query is impact analysis ("if I rename this PicklistValue, which
  RecordType grants are affected").

NOT included (intentional, mirroring validation_rule_field_refs):
  - is_default flag: Salesforce DOES allow a single value per RecordType per
    picklist field to be marked default. We do NOT include is_default here
    because it would make the row's identity (rule, value) insufficient —
    same RecordType could have only one default per field. If is_default
    becomes a hot query target, it gets promoted to a column or its own
    table at that point. For Tier 1, the existence of the grant is what's
    queryable.
  - reference_type / category enum: this table has only one semantic
    relationship type (granting), unlike validation_rule_field_refs which
    distinguishes read/priorvalue/ischanged/isnew. No CHECK constraint needed.
  - No Pydantic class — DB constraints sufficient.

Revision ID: 20260427_0140
Revises: 20260427_0130
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0140"
down_revision: Union[str, Sequence[str], None] = "20260427_0130"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE record_type_picklist_value_grants (
            record_type_entity_id UUID NOT NULL
                REFERENCES entities(id) ON DELETE CASCADE,

            picklist_value_entity_id UUID NOT NULL REFERENCES entities(id),

            PRIMARY KEY (record_type_entity_id, picklist_value_entity_id)
        )
    """)

    op.execute("""
        CREATE INDEX idx_record_type_picklist_value_grants_value
            ON record_type_picklist_value_grants(picklist_value_entity_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS record_type_picklist_value_grants CASCADE")

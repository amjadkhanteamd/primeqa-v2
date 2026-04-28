"""Phase 1 tenant schema: flow_details (per D-025, with Tier 2 reservation per SPEC §9)

Ninth detail table. Hot/queryable attributes for entity_type='Flow'.

Per D-025:
  (1) Per-entity-version: entity_id PK, FK to entities ON DELETE CASCADE.
  (2) Hot columns capture queryable Flow metadata.
  (3) No tenant_id column.

Per SPEC §9: "flow_details — Tier 1: existence + trigger; Tier 2 columns
reserved nullable." This is the first detail table to use the Tier 2
reservation pattern. parsed_logic and interpreted_at_capability_level
are reserved here so future Tier 2 rollout doesn't require schema
changes; Tier 1 sync writes them as NULL or 'tier_1', Tier 2 sync
upgrades them to populated/'tier_2' as flows get parsed.

Behavior FK (NOT containment):
  triggers_on_object_entity_id UUID NULLABLE — for record-triggered flows,
  the Object whose record changes fire the flow. NULL for autolaunched/
  screen/scheduled flows. Drives the TRIGGERS_ON edge per D-019, which
  is BEHAVIOR-category (not STRUCTURAL containment) — Flow doesn't
  belong to Object the way Field does. Same shape as
  user_details.profile_entity_id (assignment-FK driving HAS_PROFILE
  PERMISSION-category edge), just under a different category name.

Hot columns (Tier 1):
  triggers_on_object_entity_id  UUID NULLABLE
  flow_type        VARCHAR(40) NOT NULL — Salesforce flow types:
                   'Flow', 'AutoLaunchedFlow', 'CustomEvent',
                   'InvocableProcess', 'PlatformEvent', 'RecordBeforeSave',
                   'RecordAfterSave', 'Screen', 'Workflow', etc. Heavily
                   filtered.
  trigger_type     VARCHAR(40) NULLABLE — 'BeforeSave', 'AfterSave',
                   'BeforeDelete', 'AfterDelete' for record-triggered
                   flows; NULL otherwise. Duplicated with TriggersOnProperties
                   on the TRIGGERS_ON edge intentionally: (1) flows
                   without trigger objects have no TRIGGERS_ON edge, so
                   filtering via edge JOIN excludes them rather than
                   returning NULL — column gives consistent semantics;
                   (2) generation paths benefit from filtering trigger_type
                   without JOIN; (3) the duplication is acceptable
                   denormalization, not the kind D-017 warns against
                   (the edge property is the canonical storage; the
                   column is a denormalized projection).
  is_active        BOOLEAN — Salesforce flow versions activate/deactivate;
                   only active versions execute. Heavily filtered.
  version_number   INT NULLABLE — currently active Salesforce version of
                   this flow. Per Q3 modeling call: one entity per flow
                   API name (not per version), with this column tracking
                   which Salesforce version is currently active. NULL for
                   legacy/aggregate records that don't carry version info.

Tier 2 reserved columns (nullable; populated by future Tier 2 sync):
  parsed_logic                       JSONB NULLABLE — structured parse of
                                     the flow body (decisions, assignments,
                                     record updates, etc.). Schema is
                                     FlowParsedLogic Pydantic, deferred
                                     to Tier 2 work.
  interpreted_at_capability_level    VARCHAR(10) NULLABLE — values:
                                     'tier_1' (just trigger info),
                                     'tier_2' (full parsing). Track which
                                     capability tier last touched this row.

JSONB attributes (sparse, in entities.attributes via FlowAttributes):
  description, process_type (legacy compat), entry_condition_text (raw
  text of entry condition formula; Tier 2 will parse it).

Indexes:
  - triggers_on_object_entity_id partial WHERE NOT NULL — TRIGGERS_ON
    derivation source; partial because many flows have no trigger object.
  - flow_type plain — heavily filtered.
  - is_active partial WHERE TRUE — active filter dominant.
  - composite (triggers_on_object_entity_id, is_active) WHERE
    triggers_on_object_entity_id IS NOT NULL — supports "active flows
    that trigger on Object X" common shape; partial both because the
    FK column is nullable and because the active flag is dominant filter.

Revision ID: 20260427_0110
Revises: 20260427_0100
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0110"
down_revision: Union[str, Sequence[str], None] = "20260427_0100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE flow_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            triggers_on_object_entity_id UUID REFERENCES entities(id),

            flow_type VARCHAR(40) NOT NULL,
            trigger_type VARCHAR(40),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            version_number INT,

            parsed_logic JSONB,
            interpreted_at_capability_level VARCHAR(10),

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT flow_details_capability_level_known CHECK (
                interpreted_at_capability_level IS NULL
                OR interpreted_at_capability_level IN ('tier_1', 'tier_2')
            ),
            CONSTRAINT flow_details_parsed_logic_is_object CHECK (
                parsed_logic IS NULL OR jsonb_typeof(parsed_logic) = 'object'
            )
        )
    """)

    op.execute("""
        CREATE INDEX idx_flow_details_triggers_on
            ON flow_details(triggers_on_object_entity_id)
            WHERE triggers_on_object_entity_id IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX idx_flow_details_type
            ON flow_details(flow_type)
    """)

    op.execute("""
        CREATE INDEX idx_flow_details_active
            ON flow_details(entity_id)
            WHERE is_active = TRUE
    """)

    op.execute("""
        CREATE INDEX idx_flow_details_triggers_active
            ON flow_details(triggers_on_object_entity_id, is_active)
            WHERE triggers_on_object_entity_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS flow_details CASCADE")

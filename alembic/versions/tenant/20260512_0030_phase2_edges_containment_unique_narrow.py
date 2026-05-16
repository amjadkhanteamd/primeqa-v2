"""Phase 2 tenant schema: narrow idx_edges_unique_containment to BELONGS_TO only.

Per PHASE_2_PLAN_corrections.md §12. Substrate-1's
20260427_0020_phase1_edges.py created idx_edges_unique_containment with
partial filter `WHERE edge_category = 'STRUCTURAL'`. The migration
comment described the intent: "Prevents duplicate BELONGS_TO entries
for the same source at the same version."

The filter over-generalized. Both BELONGS_TO and HAS_RELATIONSHIP_TO
are STRUCTURAL category in TIER_1_EDGES. HAS_RELATIONSHIP_TO is
multi-target by design — polymorphic references in Salesforce
(Task.WhoId → Contact|Lead, Lead.OwnerId → User|Group,
Task.WhatId → Account|Opportunity|Case|...) need multiple
HAS_RELATIONSHIP_TO edges from one source Field at one
valid_from_seq. The broad filter blocked these architecturally-
correct writes with UniqueViolation, surfaced by the Field
phase's live test.

Narrows the filter to `WHERE edge_type = 'BELONGS_TO'`. Captures
the original containment-uniqueness intent precisely. Active-
edge uniqueness for all edge types is still enforced by the
existing idx_edges_unique_active_non_references which keys on
(source_entity_id, target_entity_id, edge_type) WHERE
valid_to_seq IS NULL AND edge_type != 'REFERENCES' — correctly
distinguishing multi-target edges by their distinct
target_entity_id values.

This is a substrate-1 schema correctness fix discovered through
sync-layer work but applicable independently. Future cycles
adding STRUCTURAL edge types should reference §12 to determine
whether the new edge type fits the containment-uniqueness
pattern (single-target like BELONGS_TO) or needs separate
handling (multi-target like HAS_RELATIONSHIP_TO).

Revision ID: 20260512_0030
Revises: 20260512_0020
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260512_0030"
down_revision: Union[str, Sequence[str], None] = "20260512_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DROP INDEX idx_edges_unique_containment
    """)
    op.execute("""
        CREATE UNIQUE INDEX idx_edges_unique_containment
            ON edges(source_entity_id, edge_type, valid_from_seq)
            WHERE edge_type = 'BELONGS_TO'
    """)


def downgrade() -> None:
    # Restore the prior state exactly: partial UNIQUE on STRUCTURAL
    # category. Required so down-migrations match what 20260427_0020
    # established.
    op.execute("""
        DROP INDEX idx_edges_unique_containment
    """)
    op.execute("""
        CREATE UNIQUE INDEX idx_edges_unique_containment
            ON edges(source_entity_id, edge_type, valid_from_seq)
            WHERE edge_category = 'STRUCTURAL'
    """)

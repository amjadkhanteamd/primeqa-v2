"""Phase 2 tenant schema: clean up orphaned summary rows in
   ai_enrichment_queue.

§23 enrichment-worker cycle. Before the sync-engine filter
(SUMMARY_ENABLED_ENTITY_TYPES, this cycle's main commit) the sync
layer enqueued a `summary` primitive row for ALL 11 entity types.
Only flow_details and validation_rule_details have summary_text +
summary_embedding storage (P8 migration 20260514_0010) — a `summary`
row for any other entity type has no destination and would sit
pending forever (or churn through failed_retryable).

This migration deletes those orphans. Idempotent: DELETEs 0 rows on
a schema that never had the over-broad enqueue (e.g. a fresh tenant
provisioned after the sync-engine filter shipped). After this runs,
`summary` rows exist only for Flow + ValidationRule entities.

No downgrade restore — the deleted rows had no storage destination,
so there is nothing meaningful to recreate. (A later re-sync will
re-enqueue the correct, filtered set anyway.)

Revision ID: 20260514_0020
Revises: 20260514_0010
Create Date: 2026-05-14
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260514_0020"
down_revision: Union[str, Sequence[str], None] = "20260514_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM ai_enrichment_queue
        WHERE primitive_type = 'summary'
          AND entity_type NOT IN ('Flow', 'ValidationRule')
    """)


def downgrade() -> None:
    # No restore — the deleted rows had no summary_text storage
    # destination. A re-sync re-enqueues the correct filtered set.
    pass

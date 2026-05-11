"""Phase 2 tenant schema: summary columns on validation_rule_details and flow_details (per Phase 2 plan §3.5)

Adds 5 nullable summary-related columns plus 1 ivfflat index to each of
the two detail tables that need plain-English summaries.

Per D-044 (Anthropic Haiku 4.5 for summaries), D-045 (summaries as columns
on the detail table itself, NOT a separate entity_interpretations table),
D-048 (graceful fallback — NULL summary acceptable when generation fails).

Schema additions per table (validation_rule_details and flow_details
receive identical structure):

  plain_english_summary  TEXT          NULL
  summary_model          VARCHAR(50)   NULL — model that produced the summary
  summary_prompt_version VARCHAR(20)   NULL — prompt template version
  summary_generated_at   TIMESTAMPTZ   NULL — when summary was produced
  summary_embedding      VECTOR(1536)  NULL — embedding of the summary text
  ivfflat index on summary_embedding using vector_cosine_ops, lists=100.

All 5 columns are nullable. NULL means generation failed or hasn't run.
This is intentional per D-048: the sync write succeeds even when AI
primitive generation fails for a given row; the row is queryable for
non-AI-features regardless.

Why the embedding here (not just on entities.embedding):
  entities.embedding (added in 1D) is the canonical embedding of the
  entity's semantic_text. For ValidationRule and Flow rows, the
  plain_english_summary is a separate AI-generated artifact — its
  embedding belongs alongside it on the detail table, not on the entity
  row. The two embeddings answer different similarity questions.

Index parameters:
  lists = 100 matches 1D's entities_embedding_idx; same sqrt-of-rows
  rule of thumb targeting ~10K-100K rows.

Revision ID: 20260430_0040
Revises: 20260430_0030
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260430_0040"
down_revision: Union[str, Sequence[str], None] = "20260430_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # validation_rule_details: 5 columns + 1 ivfflat index
    op.execute("ALTER TABLE validation_rule_details ADD COLUMN plain_english_summary TEXT")
    op.execute("ALTER TABLE validation_rule_details ADD COLUMN summary_model VARCHAR(50)")
    op.execute("ALTER TABLE validation_rule_details ADD COLUMN summary_prompt_version VARCHAR(20)")
    op.execute("ALTER TABLE validation_rule_details ADD COLUMN summary_generated_at TIMESTAMPTZ")
    op.execute("ALTER TABLE validation_rule_details ADD COLUMN summary_embedding VECTOR(1536)")

    op.execute("""
        CREATE INDEX validation_rule_summary_embedding_idx
            ON validation_rule_details USING ivfflat (summary_embedding vector_cosine_ops)
            WITH (lists = 100)
    """)

    # flow_details: 5 columns + 1 ivfflat index (parallel to validation_rule_details)
    op.execute("ALTER TABLE flow_details ADD COLUMN plain_english_summary TEXT")
    op.execute("ALTER TABLE flow_details ADD COLUMN summary_model VARCHAR(50)")
    op.execute("ALTER TABLE flow_details ADD COLUMN summary_prompt_version VARCHAR(20)")
    op.execute("ALTER TABLE flow_details ADD COLUMN summary_generated_at TIMESTAMPTZ")
    op.execute("ALTER TABLE flow_details ADD COLUMN summary_embedding VECTOR(1536)")

    op.execute("""
        CREATE INDEX flow_summary_embedding_idx
            ON flow_details USING ivfflat (summary_embedding vector_cosine_ops)
            WITH (lists = 100)
    """)


def downgrade() -> None:
    # Reverse order: flow first (covered by prompt's downgrade ordering)
    op.execute("DROP INDEX flow_summary_embedding_idx")
    op.execute("ALTER TABLE flow_details DROP COLUMN summary_embedding")
    op.execute("ALTER TABLE flow_details DROP COLUMN summary_generated_at")
    op.execute("ALTER TABLE flow_details DROP COLUMN summary_prompt_version")
    op.execute("ALTER TABLE flow_details DROP COLUMN summary_model")
    op.execute("ALTER TABLE flow_details DROP COLUMN plain_english_summary")

    op.execute("DROP INDEX validation_rule_summary_embedding_idx")
    op.execute("ALTER TABLE validation_rule_details DROP COLUMN summary_embedding")
    op.execute("ALTER TABLE validation_rule_details DROP COLUMN summary_generated_at")
    op.execute("ALTER TABLE validation_rule_details DROP COLUMN summary_prompt_version")
    op.execute("ALTER TABLE validation_rule_details DROP COLUMN summary_model")
    op.execute("ALTER TABLE validation_rule_details DROP COLUMN plain_english_summary")

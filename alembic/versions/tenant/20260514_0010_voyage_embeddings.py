"""Phase 2 tenant schema: Voyage embedding dimensions (1536 -> 1024)
   + summary_text columns on flow_details / validation_rule_details.

Enrichment-worker cycle precursor (P8). Two schema changes:

1. Vector dimension 1536 -> 1024.
   The original entities.embedding / *_summary_embedding columns were
   sized 1536 for the informally-anticipated OpenAI
   text-embedding-3-small. Per D-049 the enrichment worker uses the
   Voyage AI API (model voyage-3) which emits 1024-dim vectors. Every
   one of these columns is currently NULL across all rows — the
   enrichment worker hasn't shipped yet — so the ALTER ... USING NULL
   is a pure type change with no data conversion.

   pgvector ivfflat indexes are dimension-bound: an index built on a
   vector(1536) column blocks ALTER COLUMN TYPE. So the three ivfflat
   indexes are DROPped first and reCREATEd at the new dimension after
   the ALTER. Index names + opclass + lists parameter are preserved
   verbatim from the originals (entities_embedding_idx,
   flow_summary_embedding_idx, validation_rule_summary_embedding_idx —
   all ivfflat / vector_cosine_ops / lists=100).

2. summary_text columns.
   flow_details and validation_rule_details already carry
   summary_embedding + summary_model + summary_prompt_version +
   summary_generated_at, but had no column for the summary TEXT
   itself (gap surfaced in the enrichment-worker survey, Q2). Added
   here, nullable. Summary scope is deliberately Flow + ValidationRule
   for v1 — the other nine entity types' detail tables do NOT receive
   a summary_text column this cycle; they get embedding-only
   enrichment.

Revision ID: 20260514_0010
Revises: 20260512_0040
Create Date: 2026-05-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260514_0010"
down_revision: Union[str, Sequence[str], None] = "20260512_0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop the ivfflat indexes — they are bound to vector(1536) and
    #    would block the ALTER COLUMN TYPE below.
    op.execute("DROP INDEX IF EXISTS entities_embedding_idx")
    op.execute("DROP INDEX IF EXISTS flow_summary_embedding_idx")
    op.execute(
        "DROP INDEX IF EXISTS validation_rule_summary_embedding_idx"
    )

    # 2. Re-type the vector columns 1536 -> 1024. USING NULL: every
    #    row is already NULL, so this is a pure metadata change with
    #    no per-row cast.
    op.execute(
        "ALTER TABLE entities "
        "ALTER COLUMN embedding TYPE VECTOR(1024) USING NULL"
    )
    op.execute(
        "ALTER TABLE flow_details "
        "ALTER COLUMN summary_embedding TYPE VECTOR(1024) USING NULL"
    )
    op.execute(
        "ALTER TABLE validation_rule_details "
        "ALTER COLUMN summary_embedding TYPE VECTOR(1024) USING NULL"
    )

    # 3. Add the summary_text columns (Flow + ValidationRule only).
    op.add_column(
        "flow_details",
        sa.Column("summary_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "validation_rule_details",
        sa.Column("summary_text", sa.Text(), nullable=True),
    )

    # 4. Recreate the ivfflat indexes at the new dimension. Same
    #    name / opclass / lists as the originals.
    op.execute(
        "CREATE INDEX entities_embedding_idx ON entities "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX flow_summary_embedding_idx ON flow_details "
        "USING ivfflat (summary_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX validation_rule_summary_embedding_idx "
        "ON validation_rule_details "
        "USING ivfflat (summary_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    # Reverse order: drop new-dim indexes, drop summary_text, re-type
    # back to 1536, recreate the original-dim indexes.
    op.execute("DROP INDEX IF EXISTS entities_embedding_idx")
    op.execute("DROP INDEX IF EXISTS flow_summary_embedding_idx")
    op.execute(
        "DROP INDEX IF EXISTS validation_rule_summary_embedding_idx"
    )

    op.drop_column("validation_rule_details", "summary_text")
    op.drop_column("flow_details", "summary_text")

    op.execute(
        "ALTER TABLE entities "
        "ALTER COLUMN embedding TYPE VECTOR(1536) USING NULL"
    )
    op.execute(
        "ALTER TABLE flow_details "
        "ALTER COLUMN summary_embedding TYPE VECTOR(1536) USING NULL"
    )
    op.execute(
        "ALTER TABLE validation_rule_details "
        "ALTER COLUMN summary_embedding TYPE VECTOR(1536) USING NULL"
    )

    op.execute(
        "CREATE INDEX entities_embedding_idx ON entities "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX flow_summary_embedding_idx ON flow_details "
        "USING ivfflat (summary_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX validation_rule_summary_embedding_idx "
        "ON validation_rule_details "
        "USING ivfflat (summary_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )

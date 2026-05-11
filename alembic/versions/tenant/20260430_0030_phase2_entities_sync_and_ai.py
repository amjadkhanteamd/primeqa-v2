"""Phase 2 tenant schema: entities ALTER for sync-state and AI primitives.

Per Phase 2 plan §3.4. Largest single Phase 2 migration: 7 new columns,
3 CHECK constraints, 1 cross-table FK, and the first ivfflat index in
the codebase using the vector type from pgvector.

New columns (in order applied):

  Sync-state (3):
    entity_origin VARCHAR(20) NOT NULL DEFAULT 'sync' (D-031)
      — forward-compat: future Phase 3+ paths can write 'requirements'
        or 'manual_curation'. Phase 2 only writes 'sync'.
    last_seed_hash VARCHAR(64) (D-032)
      — SHA-256 hex of normalized entity content. Used for diffing on
        subsequent syncs. NULL for non-sync origins.
    last_synced_from_org_id UUID REFERENCES connected_orgs(id) (D-040)
      — per-entity provenance: which connected_org most recently
        sourced this entity. NULL for non-sync origins.

  AI primitives (4):
    semantic_text TEXT (D-046)
      — deterministic NL representation of the entity, generated from
        structured data via templating. Input to embedding generation.
        Stored for transparency and debugging.
    embedding VECTOR(1536) (D-042, D-043)
      — 1536-dim matches OpenAI text-embedding-3-small.
    embedding_model VARCHAR(50)
      — model identifier per row, e.g.
        'openai/text-embedding-3-small'. Forward-compat for swaps.
    embedding_generated_at TIMESTAMPTZ
      — staleness tracking timestamp.

CHECK constraints (3):

  entities_entity_origin_known: enum on entity_origin
  entities_hash_only_for_sync: last_seed_hash may be non-NULL only
    when entity_origin = 'sync'
  entities_synced_from_only_for_sync: last_synced_from_org_id may be
    non-NULL only when entity_origin = 'sync'

ivfflat index:
  CREATE INDEX entities_embedding_idx ON entities
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)

  lists=100 targets ~10K-100K rows per ivfflat's sqrt-of-rows rule
  of thumb. Tunable later; switch to hnsw a future-phase choice
  (per O-8 resolution, ivfflat for Phase 2 simplicity).

  The CREATE INDEX on an empty embedding column succeeds; ivfflat
  trains on rows present at index-build time. After sync populates
  entities, a REINDEX or post-population rebuild may be desirable
  for centroid quality.

DEFAULT 'sync' on entity_origin: existing entities rows from Phase 1
(test residue or otherwise) get backfilled to 'sync' transparently.
That's intentional — Phase 1 entities were sync-sourced by intent
even though the column did not exist then.

Revision ID: 20260430_0030
Revises: 20260430_0020
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260430_0030"
down_revision: Union[str, Sequence[str], None] = "20260430_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # entity_origin column + CHECK
    op.execute("""
        ALTER TABLE entities
            ADD COLUMN entity_origin VARCHAR(20) NOT NULL DEFAULT 'sync'
    """)
    op.execute("""
        ALTER TABLE entities
            ADD CONSTRAINT entities_entity_origin_known CHECK (
                entity_origin IN ('sync', 'requirements', 'manual_curation')
            )
    """)

    # last_seed_hash column + conditional CHECK
    op.execute("""
        ALTER TABLE entities
            ADD COLUMN last_seed_hash VARCHAR(64)
    """)
    op.execute("""
        ALTER TABLE entities
            ADD CONSTRAINT entities_hash_only_for_sync CHECK (
                (entity_origin = 'sync') OR (last_seed_hash IS NULL)
            )
    """)

    # last_synced_from_org_id column with FK + conditional CHECK
    op.execute("""
        ALTER TABLE entities
            ADD COLUMN last_synced_from_org_id UUID REFERENCES connected_orgs(id)
    """)
    op.execute("""
        ALTER TABLE entities
            ADD CONSTRAINT entities_synced_from_only_for_sync CHECK (
                (entity_origin = 'sync') OR (last_synced_from_org_id IS NULL)
            )
    """)

    # AI primitive columns
    op.execute("ALTER TABLE entities ADD COLUMN semantic_text TEXT")
    op.execute("ALTER TABLE entities ADD COLUMN embedding VECTOR(1536)")
    op.execute("ALTER TABLE entities ADD COLUMN embedding_model VARCHAR(50)")
    op.execute("ALTER TABLE entities ADD COLUMN embedding_generated_at TIMESTAMPTZ")

    # ivfflat similarity-search index
    op.execute("""
        CREATE INDEX entities_embedding_idx
            ON entities USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX entities_embedding_idx")
    op.execute("ALTER TABLE entities DROP COLUMN embedding_generated_at")
    op.execute("ALTER TABLE entities DROP COLUMN embedding_model")
    op.execute("ALTER TABLE entities DROP COLUMN embedding")
    op.execute("ALTER TABLE entities DROP COLUMN semantic_text")
    op.execute("ALTER TABLE entities DROP CONSTRAINT entities_synced_from_only_for_sync")
    op.execute("ALTER TABLE entities DROP COLUMN last_synced_from_org_id")
    op.execute("ALTER TABLE entities DROP CONSTRAINT entities_hash_only_for_sync")
    op.execute("ALTER TABLE entities DROP COLUMN last_seed_hash")
    op.execute("ALTER TABLE entities DROP CONSTRAINT entities_entity_origin_known")
    op.execute("ALTER TABLE entities DROP COLUMN entity_origin")

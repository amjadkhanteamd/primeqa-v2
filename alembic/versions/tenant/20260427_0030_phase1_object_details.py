"""Phase 1 tenant schema: object_details (per D-025)

First detail table. Hot/queryable attributes for entity_type='Object'.

Per D-025:
  (1) Per-entity-version: entity_id is PRIMARY KEY, FK to entities.id ON DELETE
      CASCADE. One row per entity row. No own bitemporal columns.
  (2) Hot columns only. Sparse/lightweight Object metadata
      (is_searchable, is_layoutable, etc.) lives in entities.attributes JSONB,
      validated by ObjectAttributes Pydantic schema.
  (3) No tenant_id column (per D-018 — only canonical tables carry it).

Hot columns chosen:
  - key_prefix      VARCHAR(5) — used by diff queries to identify
                                 standard vs custom Salesforce objects
                                 (custom objects always start with a code
                                 prefix; e.g., '001' = Account, 'a00' = custom)
  - is_custom       BOOLEAN     — heavily filtered in generation paths
                                 ("custom objects only" / "standard only")
  - is_queryable    BOOLEAN     — generation needs to know SOQL is valid
  - is_createable   BOOLEAN     — generation needs to know INSERT is valid
  - is_updateable   BOOLEAN     — generation needs to know UPDATE is valid
  - is_deletable    BOOLEAN     — generation needs to know DELETE is valid

Defaults match Salesforce's typical assumptions: standard objects are
queryable + createable + updateable + deletable; custom objects often
match (with exceptions). The defaults are a starting point, not an
assumption — every row's actual value comes from Salesforce's
DescribeSObjectResult.

Revision ID: 20260427_0030
Revises: 20260427_0020
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0030"
down_revision: Union[str, Sequence[str], None] = "20260427_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE object_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            key_prefix VARCHAR(5),
            is_custom BOOLEAN NOT NULL DEFAULT FALSE,
            is_queryable BOOLEAN NOT NULL DEFAULT TRUE,
            is_createable BOOLEAN NOT NULL DEFAULT TRUE,
            is_updateable BOOLEAN NOT NULL DEFAULT TRUE,
            is_deletable BOOLEAN NOT NULL DEFAULT TRUE,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # is_custom is heavily filtered ("show me custom objects" / "standard only").
    # Partial index on TRUE (custom) since custom objects are the smaller
    # population in most orgs and the predicate "WHERE is_custom" is more
    # common than "WHERE NOT is_custom".
    op.execute("""
        CREATE INDEX idx_object_details_custom
            ON object_details(entity_id)
            WHERE is_custom = TRUE
    """)

    # key_prefix is used by diff queries that want to scope to specific
    # object families (e.g., "all objects starting with key_prefix '00Q'"
    # = leads + opportunities + cases + ... ; NULL prefix = some
    # virtual objects). Index supports those scans.
    op.execute("""
        CREATE INDEX idx_object_details_key_prefix
            ON object_details(key_prefix)
            WHERE key_prefix IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS object_details CASCADE")

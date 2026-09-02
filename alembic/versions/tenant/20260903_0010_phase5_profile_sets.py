"""Phase 5 Part 3 — the customer profile set, tenant schema
(LLD_PHASE5_AUTHORING §g lean, ratified; the Part 2 residual).

`CUSTOM:<profile>` is the tenant's own standard-like set: its "criteria"
are the customer's guideline HEADINGS, ratified through the same set
lifecycle discipline as the platform standard sets — DRAFT content,
frozen from REVIEW, one content hash at APPROVED, single-ACTIVE per
profile key. A custom rule maps a heading through its OWN ratified
content (`definition.criterion.profile`), so the join at render time
reads two ratified records and derives nothing (D-281 posture).

Two tables:

  cust_profile_sets     — the lifecycle object per (profile_key, revision)
  cust_profile_criteria — the ratified heading list (the denominator)

Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260903_0010"
down_revision = "20260902_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE cust_profile_sets (
            id               BIGSERIAL PRIMARY KEY,
            profile_key      TEXT NOT NULL,
            revision         INTEGER NOT NULL DEFAULT 1,
            state            TEXT NOT NULL DEFAULT 'DRAFT'
                CONSTRAINT cust_profile_sets_state_known
                CHECK (state IN ('DRAFT','REVIEW','APPROVED','ACTIVE',
                                 'RETIRED')),
            notes            TEXT NOT NULL DEFAULT '',
            provenance       JSONB NOT NULL DEFAULT '{}'::jsonb,
            content_hash     CHAR(64),
            created_by       INTEGER NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_by      INTEGER,
            reviewed_at      TIMESTAMPTZ,
            activated_at     TIMESTAMPTZ,
            CONSTRAINT cust_profile_sets_key_shape
                CHECK (profile_key ~ '^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$'),
            CONSTRAINT cust_profile_sets_unique
                UNIQUE (profile_key, revision)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX cust_profile_sets_single_active
            ON cust_profile_sets (profile_key) WHERE state = 'ACTIVE'
    """)
    op.execute("""
        CREATE TABLE cust_profile_criteria (
            id         BIGSERIAL PRIMARY KEY,
            set_id     BIGINT NOT NULL REFERENCES cust_profile_sets (id),
            criterion  TEXT NOT NULL,       -- the guideline HEADING, verbatim
            title      TEXT NOT NULL DEFAULT '',
            ordinal    INTEGER NOT NULL,
            CONSTRAINT cust_profile_criteria_unique UNIQUE (set_id, criterion)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cust_profile_criteria")
    op.execute("DROP TABLE IF EXISTS cust_profile_sets")

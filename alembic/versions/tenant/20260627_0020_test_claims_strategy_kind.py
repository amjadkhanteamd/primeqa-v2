"""S6 bva: strategy_kind column on test_claims.

Revision ID: 20260627_0020
Revises: 20260627_0010
Create Date: 2026-06-27

D-285 (S6 build, Slice 4f.0). Adds the column that lets a claim affirmatively
RECORD its evaluation strategy — the one missing piece of the otherwise-built bva
pipeline. Today the router (``run.py`` ``_recorded_strategy_kind``) and the
decision engine (``substrate_decision.py`` ``_claim_verified``) already READ a
top-level ``strategy_kind`` off the claim and harmlessly get ``None`` (a getattr
default); this column persists the real value so generation can SET it (later,
Slice 4f.2).

  * ``test_claims.strategy_kind VARCHAR`` (nullable; the recorded evaluation
    strategy — open text, NO CHECK, code owns the vocabulary: ``single`` / ``bva``;
    a CHECK would force a migration to admit a future strategy. NULL on every
    existing claim and every claim authored before 4f.2 — the router reads NULL →
    routes single, byte-identical to today.)

Fork C (4f recon): a DEDICATED column, persisted here + exposed via ``ClaimRead``;
NEVER derived at read time (the readers READ, never DERIVE — the router-test
discipline), and NOT folded into ``asserted_truth`` JSONB (which carries a
key-presence CHECK constraint).

Lives on the bitemporal claim-version row (PK ``(test_id, version_seq)``): a new
claim version carries its strategy; existing versions stay NULL. Additive,
nullable, NO default, NO backfill — a pure ADD COLUMN (non-locking on Postgres
for nullable-no-default; zero rows rewritten). Idempotent (ADD COLUMN IF NOT
EXISTS) per the migrations-016+ convention. No index this slice (no reader filters
by strategy_kind yet). DORMANT: nothing writes ``'bva'`` until generation sets it
in 4f.2.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260627_0020'
down_revision = '20260627_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE test_claims "
        "ADD COLUMN IF NOT EXISTS strategy_kind VARCHAR")
    op.execute(
        "COMMENT ON COLUMN test_claims.strategy_kind IS "
        "'D-285 (S6 bva, Slice 4f.0): the claim''s recorded evaluation strategy "
        "— open text, no CHECK (code owns the vocabulary: single / bva). "
        "Set ONCE by generation (Slice 4f.2) from the claim''s constraint shape; "
        "READ (never derived) by the run router and the decision engine. NULL on "
        "every claim authored before 4f.2 → routes single, byte-identical to "
        "today. Fork C: dedicated column, NOT folded into asserted_truth JSONB.'")


def downgrade():
    op.execute("ALTER TABLE test_claims DROP COLUMN IF EXISTS strategy_kind")

"""S6 run-all: batch manifest table (expected applicable recipe set, C2).

Revision ID: 20260627_0010
Revises: 20260626_0010
Create Date: 2026-06-27

D-281 (S6 build, Slice 4c.0). Records, per run-all invocation, the EXPECTED
applicable recipe set as it was AT BATCH TIME — the membership the completeness
check (Slice 4c.1) compares the persisted probe-rows against. **C2** (persist the
expected membership) over an env-key: ``select_recipes_for_execution`` depends on
the approved-claim version, the recipe ``valid_to`` / status / priority AND the
environment — four of which are MUTABLE after the batch runs, so recomputing the
applicable set at read time (even pinned to an env-key) is NOT drift-immune.
Recording the actual expected ``recipe_id`` set makes completeness a pure
set-compare against persisted data.

  * ``batch_id (UUID) PRIMARY KEY``           — the run-all invocation id (shared
                                                with ``s4_execution_runs.batch_id``)
  * ``claim_test_id (UUID) NOT NULL``         — the claim this batch verifies
  * ``expected_recipe_ids (UUID[]) NOT NULL`` — the applicable set at batch mint
  * ``env_key (VARCHAR) NULL``                — optional audit metadata; NOT
                                                load-bearing (C2 membership
                                                subsumes the env)
  * ``created_at (TIMESTAMPTZ) NOT NULL``

Pure additive CREATE TABLE — no touch to existing tables/rows, no backfill. No
index this slice (4c.1 reads by ``batch_id`` = PK, already indexed). Idempotent
(``CREATE TABLE IF NOT EXISTS``) per the migrations-016+ convention. DORMANT —
the producer write lives in the run-all loop, which nothing routes to until §4a.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260627_0010'
down_revision = '20260626_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE IF NOT EXISTS s4_runall_batch_manifests ("
        " batch_id UUID PRIMARY KEY,"
        " claim_test_id UUID NOT NULL,"
        " expected_recipe_ids UUID[] NOT NULL,"
        " env_key VARCHAR,"
        " created_at TIMESTAMPTZ NOT NULL)")
    op.execute(
        "COMMENT ON TABLE s4_runall_batch_manifests IS "
        "'D-281 (S6 run-all, Slice 4c.0): the EXPECTED applicable recipe set per "
        "run-all batch, recorded at batch mint and committed EARLY (before probes "
        "run). Completeness (4c.1) = every expected recipe_id has an "
        "s4_execution_runs row → a drift-immune set-compare (C2, membership over "
        "env-key). DORMANT until the run-all path becomes reachable (4a/4f).'")


def downgrade():
    op.execute("DROP TABLE IF EXISTS s4_runall_batch_manifests")

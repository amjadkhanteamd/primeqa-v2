"""Step 2 — contemporaneity: the RUN STAMP (LLD_STEP_2_CONTEMPORANEITY §a).

ADDITIVE on ``s4_execution_runs``: every NEW run records the org it ran
against and that org's logical sequence AT EXECUTION — the executor's own
value, captured once in the select bracket and carried on the prep
tuple (the world's pin and the stamp are one number by construction).

  connected_org_id   the run's org (the D-286 seam's answer)
  org_version_seq    the org's logical sequence the world was planned at

Both NULL on every legacy row (727 on tenant 1) and on a run whose
resolver refused (org unbound / never synced) — no backfill, no
inference (Fork 3): a run with no stamp reads CANNOT_DETERMINE.
The index serves the latest-run-per-(claim, environment) read that
readiness makes. Plain DDL in env.py's transaction (D-459).
"""
from alembic import op

revision = "20260909_0010"
down_revision = "20260908_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE s4_execution_runs
            ADD COLUMN IF NOT EXISTS connected_org_id UUID,
            ADD COLUMN IF NOT EXISTS org_version_seq INTEGER
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_s4_runs_claim_env_latest
            ON s4_execution_runs (claim_test_id, environment_id, finished_at DESC)
    """)
    op.execute("COMMENT ON COLUMN s4_execution_runs.org_version_seq IS "
               "'Step 2: the org logical sequence the run executed against — the "
               "executor''s own value from the select bracket. NULL = unstamped "
               "(legacy, or the resolver refused); never backfilled.'")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_s4_runs_claim_env_latest")
    op.execute("""
        ALTER TABLE s4_execution_runs
            DROP COLUMN IF EXISTS org_version_seq,
            DROP COLUMN IF EXISTS connected_org_id
    """)

"""Step A.1 — apply → re-verify at root (LLD_STEP_A1_REVERIFY §c).

ADDITIVE on ``repair_proposals``: the re-verify OUTCOME becomes part of
the proposal — apply is not "done" until re-verify has spoken.

  applied_recipe_version_seq   the version the apply wrote AND promoted
  reverify_job_id              the S4 job enqueued (or found active)
  reverify_state               queued | ran | no_run | refused
  reverify_run_id / _outcome / _verdict   the run (D-317 resolution)
  reverify_refusal             claim_deprecated | recipe_moved |
                               no_eligible_recipe | <job error_code>
  reverify_settled_at          when the state left 'queued'

NULL ``reverify_state`` = the pre-A.1 shape (never examined).
Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260907_0010"
down_revision = "20260906_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE repair_proposals
            ADD COLUMN IF NOT EXISTS applied_recipe_version_seq INTEGER,
            ADD COLUMN IF NOT EXISTS reverify_job_id INTEGER,
            ADD COLUMN IF NOT EXISTS reverify_state TEXT
                CONSTRAINT repair_proposals_reverify_state_known
                CHECK (reverify_state IS NULL OR reverify_state IN
                       ('queued', 'ran', 'no_run', 'refused')),
            ADD COLUMN IF NOT EXISTS reverify_run_id UUID,
            ADD COLUMN IF NOT EXISTS reverify_outcome TEXT,
            ADD COLUMN IF NOT EXISTS reverify_verdict TEXT,
            ADD COLUMN IF NOT EXISTS reverify_refusal TEXT,
            ADD COLUMN IF NOT EXISTS reverify_settled_at TIMESTAMPTZ
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_repair_proposals_reverify_queued
            ON repair_proposals (reverify_state) WHERE reverify_state = 'queued'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_repair_proposals_reverify_queued")
    op.execute("""
        ALTER TABLE repair_proposals
            DROP COLUMN IF EXISTS reverify_settled_at,
            DROP COLUMN IF EXISTS reverify_refusal,
            DROP COLUMN IF EXISTS reverify_verdict,
            DROP COLUMN IF EXISTS reverify_outcome,
            DROP COLUMN IF EXISTS reverify_run_id,
            DROP COLUMN IF EXISTS reverify_state,
            DROP COLUMN IF EXISTS reverify_job_id,
            DROP COLUMN IF EXISTS applied_recipe_version_seq
    """)

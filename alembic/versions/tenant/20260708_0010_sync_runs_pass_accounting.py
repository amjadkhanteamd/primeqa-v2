"""D-341: sync_runs pass accounting — attempt_passes + active_seconds.

A reaped-and-resumed run's wall clock (completed_at - started_at) includes
the reaper's dead time between passes (observed live 2026-07-08: 20m51s
shown for ~8m of actual work; 2026-07-07: 35m18s). ``attempt_passes``
counts the engine passes over the run (fresh + resumes); ``active_seconds``
accumulates per-pass wall time. Legacy rows keep attempt_passes=0, which
the console uses to fall back to the wall-clock duration.

MIGRATE-FIRST (D-285): apply before deploying the engine/console change.
"""
from alembic import op

revision = '20260708_0010'
down_revision = '20260707_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS "
        "attempt_passes INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS "
        "active_seconds INTEGER NOT NULL DEFAULT 0"
    )


def downgrade():
    op.execute("ALTER TABLE sync_runs DROP COLUMN IF EXISTS active_seconds")
    op.execute("ALTER TABLE sync_runs DROP COLUMN IF EXISTS attempt_passes")

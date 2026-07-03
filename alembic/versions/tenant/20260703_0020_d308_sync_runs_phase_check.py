"""D-308: widen sync_runs' last_completed_phase CHECK for 'ApprovalProcess'.

The companion of 20260703_0010 — the live re-sync surfaced the SECOND
closed phase-list CHECK: the ApprovalProcess PHASE succeeded (1 entity,
1 edge) but the run's own progress bookkeeping violated
``sync_runs_last_completed_phase_known``. A live census
(pg_get_constraintdef LIKE '%Flow%') confirms these two are the ONLY
CHECKs carrying the phase list.

MIGRATE-FIRST (D-285), same rationale as 0010.
"""
from alembic import op

revision = '20260703_0020'
down_revision = '20260703_0010'
branch_labels = None
depends_on = None

_OLD = ("'Object', 'PicklistValueSet', 'PicklistValue', 'Field', "
        "'RecordType', 'Layout', 'ValidationRule', 'Profile', "
        "'PermissionSet', 'User', 'FlowDefinition', 'Flow'")
_NEW = _OLD + ", 'ApprovalProcess'"


def upgrade():
    op.execute(
        "ALTER TABLE sync_runs "
        "DROP CONSTRAINT IF EXISTS sync_runs_last_completed_phase_known"
    )
    op.execute(
        f"ALTER TABLE sync_runs "
        f"ADD CONSTRAINT sync_runs_last_completed_phase_known "
        f"CHECK (last_completed_phase IS NULL OR "
        f"last_completed_phase IN ({_NEW}))"
    )


def downgrade():
    op.execute(
        "UPDATE sync_runs SET last_completed_phase = 'Flow' "
        "WHERE last_completed_phase = 'ApprovalProcess'"
    )
    op.execute(
        "ALTER TABLE sync_runs "
        "DROP CONSTRAINT IF EXISTS sync_runs_last_completed_phase_known"
    )
    op.execute(
        f"ALTER TABLE sync_runs "
        f"ADD CONSTRAINT sync_runs_last_completed_phase_known "
        f"CHECK (last_completed_phase IS NULL OR "
        f"last_completed_phase IN ({_OLD}))"
    )

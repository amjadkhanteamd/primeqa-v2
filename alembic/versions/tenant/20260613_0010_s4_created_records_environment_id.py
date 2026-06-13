"""substrate-4 durable cleanup: s4_created_records.environment_id (D-230, 2.4)

Revision ID: 20260613_0010
Revises: 20260611_0020
Create Date: 2026-06-13

Per DECISIONS_LOG.md D-230 (2.4 — durable data-path cleanup). The crash-recovery
reaper deletes a STRANDED Salesforce record (one a run created but never tore
down, because the run died mid-flight). To delete it the reaper must resolve the
right data-mutation client = the right environment — but a run that crashed
before finalize may have NO ``s4_execution_runs`` row to join for it. So the
created-record audit row carries its own ``environment_id``, written at create
time (write-ahead), making the reaper self-sufficient.

NULLABLE — existing rows (written pre-D-230 at finalize) have no environment_id;
the reaper skips a row whose environment_id is NULL (it cannot resolve a client),
which is correct: those rows are from completed runs that already tore down.

Tenant-branch migration; per-tenant table. Schema only — no behavior.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260613_0010'
down_revision = '20260611_0020'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        's4_created_records',
        sa.Column('environment_id', sa.Integer(), nullable=True),
    )
    op.execute(
        "COMMENT ON COLUMN s4_created_records.environment_id IS "
        "'D-230 (2.4): the environment the record was created against, written at "
        "create time (write-ahead) so the crash-recovery reaper can resolve the "
        "data-mutation client without an s4_execution_runs join. NULL on pre-D-230 "
        "rows (finalize-written) — the reaper skips those.'"
    )


def downgrade():
    op.drop_column('s4_created_records', 'environment_id')

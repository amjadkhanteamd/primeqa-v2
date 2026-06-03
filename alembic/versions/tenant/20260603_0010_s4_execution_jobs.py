"""substrate-4 execution-job queue: s4_execution_jobs + attempts (D-130, Phase 3 B1)

Revision ID: 20260603_0010
Revises: 20260601_0020
Create Date: 2026-06-03

Per DECISIONS_LOG.md D-130. The S4 execution service's job queue — the durable
per-tenant record of "execute recipe ``test_id`` on ``environment_id``", with the
claim -> attempt -> heartbeat -> reap lifecycle the B2 consumer drains. A
near-mechanical mirror of S3's ``s3_generation_jobs`` (D-106.4): per-tenant schema
(unqualified, **no tenant_id column** — isolation by schema), ``SELECT … FOR
UPDATE SKIP LOCKED`` claim, fresh-``request_id``-per-attempt child table.

Idempotency (D-130.A) DIFFERS from S3. S3 keys ``UNIQUE (requirement_key,
s1_version_seq)`` ("one job ever" — generation is deterministic). Execution is
legitimately **repeatable** (re-verification as the org changes), so S4 uses a
**partial-unique on the active set**: ``UNIQUE (test_id, environment_id) WHERE
status IN ('queued','claimed','running')`` — dedups a double-enqueue while a run
is pending, but allows a fresh job once the prior is terminal (v1's
"new-job-after-terminal" model, which S4 needs and S3 did not).

Tenant-branch migration; tables are per-tenant. Schema only — no behavior.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260603_0010'
down_revision = '20260601_0020'
branch_labels = None
depends_on = None


_ACTIVE = "status IN ('queued', 'claimed', 'running')"


def upgrade():
    # -----------------------------------------------------------------------
    # s4_execution_jobs — one unit of execution work ("run recipe test_id on
    # environment_id"); re-runnable (a fresh job after the prior is terminal).
    # -----------------------------------------------------------------------
    op.create_table(
        's4_execution_jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        # The S2 claim/recipe selection key (run_recipe_execution_async(test_id=…)).
        sa.Column('test_id', postgresql.UUID(as_uuid=True), nullable=False),
        # Which org to execute against — a v1-side environments.id (loose int, no
        # per-tenant FK target; the connection/credential resolves from it).
        sa.Column('environment_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='queued'),
        # The current attempt's tracking id (observability; the executor mints its
        # own run_id per run — this is the job-level attempt identity).
        sa.Column('current_request_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.CheckConstraint(
            "status IN ('queued', 'claimed', 'running', 'completed', 'failed', 'cancelled')",
            name='ck_s4_execution_jobs_status',
        ),
    )
    # Idempotency (D-130.A): one ACTIVE job per (test_id, environment_id); a new
    # job is allowed once the prior reaches a terminal state (re-run / re-verify).
    op.create_index(
        'uq_s4_execution_jobs_active',
        's4_execution_jobs',
        ['test_id', 'environment_id'],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
    )
    # Consumer work-find index: the active set only (the claim scans queued).
    op.create_index(
        'idx_s4_execution_jobs_status_active',
        's4_execution_jobs',
        ['status'],
        postgresql_where=sa.text(_ACTIVE),
    )
    op.execute(
        "COMMENT ON TABLE s4_execution_jobs IS "
        "'Substrate-4 execution-job queue (D-130). Per-tenant; mirrors the S3 "
        "generation-jobs lifecycle. Partial-unique on the active set — one active "
        "job per (test_id, environment_id), re-runnable after terminal.'"
    )

    # -----------------------------------------------------------------------
    # s4_execution_job_attempts — per-attempt history (observability-grade).
    # Each row is one run_recipe_execution_async; request_id is the fresh
    # per-attempt tracking id.
    # -----------------------------------------------------------------------
    op.create_table(
        's4_execution_job_attempts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('attempt_no', sa.Integer(), nullable=False),
        sa.Column('request_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='running'),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['job_id'], ['s4_execution_jobs.id'],
            name='fk_s4_execution_job_attempts_job', ondelete='CASCADE',
        ),
        sa.UniqueConstraint('job_id', 'attempt_no', name='uq_s4_execution_job_attempts_no'),
        sa.UniqueConstraint('request_id', name='uq_s4_execution_job_attempts_request_id'),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')",
            name='ck_s4_execution_job_attempts_status',
        ),
    )
    op.create_index(
        'idx_s4_execution_job_attempts_job_id',
        's4_execution_job_attempts',
        ['job_id'],
    )
    op.execute(
        "COMMENT ON TABLE s4_execution_job_attempts IS "
        "'Substrate-4 per-attempt history (D-130). One row per "
        "run_recipe_execution_async attempt; request_id is the per-attempt id.'"
    )


def downgrade():
    op.drop_index('idx_s4_execution_job_attempts_job_id', table_name='s4_execution_job_attempts')
    op.drop_table('s4_execution_job_attempts')
    op.drop_index('idx_s4_execution_jobs_status_active', table_name='s4_execution_jobs')
    op.drop_index('uq_s4_execution_jobs_active', table_name='s4_execution_jobs')
    op.drop_table('s4_execution_jobs')

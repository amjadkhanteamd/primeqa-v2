"""substrate-3 production integration: s3_generation_jobs + attempts (D-106.4 slice 1)

Revision ID: 20260525_0020
Revises: 20260525_0010
Create Date: 2026-05-25

Per DECISIONS_LOG.md D-106.4 (+ slice-1 amendment). The S3 service layer's job
queue: the async-work record wrapping the in-process ``run_generation`` core.

Placement (D-106.4 .A, PER-TENANT): mirrors the v1 ``generation_jobs``
*lifecycle* (claim -> heartbeat -> reap -> status), NOT its shared placement.
Lands in the per-tenant schema (alembic tenant branch, unqualified, **no
tenant_id column** — isolation by schema, the substrate-1/-2/-3 convention),
beside the ledger it tracks and where ``run_generation`` operates.

Idempotency (D-106.4 .B):
  - Layer 1: full ``UNIQUE (requirement_key, s1_version_seq)`` — one job per
    logical request, ever (S3 retries *in place* via ``start_attempt``; v1's
    app-enforced partial-unique solved a different "new job after terminal"
    model the S3 design does not use).
  - Layer 2: ``s3_generation_job_attempts`` owns per-attempt history (B-job).
    Each attempt mints a fresh ``request_id``; the ledger's
    ``generation_requests.prior_request_id`` stays reserved for *semantic*
    regeneration (so operational retries never leak into the semantic chain).

Tenant-branch migration; tables are per-tenant. Schema only — no behavior.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260525_0020'
down_revision = '20260525_0010'
branch_labels = None
depends_on = None


_JOB_STATUSES = ('queued', 'claimed', 'running', 'completed', 'failed', 'cancelled')
_ATTEMPT_STATUSES = ('running', 'completed', 'failed', 'cancelled')


def upgrade():
    # -----------------------------------------------------------------------
    # s3_generation_jobs — the logical unit of generation work (one per
    # (requirement_key, s1_version_seq)); retried in place via start_attempt.
    # -----------------------------------------------------------------------
    op.create_table(
        's3_generation_jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        # Loose references: requirement_key is the upstream requirement/Jira
        # identifier (string; the requirements table is v1-side, not per-tenant).
        sa.Column('requirement_key', sa.Text(), nullable=False),
        # The S1 org snapshot pinned at enqueue — reproducible run. Real FK:
        # logical_versions.version_seq is BIGSERIAL PRIMARY KEY (same schema).
        sa.Column('s1_version_seq', sa.BigInteger(), nullable=False),
        sa.Column('s1_version_name', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='queued'),
        # Latest attempt's request_id (the ledger key for the current run).
        sa.Column('current_request_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('progress_pct', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('progress_msg', sa.String(length=200), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        # Loose int (no per-tenant users table to FK); set by the slice-4 endpoint.
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(
            ['s1_version_seq'], ['logical_versions.version_seq'],
            name='fk_s3_generation_jobs_s1_version',
        ),
        # Idempotency layer 1 (D-106.4 .B): one job per logical request, ever.
        sa.UniqueConstraint('requirement_key', 's1_version_seq',
                            name='uq_s3_generation_jobs_request'),
        sa.CheckConstraint(
            "status IN ('queued', 'claimed', 'running', 'completed', 'failed', 'cancelled')",
            name='ck_s3_generation_jobs_status',
        ),
    )
    # Consumer work-find index (slice 3): the active set only.
    op.create_index(
        'idx_s3_generation_jobs_status_active',
        's3_generation_jobs',
        ['status'],
        postgresql_where=sa.text("status IN ('queued', 'claimed', 'running')"),
    )
    op.execute(
        "COMMENT ON TABLE s3_generation_jobs IS "
        "'Substrate-3 production-integration job queue (D-106.4). Per-tenant; "
        "mirrors the v1 generation_jobs lifecycle, not its shared placement. "
        "One job per (requirement_key, s1_version_seq); retried in place.'"
    )

    # -----------------------------------------------------------------------
    # s3_generation_job_attempts — normalized per-attempt history (B-job).
    # Each row is one run of run_generation; request_id is the fresh per-attempt
    # ledger key (closes the generation_requests PK collision on retry).
    # -----------------------------------------------------------------------
    op.create_table(
        's3_generation_job_attempts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('attempt_no', sa.Integer(), nullable=False),
        sa.Column('request_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='running'),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['job_id'], ['s3_generation_jobs.id'],
            name='fk_s3_generation_job_attempts_job', ondelete='CASCADE',
        ),
        sa.UniqueConstraint('job_id', 'attempt_no', name='uq_s3_generation_job_attempts_no'),
        # Each attempt's request_id is a fresh UUID — globally unique.
        sa.UniqueConstraint('request_id', name='uq_s3_generation_job_attempts_request_id'),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')",
            name='ck_s3_generation_job_attempts_status',
        ),
    )
    op.create_index(
        'idx_s3_generation_job_attempts_job_id',
        's3_generation_job_attempts',
        ['job_id'],
    )
    op.execute(
        "COMMENT ON TABLE s3_generation_job_attempts IS "
        "'Substrate-3 per-attempt history (D-106.4 .B / B-job). One row per "
        "run_generation attempt; request_id is the fresh per-attempt ledger key.'"
    )


def downgrade():
    op.drop_index('idx_s3_generation_job_attempts_job_id', table_name='s3_generation_job_attempts')
    op.drop_table('s3_generation_job_attempts')
    op.drop_index('idx_s3_generation_jobs_status_active', table_name='s3_generation_jobs')
    op.drop_table('s3_generation_jobs')

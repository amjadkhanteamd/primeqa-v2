"""substrate-1 sync-job queue: s1_sync_jobs (D-151, cutover Step 0 / S0.2)

Revision ID: 20260604_0020
Revises: 20260604_0010
Create Date: 2026-06-04

Per DECISIONS_LOG.md D-151. The S1-sync production trigger's job queue — the
durable per-tenant record of "sync ``connected_org_id`` from Salesforce", with
the claim → heartbeat → reap lifecycle the S0.3 consumer drains. A near-mechanical
mirror of ``s4_execution_jobs`` (D-130): per-tenant schema (unqualified, **no
tenant_id column** — isolation by schema), ``SELECT … FOR UPDATE SKIP LOCKED``
claim, partial-unique on the active set.

**No attempts child table** (the one divergence from S4): the engine writes its
own per-run history to ``sync_runs``; the job's ``last_sync_run_id`` links to it
(the resume anchor for ``run_sync(resume_sync_run_id=…)``). Duplicating sync_runs
in an attempts table would be redundant.

Idempotency (the S4 repeatable shape): partial-unique ``(connected_org_id) WHERE
status IN (active)`` — one active sync per org, a fresh job allowed once the prior
is terminal (re-sync as the org evolves).

Tenant-branch migration; table is per-tenant. Schema only — no behavior.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260604_0020'
down_revision = '20260604_0010'
branch_labels = None
depends_on = None


_ACTIVE = "status IN ('queued', 'claimed', 'running')"


def upgrade():
    op.create_table(
        's1_sync_jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        # The sync target (a connected_orgs row in this tenant schema).
        sa.Column('connected_org_id', postgresql.UUID(as_uuid=True), nullable=False),
        # The credential source — a v1-side environments.id (loose int; the SF
        # client resolves from it via resolve_sync_sf_client, D-150).
        sa.Column('environment_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='queued'),
        # The engine's sync_run for the current/last attempt — the resume anchor
        # (run_sync(resume_sync_run_id=…)). The engine owns sync_runs; this links.
        sa.Column('last_sync_run_id', postgresql.UUID(as_uuid=True), nullable=True),
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
            name='ck_s1_sync_jobs_status',
        ),
    )
    # Idempotency: one ACTIVE sync per connected_org; a fresh job after terminal.
    op.create_index(
        'uq_s1_sync_jobs_active',
        's1_sync_jobs',
        ['connected_org_id'],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
    )
    # Consumer work-find index: the active set (the claim scans queued).
    op.create_index(
        'idx_s1_sync_jobs_status_active',
        's1_sync_jobs',
        ['status'],
        postgresql_where=sa.text(_ACTIVE),
    )
    op.execute(
        "COMMENT ON TABLE s1_sync_jobs IS "
        "'Substrate-1 sync-job queue (D-151). Per-tenant; mirrors the S4 "
        "execution-jobs lifecycle. Partial-unique on the active set — one active "
        "sync per connected_org, re-runnable after terminal. last_sync_run_id "
        "links the engine sync_run (the resume anchor).'"
    )


def downgrade():
    op.drop_index('idx_s1_sync_jobs_status_active', table_name='s1_sync_jobs')
    op.drop_index('uq_s1_sync_jobs_active', table_name='s1_sync_jobs')
    op.drop_table('s1_sync_jobs')

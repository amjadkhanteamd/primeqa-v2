"""substrate-4 scheduled runs: s4_run_schedules (Build 6, D-214)

Revision ID: 20260611_0010
Revises: 20260608_0010
Create Date: 2026-06-11

Per DECISIONS_LOG.md D-214. The third automated execution trigger on the new
engine (theme 4): on-approval (D-199) and the CI release gate (D-198.3 +
webhook enqueue) already exist; this table drives the SCHEDULED regression
trigger. One row = one cadence for one environment; the scheduler's
``s4_schedule_tick`` enqueues every currently-approved claim on the
environment when the cron window passes (lateness-tolerant — fire once when
due-or-overdue, never backfill).

Placement (PER-TENANT): scheduling config for one tenant's environments —
isolated tenant data, beside s4_execution_jobs (alembic tenant branch,
unqualified, no tenant_id column — isolation by schema).

Deliberately NOT v1's ``scheduled_runs`` (that table is on the D-213
retirement list).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260611_0010'
down_revision = '20260608_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        's4_run_schedules',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        # The v1 environments row this schedule targets (public.environments;
        # logical ref — environments live in the shared schema).
        sa.Column('environment_id', sa.Integer(), nullable=False),
        sa.Column('cron_expr', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_fired_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id', name='pk_s4_run_schedules'),
        sa.UniqueConstraint('environment_id', 'cron_expr',
                            name='uq_s4_run_schedules_env_cron'),
    )
    op.execute(
        "COMMENT ON TABLE s4_run_schedules IS "
        "'Substrate-4 scheduled regression runs (D-214). Per-tenant; one row = "
        "one cadence for one environment. The scheduler tick enqueues every "
        "approved claim on the environment when the cron window passes.'"
    )


def downgrade():
    op.drop_table('s4_run_schedules')

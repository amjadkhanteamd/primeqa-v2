"""substrate-4 provisioning: s4_created_records (F6.1, D-196)

Revision ID: 20260608_0010
Revises: 20260604_0030
Create Date: 2026-06-08

Per DECISIONS_LOG.md D-196 (+ SPEC substrate_4_execution F6). The cleanup spine
for the provisioning vertical: an audit of every record S4 *creates* during a
run (the positive vertical's target, and — from F6.2 — its provisioned
master-detail/lookup parents). The executor records each create on a
``CreatedRecordTracker`` and tears them down **reverse-order** in-run; this table
is the finalize-persisted audit (and the foundation for the deferred
crash-recovery reaper, which needs pre-teardown durability).

Placement (PER-TENANT): execution truth for one tenant's recipes against one
tenant's orgs — isolated tenant data. Lands in the per-tenant schema (alembic
tenant branch, unqualified, **no tenant_id column** — isolation by schema, the
substrate convention), beside s4_execution_runs / s4_execution_jobs.

Schema:
  - id (BIGSERIAL PK), run_id (the s4_execution_runs run this create belongs to —
    a logical reference, not DB-enforced; runs persist in the same finalize tx),
    sobject (the SF object), record_id (the created record's SF id), created_seq
    (the in-run creation order — reverse this for teardown), cleaned (whether a
    reaper has reclaimed it; in-run teardown is best-effort and not reflected
    here — the reaper is deferred), created_at.
  - Index on run_id (the obvious lookup: "records this run created").

Tenant-branch migration; table is per-tenant. Schema only — no behavior.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260608_0010'
down_revision = '20260604_0030'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        's4_created_records',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        # The run this create belongs to (logical ref to s4_execution_runs.run_id;
        # not DB-enforced — created records persist in the same finalize tx).
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sobject', sa.Text(), nullable=False),
        sa.Column('record_id', sa.Text(), nullable=False),
        # In-run creation order; teardown walks this reverse (children before parents).
        sa.Column('created_seq', sa.Integer(), nullable=False),
        # Reaper-reclaimed flag (the crash-recovery reaper is deferred; in-run
        # teardown is best-effort and not reflected here).
        sa.Column('cleaned', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id', name='pk_s4_created_records'),
    )
    op.create_index(
        'idx_s4_created_records_run_id',
        's4_created_records',
        ['run_id'],
    )
    op.execute(
        "COMMENT ON TABLE s4_created_records IS "
        "'Substrate-4 provisioning audit (D-196, F6.1). Per-tenant; one row per "
        "record S4 created in a run (target + provisioned parents). The executor "
        "tears them down reverse-order in-run; this is the audit + the foundation "
        "for the deferred crash-recovery reaper.'"
    )


def downgrade():
    op.drop_index('idx_s4_created_records_run_id', table_name='s4_created_records')
    op.drop_table('s4_created_records')

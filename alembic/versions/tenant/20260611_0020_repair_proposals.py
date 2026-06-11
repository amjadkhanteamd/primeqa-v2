"""repair-agent spine: repair_proposals (Build 7, D-215.1)

Revision ID: 20260611_0020
Revises: 20260611_0010
Create Date: 2026-06-11

Per DECISIONS_LOG.md D-215/.1. The repair agent's review ledger: one row = one
deterministic repair PROPOSAL for one failed/errored run's claim. Proposal-only
spine — a human approves on the Repairs panel; apply executes the repair
(rerun / regenerate) and stamps the row. Findings (enforcement_gap etc.) never
get rows — they are the product's output, not defects to repair.

Placement (PER-TENANT): repair decisions over one tenant's runs/claims —
isolated tenant data (alembic tenant branch, unqualified, no tenant_id
column — isolation by schema).

Status flow: proposed → approved → applied, or proposed → rejected.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260611_0020'
down_revision = '20260611_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'repair_proposals',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        # The triggering run + its claim (logical refs to s4_execution_runs /
        # test_claims; the triage reads s6_interpretations).
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('claim_test_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('environment_id', sa.Integer(), nullable=False),
        sa.Column('verdict', sa.Text(), nullable=False),
        sa.Column('cause_kind', sa.Text(), nullable=True),
        # regenerate_from_current_org | rerun (the deterministic spine kinds)
        sa.Column('proposal_kind', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        # proposed -> approved -> applied | rejected
        sa.Column('status', sa.Text(), nullable=False,
                  server_default=sa.text("'proposed'")),
        sa.Column('decided_by', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id', name='pk_repair_proposals'),
    )
    op.create_index('idx_repair_proposals_claim', 'repair_proposals',
                    ['claim_test_id'])
    op.create_index('idx_repair_proposals_status', 'repair_proposals',
                    ['status'])
    # one ACTIVE (proposed/approved) proposal per (claim, kind) — the triage
    # dedup. Partial unique index; applied/rejected rows accumulate as history.
    op.execute(
        "CREATE UNIQUE INDEX uq_repair_proposals_active "
        "ON repair_proposals (claim_test_id, proposal_kind) "
        "WHERE status IN ('proposed', 'approved')"
    )
    op.execute(
        "COMMENT ON TABLE repair_proposals IS "
        "'Repair-agent review ledger (D-215.1). Proposal-only spine: "
        "deterministic triage of failed S6 interpretations; human "
        "approve/reject; apply reruns or regenerates. Findings never get rows.'"
    )


def downgrade():
    op.drop_table('repair_proposals')

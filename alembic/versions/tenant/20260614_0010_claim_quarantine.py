"""persisted flaky-test quarantine: claim_quarantine ledger (theme #5, D-232)

Revision ID: 20260614_0010
Revises: 20260613_0010
Create Date: 2026-06-14

Per DECISIONS_LOG.md D-232. D-200 shipped a storage-free, decision-time flake
quarantine; this is the deferred PERSISTED ledger so operators can pin/unpin a
claim and the quarantine survives across decision recomputes.

One row per CLAIM IDENTITY (test_id) — NOT per version (so it is a column-free
ledger keyed on test_id, not a flag on the version-seq-versioned test_claims).
`active=true` ⇒ quarantined; an unpin flips `active=false` + stamps lifted_*, so
history is kept (upsert on test_id). `source` distinguishes a human pin
('manual', which WINS over the live _is_flaky signal in the decision) from a
future auto-pin loop ('auto').

Placement (PER-TENANT): quarantine over one tenant's claims — isolated tenant
data (alembic tenant branch, unqualified, no tenant_id column — isolation by
schema). Additive; the QuarantineStore reads it best-effort so the code can
deploy before this migration is applied (the D-230 ordering).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260614_0010'
down_revision = '20260613_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'claim_quarantine',
        # The quarantined claim's IDENTITY (logical ref to test_claims.test_id;
        # version-independent — quarantine is a claim fact, not a version fact).
        sa.Column('test_id', postgresql.UUID(as_uuid=True), primary_key=True,
                  nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),
        sa.Column('reason', sa.Text(), nullable=True),
        # 'manual' (a human pin — wins over the live signal) | 'auto' (a future
        # auto-pin loop persisting the _is_flaky detection).
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('pinned_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('pinned_by', sa.Integer(), nullable=True),
        sa.Column('lifted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lifted_by', sa.Integer(), nullable=True),
        sa.CheckConstraint("source IN ('manual', 'auto')",
                           name='ck_claim_quarantine_source'),
    )
    # The decision + the badge query the small set of ACTIVE rows.
    op.create_index('idx_claim_quarantine_active', 'claim_quarantine',
                    ['active'], postgresql_where=sa.text('active = true'))


def downgrade():
    op.drop_index('idx_claim_quarantine_active', table_name='claim_quarantine')
    op.drop_table('claim_quarantine')

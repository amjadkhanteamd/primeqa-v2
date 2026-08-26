"""3A-2: `conformance-claim` + `ui-inspection` join the kind enums.

Plain in-transaction ``ALTER TYPE ... ADD VALUE IF NOT EXISTS`` — the
20260702_0010 (D-305) posture; transaction-safe on PG 12+ as long as the
same transaction does not USE the new members (nothing in this chain
does). D-459: autocommit_block is prohibited (guard-tested).

MIGRATE-FIRST (D-285): apply before any writer deploys. Downgrades are
deliberate no-ops (PG cannot drop enum members in place).
"""
from alembic import op

revision = '20260825_0010'
down_revision = '20260823_0020'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE claim_kind ADD VALUE IF NOT EXISTS 'conformance-claim'")
    op.execute("ALTER TYPE recipe_kind ADD VALUE IF NOT EXISTS 'ui-inspection'")


def downgrade():
    pass

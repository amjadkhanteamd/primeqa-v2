"""D-305: add 'acceptance-claim' to the claim_kind enum.

The acceptance archetype (lever 7c) — "the org ACCEPTS this operation under
this business state", the prohibition-claim's mirror. The tested values ride
``semantic_conditions`` (identity-bearing, the D-293 machinery), so distinct
acceptance cases are distinct claims.

Tenant-branch migration (down_revision = the prior substrate/tenant head,
20260701_0010). MIGRATE-FIRST (the D-285 lesson): the ORM's CLAIM_KIND_ENUM
now lists the value code-side (create_type=False — the DB type is the truth),
and S3 will WRITE it once the emission path ships, so prod tenants must be
migrated before that code deploys.

Notes:
  - ``ALTER TYPE ... ADD VALUE`` is transaction-safe on PG 12+ as long as the
    added value is not used in the same transaction (it is not — S3 writes
    arrive post-deploy). IF NOT EXISTS keeps it idempotent.
  - Downgrade is a documented no-op: PG cannot drop an enum value in place,
    and an unused value is inert.
"""
from alembic import op

revision = '20260702_0010'
down_revision = '20260701_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TYPE claim_kind ADD VALUE IF NOT EXISTS 'acceptance-claim'"
    )


def downgrade():
    # PG cannot remove an enum value in place; an unused value is inert.
    pass

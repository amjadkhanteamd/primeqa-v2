"""substrate-3: add emission-deferred to refusal_kind enum

Revision ID: 20260522_0040
Revises: 20260522_0030
Create Date: 2026-05-22

Per DECISIONS_LOG.md D-105 (refuse-not-crash for grounded-but-unbuilt
claim_kinds). Adds the ninth refusal kind — ``emission-deferred`` — to
the substrate-3 ``refusal_kind`` native PG enum. It is the operational
refusal raised when a requirement grounds admissibly but the substrate
has not yet built emission for that (archetype, claim_kind) pair
(D-097.6 deferral, runtime face). Distinct from the input-quality
invalidity kinds: the requirement is fine; the capability boundary is
the substrate's, and it lifts as kinds land.

Tenant-branch migration (down_revision = the prior substrate-3 head).
Runs against each tenant_<id> schema via alembic/env.py's SET LOCAL
search_path; the ``refusal_kind`` type is referenced unqualified so it
resolves against the search path exactly as the Slice-0 migration
created it.

ADD VALUE notes:
  - ``IF NOT EXISTS`` makes the upgrade re-runnable / idempotent
    (PG 9.6+).
  - ``ALTER TYPE ... ADD VALUE`` is transaction-safe on PG 12+ as long
    as the new value is not *used* in the same transaction; this
    migration only adds it, so it runs cleanly inside alembic's
    per-migration transaction.
  - Downgrade is a deliberate no-op: PG cannot drop an enum value
    without recreating the type and rewriting every dependent column,
    which is destructive and out of scope for a forward-only taxonomy
    extension (matches the inspection-trigger ADD VALUE posture and
    substrate-2's D-063 §9.5).

Schema only. No data, no behavior.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260522_0040'
down_revision = '20260522_0030'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TYPE refusal_kind ADD VALUE IF NOT EXISTS 'emission-deferred'"
    )


def downgrade():
    # No-op: PG enum values cannot be removed without recreating the
    # type and rewriting dependent columns. Forward-only per D-105 /
    # substrate-2 D-063 §9.5.
    pass

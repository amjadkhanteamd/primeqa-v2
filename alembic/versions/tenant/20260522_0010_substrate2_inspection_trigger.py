"""substrate-2: add inspection-trigger to trigger_kind enum

Revision ID: 20260522_0010
Revises: 20260520_0010
Create Date: 2026-05-22

Per DECISIONS_LOG.md D-099 (reopens D-055's five-kind lock). Adds the
sixth trigger kind — ``inspection-trigger`` — to the substrate-2
``trigger_kind`` native PG enum. An inspection-trigger is the
invariant-inspective execution-initiation mode (D-099.1/.2): execution
initiated by inspection of extant state rather than by a causal event.

Tenant-branch migration (down_revision = substrate-3 ledger head). Runs
against each tenant_<id> schema via alembic/env.py's SET LOCAL
search_path; the ``trigger_kind`` type is referenced unqualified so it
resolves against the search path exactly as substrate-2 created it.

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
    extension (matches substrate-2's D-063 §9.5 ADD VALUE posture).

Schema only. No data, no behavior.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260522_0010'
down_revision = '20260520_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TYPE trigger_kind ADD VALUE IF NOT EXISTS 'inspection-trigger'"
    )


def downgrade():
    # No-op: PG enum values cannot be removed without recreating the
    # type and rewriting dependent columns. Forward-only per D-099 /
    # substrate-2 D-063 §9.5.
    pass

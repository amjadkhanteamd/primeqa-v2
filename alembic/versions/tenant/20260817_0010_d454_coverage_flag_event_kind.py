"""D-454: `coverage_flag` joins the provenance event-kind enum.

The partial-coverage flag (flag-for-review, never refuse — D-453 measured
the refuse ceiling at 159/215 with 112 live-green) lands claim-keyed as an
append-only ``test_provenance`` event. ``event_kind`` is the PG enum
``provenance_event_kind``; this migration adds the one member the writer
needs. Additive only — no existing rows or members change.

MIGRATE-FIRST (D-285/D-438): apply before the writer deploys. ``ADD VALUE
IF NOT EXISTS`` is idempotent; on PG 12+ it is transaction-safe as long as
the same transaction does not USE the new member (nothing in this chain
does) — the same posture as the five sibling ADD-VALUE revisions.

FIX-1 (2026-08-24): autocommit_block removed — it asserts alembic owns the
transaction, but env.py opens its own via connection.begin(), so fresh
chains crashed here and rolled back atomically. Already-applied tenants
never re-run this; IF NOT EXISTS makes re-execution a no-op. See
docs/reviews/PLIMSOL_FIX_PLAN.md.

Downgrade is deliberately a no-op: PG cannot drop an enum member in place,
and rows written with it must not be orphaned by a schema rollback.
"""
from alembic import op

revision = '20260817_0010'
down_revision = '20260810_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TYPE provenance_event_kind "
        "ADD VALUE IF NOT EXISTS 'coverage_flag'"
    )


def downgrade():
    # PG enums cannot drop members in place; rows may already carry it.
    pass

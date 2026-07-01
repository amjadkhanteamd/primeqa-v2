"""substrate-3 (D-293): add behaviour-incomplete to refusal_kind enum

Revision ID: 20260701_0010
Revises: 20260629_0010
Create Date: 2026-07-01

Per DECISIONS_LOG.md D-293 (refuse-not-degrade for prohibition claims). Adds
the tenth refusal kind — ``behaviour-incomplete`` — to the substrate-3
``refusal_kind`` native PG enum. It is the policy refusal raised when a
prohibition intent grounds admissibly but is not a COMPLETE behaviour instance:
no behavioural reject recipe is derivable from the grounding validation rule
(a non-numeric formula, or a non-VR-rejectable operation like delete/share/
transfer). The substrate refuses honestly rather than degrading to the old
caveated metadata-inspection — extending the D-247 honesty discipline from
coverage to authoring. Lifts as violation-derivation widens (the out-of-scope
D-293 follow-on).

Tenant-branch migration (down_revision = the prior substrate/tenant head,
D-291's env-59 dangler close). Runs against each tenant_<id> schema via
alembic/env.py's SET LOCAL search_path; the ``refusal_kind`` type is referenced
unqualified so it resolves against the search path exactly as the Slice-0
migration created it.

ADD VALUE notes (identical posture to 20260522_0040 emission-deferred):
  - ``IF NOT EXISTS`` makes the upgrade re-runnable / idempotent (PG 9.6+).
  - ``ALTER TYPE ... ADD VALUE`` is transaction-safe on PG 12+ as long as the
    new value is not *used* in the same transaction; this migration only adds
    it, so it runs cleanly inside alembic's per-migration transaction.
  - Downgrade is a deliberate no-op: PG cannot drop an enum value without
    recreating the type and rewriting every dependent column, which is
    destructive and out of scope for a forward-only taxonomy extension.

Schema only. No data, no behavior.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260701_0010'
down_revision = '20260629_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TYPE refusal_kind ADD VALUE IF NOT EXISTS 'behaviour-incomplete'"
    )


def downgrade():
    # No-op: PG enum values cannot be removed without recreating the type and
    # rewriting dependent columns. Forward-only per D-293 / D-105 precedent.
    pass

"""s4_execution_runs (claim_test_id, finished_at DESC) index (D-233 review fix)

Revision ID: 20260614_0020
Revises: 20260614_0010
Create Date: 2026-06-14

The D-233.B claims-list "last run" column added a correlated LATERAL to
``_list_claims``: ``SELECT ... FROM s4_execution_runs WHERE claim_test_id = c.test_id
ORDER BY finished_at DESC LIMIT 1`` — run once per visible claim (up to 50/page).
``s4_execution_runs`` carried only ``idx_s4_execution_runs_recipe_id`` (the
20260527_0010 DDL deferred others "until real query patterns emerge"); with no
index on ``claim_test_id`` / ``finished_at`` that LATERAL is a seqscan + sort of the
whole tenant run history per claim, which degrades ``/claims`` (and the draft inbox
that shares ``_list_claims``) as runs accumulate. The D-233 adversarial review
confirmed it.

A composite ``(claim_test_id, finished_at DESC)`` satisfies the equality filter and
the ``ORDER BY finished_at DESC LIMIT 1`` from a single index scan. It also backs the
PRE-EXISTING same-shape reads: ``read_claim_runs`` (``_CLAIM_RUNS_SQL``) and the flake
window (``_COUNTED_RUNS_SQL``), both ``WHERE claim_test_id = … ORDER BY finished_at
DESC LIMIT …``.

Placement (PER-TENANT): ``s4_execution_runs`` is per-tenant (schema-isolated, no
tenant_id column) — alembic tenant branch. Purely additive index; ``IF NOT EXISTS``
so it is safe to re-run and tolerant of a hand-created index.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260614_0020'
down_revision = '20260614_0010'
branch_labels = None
depends_on = None


_INDEX = 'idx_s4_execution_runs_claim_finished'


def upgrade():
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {_INDEX} "
        "ON s4_execution_runs (claim_test_id, finished_at DESC)")


def downgrade():
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")

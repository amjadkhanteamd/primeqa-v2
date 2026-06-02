"""substrate-6 phrasing: phrasing JSONB column on s6_interpretations (D-117)

Revision ID: 20260601_0020
Revises: 20260601_0010
Create Date: 2026-06-01

Per DECISIONS_LOG.md D-117 (S6 LLM-phrasing presentation layer). A nullable
``phrasing`` JSONB column on ``s6_interpretations`` caches the LLM-phrased prose
(a ``headline`` + ``explanation``) over the deterministic interpretation — NULL
until phrased (on-demand + cache). The phrasing is produced by the v1 enricher
(``intelligence.interpretation_phrasing``) and written by the substrate's pure
``result_store.set_phrasing`` helper — the substrate owns its table's schema; the
LLM stays in v1 (``interpretation/`` stays LLM-free).

Tenant-branch migration; table is per-tenant. Idempotent (``IF NOT EXISTS``).
Schema only — no behavior.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260601_0020'
down_revision = '20260601_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE s6_interpretations ADD COLUMN IF NOT EXISTS phrasing JSONB"
    )


def downgrade():
    op.execute(
        "ALTER TABLE s6_interpretations DROP COLUMN IF EXISTS phrasing"
    )

"""substrate-8 grounding-validity store: s8_grounding_validity (D-142)

Revision ID: 20260603_0030
Revises: 20260603_0010
Create Date: 2026-06-03

Per DECISIONS_LOG.md D-142 (+ SPEC substrate_8_evolution). The S8 recorded-verdict
store: one row per grounded claim version — S8's structured grounding-validity
reading (claim-level + per-recipe verdicts) of one S2 claim against the org at a
given S1 version. The thin mechanics (the D-137 parity): persist + read only — no
sync trigger / reverse index / orchestration (those stay fenced).

Placement (PER-TENANT, mirror-S6): one tenant's grounding verdicts over one
tenant's claims — isolated tenant data. Lands in the per-tenant schema (alembic
tenant branch, unqualified, **no tenant_id column** — isolation by schema, the
substrate convention), beside s6_interpretations / test_claims / test_recipes.

Schema (D-142):
  - Identity: test_id (UUID) + version_seq (int) — the claim version grounded;
    PK (test_id, version_seq), the artifact grain (the verdict is per-claim-
    version; the per-recipe verdicts live in detail).
  - evaluated_at_version_seq (int): the S1 version the verdict was computed
    against — the contemporaneous-grounding axis (S8-Q-007), made queryable so a
    consumer can detect rows grounded against an older S1 seq (the snapshot is
    only as-fresh-as this seq; refresh is the slice-5 recompute trigger).
  - overall + claim_verdict (TEXT): the Python GroundingVerdict Literal is the
    source of truth; TEXT avoids an ALTER TYPE and stays queryable (the
    s6_interpretations precedent).
  - detail JSONB: the rich part — claim-grounding reason/unresolved + each
    recipe's leg verdicts/reasons/invalid + rolled_up.

Index: overall (the "show me all drifted / broken" query). test_id+version_seq
lookups use the PK.

Tenant-branch migration; table is per-tenant. Schema only — no behavior.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260603_0030'
down_revision = '20260603_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        's8_grounding_validity',
        # Identity: one verdict per grounded claim version.
        sa.Column('test_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version_seq', sa.Integer(), nullable=False),
        # The S1 seq the verdict was computed against (the snapshot axis).
        sa.Column('evaluated_at_version_seq', sa.Integer(), nullable=False),
        # The composed verdict (TEXT — Python Literal is the source of truth).
        sa.Column('overall', sa.Text(), nullable=False),
        sa.Column('claim_verdict', sa.Text(), nullable=False),
        # The rich part: claim-grounding + per-recipe leg verdicts + roll-ups.
        sa.Column('detail', postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint(
            'test_id', 'version_seq', name='pk_s8_grounding_validity'),
    )
    # "all drifted / broken tests" — the standing-verdict consumer query.
    op.create_index(
        'idx_s8_grounding_validity_overall',
        's8_grounding_validity',
        ['overall'],
    )
    op.execute(
        "COMMENT ON TABLE s8_grounding_validity IS "
        "'Substrate-8 grounding-validity store (D-142). Per-tenant; one row per "
        "grounded claim version — typed identity + overall/claim_verdict + a "
        "detail JSONB (claim + per-recipe leg verdicts). A snapshot as-of "
        "evaluated_at_version_seq; the slice-5 recompute trigger refreshes it.'"
    )


def downgrade():
    op.drop_index(
        'idx_s8_grounding_validity_overall',
        table_name='s8_grounding_validity')
    op.drop_table('s8_grounding_validity')

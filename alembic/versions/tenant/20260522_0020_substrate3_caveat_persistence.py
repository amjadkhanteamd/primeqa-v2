"""substrate-3: persist caveat posture on generation_outcomes

Revision ID: 20260522_0020
Revises: 20260522_0010
Create Date: 2026-05-22

Per DECISIONS_LOG.md D-101.3. The plausibility caveat is persisted as the
emission-time epistemic posture of an outcome, NOT derived-on-read: a provenance
ledger row must be self-describing (``admissibility_layer`` alone conflates
layer-1-complete and layer-1-plausible), and a future Layer-2 parser rollout
must not silently rewrite the posture of already-emitted artifacts.

Adds to ``generation_outcomes``:
  - ``caveat_required BOOLEAN NOT NULL DEFAULT false`` — does the artifact carry
    a caveat? False on refusals and Layer-1-complete drafts.
  - ``caveat_kind caveat_kind`` (nullable) — the typed qualification class; the
    semantic-completeness registry verdict snapshot (D-097.3 is the authority).
    Stored typed posture only, never rendered prose.
  - CHECK ``caveat_required = (caveat_kind IS NOT NULL)`` — self-consistency.

Tenant-branch migration (down_revision = the inspection-trigger migration). The
``caveat_kind`` enum and the columns land in the per-tenant schema via
alembic/env.py's SET LOCAL search_path (the substrate-3 ledger convention).

Schema only. No data, no behavior.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260522_0020'
down_revision = '20260522_0010'
branch_labels = None
depends_on = None


# Native PG enum; create_type=False, lifecycle managed here (substrate-3 idiom).
# One value at v1 per D-101.3; future qualification classes add via ALTER TYPE.
caveat_kind_enum = postgresql.ENUM(
    'deeper_verification_layer_unparsed',
    name='caveat_kind',
    create_type=False,
)


def upgrade():
    bind = op.get_bind()
    caveat_kind_enum.create(bind, checkfirst=False)
    op.add_column(
        'generation_outcomes',
        sa.Column('caveat_required', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
    )
    op.add_column(
        'generation_outcomes',
        sa.Column('caveat_kind', caveat_kind_enum, nullable=True),
    )
    op.create_check_constraint(
        'ck_generation_outcomes_caveat',
        'generation_outcomes',
        'caveat_required = (caveat_kind IS NOT NULL)',
    )
    op.execute(
        "COMMENT ON COLUMN generation_outcomes.caveat_kind IS "
        "'Emission-time epistemic posture per D-101.3; semantic-completeness "
        "registry verdict snapshot (D-097.3). Typed posture only, never prose.'"
    )


def downgrade():
    op.drop_constraint(
        'ck_generation_outcomes_caveat', 'generation_outcomes', type_='check',
    )
    op.drop_column('generation_outcomes', 'caveat_kind')
    op.drop_column('generation_outcomes', 'caveat_required')
    caveat_kind_enum.drop(op.get_bind(), checkfirst=False)

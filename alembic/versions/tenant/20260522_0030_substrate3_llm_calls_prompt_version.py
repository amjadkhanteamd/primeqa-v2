"""substrate-3: add prompt_version to llm_calls

Revision ID: 20260522_0030
Revises: 20260522_0020
Create Date: 2026-05-22

Per DECISIONS_LOG.md D-103.5 / D-104. The live eval layer (D-089 slice 2) makes
prompt provenance directly useful: a per-call ``prompt_version`` next to
``model_identifier`` lets drift evals correlate behavior to the exact frozen
prompt that produced it, without joining back through the request's
``operational_context`` JSONB. Nullable — populated from the resolved prompt
version (request ``operational_context.prompt_template_version`` or the registry
CURRENT); rows written before this column are simply NULL.

Tenant-branch migration; ``llm_calls`` lives in the per-tenant schema. Schema
only.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260522_0030'
down_revision = '20260522_0020'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('llm_calls', sa.Column('prompt_version', sa.Text(), nullable=True))
    op.execute(
        "COMMENT ON COLUMN llm_calls.prompt_version IS "
        "'Frozen prompt version that produced this turn (D-103.5/D-104); next to "
        "model_identifier for drift correlation. NULL on pre-column rows.'"
    )


def downgrade():
    op.drop_column('llm_calls', 'prompt_version')

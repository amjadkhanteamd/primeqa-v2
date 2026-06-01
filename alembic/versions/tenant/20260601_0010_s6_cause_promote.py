"""substrate-6 clustering: promote cause_kind/vr_name on s6_interpretations (D-116)

Revision ID: 20260601_0010
Revises: 20260527_0020
Create Date: 2026-06-01

Per DECISIONS_LOG.md D-116 (S6 cross-run clustering). The s6_interpretations
store (D-111.2) kept the deeper ``cause`` ({cause_kind, vr_name, detail}) in the
``detail`` JSONB and explicitly DEFERRED a cause index — *"cause_kind / vr_name
promote to columns when cause-clustering lands"*. This is that trigger: promote
``cause_kind`` + ``vr_name`` to typed (TEXT, nullable — ``Cause`` is Optional)
columns + b-tree indexes, the queryable axes the clustering GROUP BYs need
(recurring causes, the same VR across runs). ``detail.cause`` stays the
structured source of truth; the columns are the clustering index.

Back-fills existing rows from the JSONB so already-persisted interpretations
cluster too; ``persist_interpretation`` writes the columns going forward.

Tenant-branch migration; table is per-tenant. Idempotent (ADD COLUMN /
CREATE INDEX ``IF NOT EXISTS`` — the substrate-1 field_details_add_picklist_set
precedent). Schema only — no behavior.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260601_0010'
down_revision = '20260527_0020'
branch_labels = None
depends_on = None


def upgrade():
    # Promote the two cause axes from the detail JSONB to typed, indexable
    # columns (TEXT, nullable — the Python CauseKind Literal is the source of
    # truth, mirroring the verdict column).
    op.execute(
        "ALTER TABLE s6_interpretations "
        "ADD COLUMN IF NOT EXISTS cause_kind TEXT, "
        "ADD COLUMN IF NOT EXISTS vr_name TEXT"
    )
    # Back-fill from the existing detail->'cause' JSONB so already-persisted
    # interpretations cluster too (a no-op on an empty / cause-less table).
    op.execute(
        "UPDATE s6_interpretations "
        "SET cause_kind = detail->'cause'->>'cause_kind', "
        "    vr_name    = detail->'cause'->>'vr_name' "
        "WHERE detail->'cause' IS NOT NULL"
    )
    # The clustering GROUP BYs (recipe_id is already indexed; run_id is the PK).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_s6_interpretations_cause_kind "
        "ON s6_interpretations(cause_kind)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_s6_interpretations_vr_name "
        "ON s6_interpretations(vr_name)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_s6_interpretations_vr_name")
    op.execute("DROP INDEX IF EXISTS idx_s6_interpretations_cause_kind")
    op.execute(
        "ALTER TABLE s6_interpretations "
        "DROP COLUMN IF EXISTS vr_name, "
        "DROP COLUMN IF EXISTS cause_kind"
    )

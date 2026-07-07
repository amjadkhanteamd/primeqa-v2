"""Per-org S8 grounding (3f Slices 2-3): ``connected_org_id`` on
``s8_grounding_validity`` + PK re-key to ``(test_id, version_seq,
connected_org_id)``.

Grounding is fundamentally per-org — a claim's field/VR/picklist existence
depends on ONE org's model. The store was keyed ``(test_id, version_seq)``
with rows computed org-blind; the D-265 guardrail refused multi-org tenants
outright (clearing their rows every tick). This migration gives every verdict
an org identity so the recompute can loop per org and the readers can filter.

Backfill: a tenant with EXACTLY ONE org-with-model attributes its existing
rows to that org (they were computed against precisely that org's model —
org-blind was vacuously org-correct). Rows still NULL after that are
unattributable (multi-org tenants are already empty — D-265 cleared them;
zero-org tenants have nothing their rows could bind to) and are deleted, then
the column goes NOT NULL.

(tenant-branch migration). Apply with:

    alembic -x mode=all_tenants upgrade 20260707_0010
  (or -x mode=tenant -x tenant_id=N upgrade 20260707_0010)

Revision ID: 20260707_0010
Revises: 20260703_0020
Create Date: 2026-07-07
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260707_0010"
down_revision = "20260703_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE s8_grounding_validity "
        "ADD COLUMN IF NOT EXISTS connected_org_id UUID REFERENCES connected_orgs(id)"
    )
    # Single-org backfill: the one org with a live model owns every row.
    op.execute(
        "UPDATE s8_grounding_validity SET connected_org_id = one_org.org "
        "FROM (SELECT MIN(CAST(connected_org_id AS text))::uuid AS org "
        "      FROM entities "
        "      WHERE valid_to_seq IS NULL AND connected_org_id IS NOT NULL "
        "      HAVING COUNT(DISTINCT connected_org_id) = 1) one_org "
        "WHERE connected_org_id IS NULL"
    )
    # Unattributable leftovers (multi-org already cleared by D-265; zero-org
    # rows have no org to bind to) — delete, then enforce NOT NULL.
    op.execute(
        "DELETE FROM s8_grounding_validity WHERE connected_org_id IS NULL"
    )
    op.execute(
        "ALTER TABLE s8_grounding_validity "
        "ALTER COLUMN connected_org_id SET NOT NULL"
    )
    # Re-key: one verdict per claim version PER ORG.
    op.execute(
        "ALTER TABLE s8_grounding_validity "
        "DROP CONSTRAINT IF EXISTS pk_s8_grounding_validity"
    )
    op.execute(
        "ALTER TABLE s8_grounding_validity "
        "ADD CONSTRAINT pk_s8_grounding_validity "
        "PRIMARY KEY (test_id, version_seq, connected_org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_s8_grounding_validity_org "
        "ON s8_grounding_validity(connected_org_id)"
    )


def downgrade() -> None:
    # Best-effort — only cleanly reversible while the data is single-org
    # (multi-org rows collide on the 2-column PK; same caveat as 20260622_0020).
    op.execute("DROP INDEX IF EXISTS idx_s8_grounding_validity_org")
    op.execute(
        "ALTER TABLE s8_grounding_validity "
        "DROP CONSTRAINT IF EXISTS pk_s8_grounding_validity"
    )
    op.execute(
        "ALTER TABLE s8_grounding_validity "
        "ADD CONSTRAINT pk_s8_grounding_validity "
        "PRIMARY KEY (test_id, version_seq)"
    )
    op.execute(
        "ALTER TABLE s8_grounding_validity DROP COLUMN IF EXISTS connected_org_id"
    )

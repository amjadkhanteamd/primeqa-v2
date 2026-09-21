"""Triage round 2, batch 1 (AUD-037): an unlinked surface says why.

Tenant schema, REJECTING (a violating row is refused; production holds zero
rows in the table at all — read-only proven 2026-09-21 — and the merge is
dump-first per D-476 regardless):

  requirement_surface_links   CHECK: a deactivated link carries a non-empty
                              deactivation_reason — the same constraint
                              D-497 gave release_targets and quality_waivers.

Idempotent: created only when absent.
"""
from alembic import op

revision = "20260921_0010"
down_revision = "20260919_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE c.conname = 'ck_requirement_surface_links_deactivation_reason'
                  AND t.relname = 'requirement_surface_links' AND n.nspname = current_schema()
            ) THEN
                ALTER TABLE requirement_surface_links
                    ADD CONSTRAINT ck_requirement_surface_links_deactivation_reason
                    CHECK (active OR length(btrim(COALESCE(deactivation_reason, ''))) > 0);
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE requirement_surface_links DROP CONSTRAINT IF EXISTS ck_requirement_surface_links_deactivation_reason")

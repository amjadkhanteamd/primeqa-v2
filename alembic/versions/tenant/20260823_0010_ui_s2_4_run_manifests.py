"""ui-s2.4: run manifests + jobs.manifest_id (spike, dormant).

Creates s4_ui_run_manifests — the immutable run-scope manifest (surface
set, artifact pins, stabilisation policy, execution policy), written and
committed in its own transaction BEFORE any job is enqueued (the D-281
batch-manifest idiom lifted to run scope). Immutability is by convention:
no update path exists in code (pinned by test); no DB trigger at spike
grade.

Adds s4_ui_inspection_jobs.manifest_id UUID NOT NULL FK. NOT NULL is
safe: production has never had these tables (the 2.3+2.4 chain lands at
branch merge, MIGRATE-FIRST per D-285), and the local spike DB is
throwaway — recreated for 2.4, not backfilled. No ON DELETE action:
manifests are never deleted; a delete attempt on a referenced manifest
correctly fails.

Design: docs/ui-testing/LLD_PHASE2_4_MANIFEST.md.
"""

from alembic import op

revision = '20260823_0010'
down_revision = '20260821_0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE s4_ui_run_manifests (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            payload     JSONB NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        ALTER TABLE s4_ui_inspection_jobs
            ADD COLUMN manifest_id UUID NOT NULL
            REFERENCES s4_ui_run_manifests (id)
    """)
    op.execute("""
        CREATE INDEX s4_ui_inspection_jobs_manifest_idx
            ON s4_ui_inspection_jobs (manifest_id)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE s4_ui_inspection_jobs
            DROP COLUMN IF EXISTS manifest_id
    """)
    op.execute("DROP TABLE IF EXISTS s4_ui_run_manifests")

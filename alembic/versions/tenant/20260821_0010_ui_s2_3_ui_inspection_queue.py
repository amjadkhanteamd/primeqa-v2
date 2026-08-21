"""ui-s2.3: per-tenant UI inspection job queue + results (spike, dormant).

Creates:
  1. s4_ui_inspection_jobs — lease/heartbeat job queue for browser
     inspection batches. Mirrors the ai_enrichment_queue idiom
     (20260512_0010): five-value status vocabulary, attempts counter,
     enqueued/started/completed timestamps, error_text, pickup + stalled
     partial indexes. Deltas (per docs/ui-testing/LLD_PHASE2_3_QUEUE.md):
     lease fields (claimed_by, claimed_at, heartbeat_at) and a reaps
     counter — the reaper is heartbeat-based, not elapsed-time-based,
     because browser batches legitimately run long; reaps records lease
     deaths without charging attempts (claim-only charging).
  2. s4_ui_inspection_results — one row per (job_id, surface_key), the
     raw engine observation JSONB. UNIQUE (job_id, surface_key) makes
     retry finalisation an idempotent UPSERT — duplicate results are
     impossible by constraint. 2.4 extends this with manifest linkage.

MIGRATE-FIRST (D-285) ordering note: this revision ships on the
phase-ui-s2-spike branch; the PRODUCTION application happens only at
branch merge. Until then it is applied to local verification schemas
only. Both tables are dormant — nothing enqueues or consumes except the
manual `python -m primeqa.browser_worker.consume` CLI.
"""

from alembic import op

revision = '20260821_0010'
down_revision = '20260817_0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE s4_ui_inspection_jobs (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            payload       JSONB NOT NULL,
            status        VARCHAR(20) NOT NULL DEFAULT 'pending',
            attempts      INT NOT NULL DEFAULT 0,
            reaps         SMALLINT NOT NULL DEFAULT 0,
            claimed_by    TEXT,
            claimed_at    TIMESTAMPTZ,
            heartbeat_at  TIMESTAMPTZ,
            enqueued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at    TIMESTAMPTZ,
            completed_at  TIMESTAMPTZ,
            error_text    TEXT,
            CONSTRAINT s4_ui_inspection_jobs_status_known CHECK (
                status IN ('pending', 'in_progress', 'succeeded',
                           'failed_retryable', 'failed_permanent')
            ),
            CONSTRAINT s4_ui_inspection_jobs_status_implies_timing CHECK (
                (status = 'pending'
                    AND started_at IS NULL AND completed_at IS NULL
                    AND claimed_by IS NULL AND claimed_at IS NULL
                    AND heartbeat_at IS NULL)
                OR (status = 'in_progress'
                    AND started_at IS NOT NULL AND completed_at IS NULL
                    AND claimed_by IS NOT NULL AND claimed_at IS NOT NULL
                    AND heartbeat_at IS NOT NULL)
                OR (status IN ('succeeded', 'failed_retryable',
                               'failed_permanent')
                    AND started_at IS NOT NULL AND completed_at IS NOT NULL)
            )
        )
    """)
    op.execute("""
        CREATE INDEX s4_ui_inspection_jobs_pickup_idx
            ON s4_ui_inspection_jobs (status, enqueued_at)
    """)
    op.execute("""
        CREATE INDEX s4_ui_inspection_jobs_stalled_idx
            ON s4_ui_inspection_jobs (status, heartbeat_at)
            WHERE status = 'in_progress'
    """)
    op.execute("""
        CREATE TABLE s4_ui_inspection_results (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id       UUID NOT NULL
                         REFERENCES s4_ui_inspection_jobs (id)
                         ON DELETE CASCADE,
            surface_key  TEXT NOT NULL,
            attempt      INT NOT NULL,
            observation  JSONB NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT s4_ui_inspection_results_job_surface_unique
                UNIQUE (job_id, surface_key)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS s4_ui_inspection_results")
    op.execute("DROP TABLE IF EXISTS s4_ui_inspection_jobs")

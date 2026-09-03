"""Scheduling slice (SDLC v3 item 3, D-474) — per-release UI-conformance
runs without manual invocation.

One tenant table: ``ui_run_schedules``. A schedule automates RUNS of an
already-APPROVED claim set — never approval, never authoring. The
semantics-bearing columns:

  created_by        — the AUTHORISING human (D-245 boundary checked at
                      creation; re-checked against their CURRENT
                      authority at every tick — dead or demoted
                      authority deactivates the schedule loudly)
  auth              — the DESCRIPTOR only ({mode, persona}), never a
                      credential (the manifest law, unchanged)
  last_job_id       — the overlap gate reads this job's live status:
                      still queued/running -> SKIP with an audit event,
                      never stack
  error_state       — a failed enqueue or dead authority is a recorded
                      state + audit event, never a silent tick

Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260904_0010"
down_revision = "20260903_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ui_run_schedules (
            id                    BIGSERIAL PRIMARY KEY,
            claim_set_id          UUID NOT NULL REFERENCES claim_sets (id),
            cron_expr             TEXT NOT NULL,
            auth                  JSONB,
            active                BOOLEAN NOT NULL DEFAULT TRUE,
            note                  TEXT NOT NULL DEFAULT '',
            created_by            INTEGER NOT NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_enqueued_at      TIMESTAMPTZ,
            last_job_id           UUID,
            last_skipped_at       TIMESTAMPTZ,
            skips_since_last_run  INTEGER NOT NULL DEFAULT 0,
            error_state           TEXT
                CONSTRAINT ui_run_schedules_error_known
                CHECK (error_state IS NULL OR error_state IN
                       ('enqueue_failed', 'dead_authority')),
            last_error            TEXT,
            deactivated_reason    TEXT,
            deactivated_at        TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX ui_run_schedules_active_idx
            ON ui_run_schedules (active) WHERE active
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ui_run_schedules")

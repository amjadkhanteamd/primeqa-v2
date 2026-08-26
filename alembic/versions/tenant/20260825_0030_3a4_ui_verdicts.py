"""3A-4 — claim-grain verdict tables (LLD 3A-4 §f).

Two tenant-schema tables, deliberately NOT an extension of the
surface-grain ``s4_ui_inspection_results`` (grain mismatch: one surface
observation fans out to many member verdicts):

  - s6_ui_verdicts: one row per (job, member claim). UNIQUE(job_id,
    test_id) makes reprocessing an idempotent UPSERT. ``ownership`` is
    the DE-11 origin marker (FAIL rows only; NULL elsewhere).
    ``evidence_state_at_write`` records what the processor saw; the
    reporting read JOINs the LIVE evidence state (2.5 law: no verdict
    presented evidence-complete without REFERENCED evidence).
  - s6_ui_processing_runs: one row per processed job — the honest
    remainder ledger (unmapped engine ids, per-status surface counts,
    members that got NO verdict and why) plus the bindings snapshot
    hash so a changed reprocess outcome is attributable.

Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260825_0030"
down_revision = "20260825_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE s6_ui_verdicts (
            id                      UUID PRIMARY KEY,
            manifest_id             UUID NOT NULL,
            job_id                  UUID NOT NULL,
            surface_key             TEXT NOT NULL,
            claim_set_id            UUID NOT NULL,
            test_id                 UUID NOT NULL,
            plimsol_rule_id         TEXT NOT NULL,
            verdict                 TEXT NOT NULL
                CONSTRAINT s6_ui_verdicts_verdict_known
                CHECK (verdict IN
                       ('PASS','FAIL','NEEDS_HUMAN','NOT_DETERMINED')),
            verdict_basis           JSONB NOT NULL DEFAULT '{}'::jsonb,
            ownership               TEXT
                CONSTRAINT s6_ui_verdicts_ownership_known
                CHECK (ownership IS NULL OR ownership IN
                       ('CONFIRMED','PROBABLE','UNKNOWN')),
            evidence_state_at_write TEXT,
            processed_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (job_id, test_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_s6_ui_verdicts_claim_set
        ON s6_ui_verdicts (claim_set_id, verdict)
    """)
    op.execute("""
        CREATE TABLE s6_ui_processing_runs (
            job_id              UUID PRIMARY KEY,
            manifest_id         UUID NOT NULL,
            claim_set_id        UUID NOT NULL,
            engine              TEXT NOT NULL,
            engine_version      TEXT NOT NULL,
            bindings_hash       TEXT NOT NULL,
            unmapped_engine_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            surface_statuses    JSONB NOT NULL DEFAULT '{}'::jsonb,
            verdict_counts      JSONB NOT NULL DEFAULT '{}'::jsonb,
            no_verdict_members  JSONB NOT NULL DEFAULT '{}'::jsonb,
            processed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS s6_ui_processing_runs")
    op.execute("DROP TABLE IF EXISTS s6_ui_verdicts")

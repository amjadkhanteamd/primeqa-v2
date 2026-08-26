"""Phase 7 — org-environment snapshots + comparison tables (LLD §c/§e).

  - org_environment_snapshots: the run-scoped environmental context the
    causal comparator diffs (platform api version, organization facts,
    installed-package inventory). Immutable, hash-keyed: identical
    content reuses the existing row. Captured at MANIFEST BUILD time
    (the D-461 pin philosophy); manifests reference it via
    pins.org_env_snapshot_id.
  - s6_ui_comparison_runs: one immutable row per (baseline, candidate)
    job pair; UNIQUE on the pair makes re-compare an idempotent
    byte-identical UPSERT (the 3A-4 reprocess posture). tool_drift and
    env_delta record the subtracted dimensions; a refused comparison
    (cross-inventory) is a recorded refusal, not an error.
  - s6_ui_verdict_transitions: the per-claim taxonomy rows; the causal
    JSONB carries primary/confidence/contributing/evidence (DE-13).

Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260826_0010"
down_revision = "20260825_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE org_environment_snapshots (
            id                   UUID PRIMARY KEY,
            captured_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            platform_api_version TEXT,
            organization         JSONB NOT NULL DEFAULT '{}'::jsonb,
            packages             JSONB NOT NULL DEFAULT '[]'::jsonb,
            content_hash         TEXT NOT NULL UNIQUE
        )
    """)
    op.execute("""
        CREATE TABLE s6_ui_comparison_runs (
            id                     UUID PRIMARY KEY,
            baseline_job_id        UUID NOT NULL,
            candidate_job_id       UUID NOT NULL,
            baseline_claim_set_id  UUID,
            candidate_claim_set_id UUID,
            inventory_version      INTEGER,
            outcome                TEXT NOT NULL
                CONSTRAINT s6_ui_comparison_runs_outcome_known
                CHECK (outcome IN ('completed','refused')),
            refusal_reason         TEXT,
            tool_drift             JSONB NOT NULL DEFAULT '{}'::jsonb,
            env_delta              JSONB NOT NULL DEFAULT '{}'::jsonb,
            transition_counts      JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (baseline_job_id, candidate_job_id)
        )
    """)
    op.execute("""
        CREATE TABLE s6_ui_verdict_transitions (
            comparison_id     UUID NOT NULL
                REFERENCES s6_ui_comparison_runs(id),
            test_id           UUID NOT NULL,
            transition        TEXT NOT NULL
                CONSTRAINT s6_ui_verdict_transitions_kind_known
                CHECK (transition IN
                       ('NEW_FAIL','FIXED','STILL_FAILING','STILL_PASSING',
                        'NEW_CLAIM','RETIRED_CLAIM','NOT_COMPARABLE',
                        'NOT_RUN')),
            from_verdict      TEXT,
            to_verdict        TEXT,
            drift             BOOLEAN NOT NULL DEFAULT FALSE,
            fingerprint_delta JSONB,
            causal            JSONB,
            surface_key       TEXT,
            plimsol_rule_id   TEXT,
            PRIMARY KEY (comparison_id, test_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_s6_ui_verdict_transitions_kind
        ON s6_ui_verdict_transitions (transition)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS s6_ui_verdict_transitions")
    op.execute("DROP TABLE IF EXISTS s6_ui_comparison_runs")
    op.execute("DROP TABLE IF EXISTS org_environment_snapshots")

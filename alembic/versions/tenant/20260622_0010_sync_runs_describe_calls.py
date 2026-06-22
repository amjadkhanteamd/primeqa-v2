"""Phase-1 close-out #4a: sync_runs.describe_calls acceptance-gate telemetry.

Revision ID: 20260622_0010
Revises: 20260620_0010
Create Date: 2026-06-22

Adds one additive per-tenant-schema column:

  * ``sync_runs.describe_calls INTEGER NOT NULL DEFAULT 0`` — the count of
    describe-class Salesforce API calls (global describe, per-object describe,
    bulk ``/composite/batch`` describe, ``describe/layouts``) made during the
    sync run. The SF client accumulates the count across the run's phases and the
    engine flushes it once at structural completion via
    ``readiness.increment_run_counters(describe_calls_delta=...)``. Sits alongside
    the existing enrichment counters (embeddings_generated, summaries_generated,
    summaries_failed). Lets the resync acceptance gate measure
    "unchanged resync → describe_calls ≤ 1". SOQL / tooling-query calls are NOT
    counted (they aren't describe-class).

Idempotent (ADD COLUMN IF NOT EXISTS) per the migrations-016+ convention.
Additive, reversible, deploy-safe (NOT NULL DEFAULT 0 backfills existing rows).
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260622_0010'
down_revision = '20260620_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE sync_runs "
        "ADD COLUMN IF NOT EXISTS describe_calls INTEGER NOT NULL DEFAULT 0")
    op.execute(
        "COMMENT ON COLUMN sync_runs.describe_calls IS "
        "'#4a: count of describe-class Salesforce API calls (global / per-object "
        "/ composite-batch / describe-layouts) made during this run, accumulated "
        "by the SF client and flushed once at structural completion. Excludes "
        "SOQL/tooling queries. Acceptance-gate telemetry; never load-bearing.'")


def downgrade():
    op.execute("ALTER TABLE sync_runs DROP COLUMN IF EXISTS describe_calls")

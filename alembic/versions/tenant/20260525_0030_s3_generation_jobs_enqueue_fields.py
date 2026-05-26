"""substrate-3 production integration: s3_generation_jobs enqueue fields (D-106.4 slice 3)

Revision ID: 20260525_0030
Revises: 20260525_0020
Create Date: 2026-05-25

Per DECISIONS_LOG.md D-106.4 (+ slice-3 amendment). Two enqueue-pinned columns
the consumer reads:

  - requirement_text — the requirement text the consumer feeds into the
    GenerationRequest (option B: S3 is caller-fed; the consumer must NOT re-read
    v1 public.requirements). Populated at enqueue (slice 4, v1-side).
  - environment_id — the api_key resolution handle. The Anthropic key is
    environment -> connection-scoped (Fernet-decrypted via the v1 connection
    store); the consumer resolves it worker-side via the v1 repos. A loose int
    (the environments table is v1-side) — never reaches the ledger / request.

Both nullable (pinned at enqueue). environment_id is a determined attribute
(s1_version_seq implies the env), NOT part of the UNIQUE key — that constraint
is unchanged.

Tenant-branch migration; per-tenant. Idempotent (ADD COLUMN IF NOT EXISTS).
"""
from alembic import op


revision = '20260525_0030'
down_revision = '20260525_0020'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE s3_generation_jobs ADD COLUMN IF NOT EXISTS requirement_text TEXT")
    op.execute("ALTER TABLE s3_generation_jobs ADD COLUMN IF NOT EXISTS environment_id INTEGER")
    op.execute(
        "COMMENT ON COLUMN s3_generation_jobs.requirement_text IS "
        "'Enqueue-pinned requirement text (D-106.4 option B: caller-fed; the "
        "consumer does not re-read v1 public.requirements).'"
    )
    op.execute(
        "COMMENT ON COLUMN s3_generation_jobs.environment_id IS "
        "'api_key resolution handle (D-106.4 slice 3): env -> connection -> "
        "Fernet-decrypted key, resolved worker-side. Loose int; never in the ledger.'"
    )


def downgrade():
    op.execute("ALTER TABLE s3_generation_jobs DROP COLUMN IF EXISTS environment_id")
    op.execute("ALTER TABLE s3_generation_jobs DROP COLUMN IF EXISTS requirement_text")

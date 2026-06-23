"""Phase 2 Slice A: typed failure taxonomy columns on the run records.

Revision ID: 20260624_0010
Revises: 20260623_0010
Create Date: 2026-06-24

Adds the queryable typed-failure columns so a sync/execution failure's CLASS
(auth / permission / transient / rate_limit / normalization / unknown) is
distinguishable in the run record — not only embedded in freetext / JSONB:

  * ``sync_runs.failure_category VARCHAR``      (nullable; NULL = no failure)
  * ``sync_runs.sf_error_code VARCHAR``         (nullable; the SF ``errorCode``)
  * ``s4_execution_runs.failure_category VARCHAR`` (nullable; NULL = no transport error)
  * ``s4_execution_runs.sf_error_code VARCHAR``    (nullable)

The category vocabulary is owned by ``primeqa.integrations.failure_taxonomy``
(code is the source of truth — no DB CHECK, so a later slice can extend the
vocabulary without a migration). The existing freetext ``sync_runs.error_message``
/ ``error_traceback`` and the ``s4_execution_runs.evidence`` JSONB are KEPT — these
columns are additive promotions, not replacements.

Additive, nullable, deploy-safe. NO backfill — historical runs stay NULL (their
typed category was never captured; we do not parse old freetext). Idempotent
(ADD COLUMN IF NOT EXISTS) per the migrations-016+ convention.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260624_0010'
down_revision = '20260623_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE sync_runs "
        "ADD COLUMN IF NOT EXISTS failure_category VARCHAR")
    op.execute(
        "ALTER TABLE sync_runs "
        "ADD COLUMN IF NOT EXISTS sf_error_code VARCHAR")
    op.execute(
        "ALTER TABLE s4_execution_runs "
        "ADD COLUMN IF NOT EXISTS failure_category VARCHAR")
    op.execute(
        "ALTER TABLE s4_execution_runs "
        "ADD COLUMN IF NOT EXISTS sf_error_code VARCHAR")
    op.execute(
        "COMMENT ON COLUMN sync_runs.failure_category IS "
        "'Phase 2 Slice A: typed failure class (auth/permission/transient/"
        "rate_limit/normalization/unknown) classified from the run''s exception "
        "via integrations.failure_taxonomy.classify_failure. NULL = no failure. "
        "Vocabulary owned in code (no CHECK). Sits beside the freetext "
        "error_message.'")
    op.execute(
        "COMMENT ON COLUMN sync_runs.sf_error_code IS "
        "'Phase 2 Slice A: the Salesforce errorCode (e.g. INSUFFICIENT_FIELD_ACCESS) "
        "when one was parsed from the failure; NULL otherwise.'")
    op.execute(
        "COMMENT ON COLUMN s4_execution_runs.failure_category IS "
        "'Phase 2 Slice A: typed failure class promoted from the run''s top-level "
        "error surface (evidence.error) via integrations.failure_taxonomy. NULL for "
        "runs with no transport error (passed / assertion-failed / business "
        "rejection). The full per-step trace stays in evidence JSONB.'")
    op.execute(
        "COMMENT ON COLUMN s4_execution_runs.sf_error_code IS "
        "'Phase 2 Slice A: the Salesforce errorCode of the erroring step (D-225 "
        "captured it on the mutation-attempt evidence); NULL otherwise.'")


def downgrade():
    op.execute("ALTER TABLE s4_execution_runs DROP COLUMN IF EXISTS sf_error_code")
    op.execute("ALTER TABLE s4_execution_runs DROP COLUMN IF EXISTS failure_category")
    op.execute("ALTER TABLE sync_runs DROP COLUMN IF EXISTS sf_error_code")
    op.execute("ALTER TABLE sync_runs DROP COLUMN IF EXISTS failure_category")

"""Phase 2 Slice B: surface swallowed metadata-fetch gaps on sync_runs.

Revision ID: 20260624_0020
Revises: 20260624_0010
Create Date: 2026-06-24

Makes the previously-silent metadata-fetch swallows (a permission/FLS denial that
dropped a field's metadata or a permission-set edge, hidden behind a log.warning)
VISIBLE and typed on the run record:

  * ``sync_runs.permission_gaps INT NOT NULL DEFAULT 0`` — count of GENUINE
    data-loss gaps surfaced this run (a typed failure that dropped real data; a
    benign ``unknown`` gap — e.g. a 404 for a feature the org lacks — is recorded
    in gap_details but does NOT count here). ``> 0`` finalizes the run
    ``partial_success`` (the existing terminal status, previously used only for
    ``failed_permanent`` enrichment rows) instead of ``success``-with-silent-loss.
  * ``sync_runs.gap_details JSONB`` (nullable) — the list of surfaced gaps,
    ``{site, category, sf_error_code, context}`` (what was dropped, classified via
    integrations.failure_taxonomy).

Additive, deploy-safe. ``permission_gaps`` carries ``DEFAULT 0`` so old rows + the
currently-deployed code (which never SETs it) are correct; the NEW code SETs it on
the success-path finalize, so this is applied MIGRATE-FIRST (same as Slice A) — the
column must exist before the new code's finalize writes it. ``gap_details`` is
nullable (NULL = no gaps). Idempotent (ADD COLUMN IF NOT EXISTS).
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260624_0020'
down_revision = '20260624_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE sync_runs "
        "ADD COLUMN IF NOT EXISTS permission_gaps INTEGER NOT NULL DEFAULT 0")
    op.execute(
        "ALTER TABLE sync_runs "
        "ADD COLUMN IF NOT EXISTS gap_details JSONB")
    op.execute(
        "COMMENT ON COLUMN sync_runs.permission_gaps IS "
        "'Phase 2 Slice B: count of GENUINE data-loss metadata-fetch gaps surfaced "
        "this run (a typed failure that dropped real data — a benign unknown gap is "
        "recorded in gap_details but not counted). >0 finalizes the run "
        "partial_success. Default 0. Per-org via source_org_id.'")
    op.execute(
        "COMMENT ON COLUMN sync_runs.gap_details IS "
        "'Phase 2 Slice B: list of surfaced metadata-fetch swallows "
        "{site, category, sf_error_code, context} — what was dropped, classified via "
        "integrations.failure_taxonomy. NULL = no gaps.'")


def downgrade():
    op.execute("ALTER TABLE sync_runs DROP COLUMN IF EXISTS gap_details")
    op.execute("ALTER TABLE sync_runs DROP COLUMN IF EXISTS permission_gaps")

"""1b.1 S1 sync skip-gate: connected_orgs.setup_audit_watermark + sync_runs.skipped

Revision ID: 20260619_0010
Revises: 20260614_0030
Create Date: 2026-06-19

The S1 sync engine gains an org-level skip gate: if Salesforce's SetupAuditTrail
shows no setup changes since the org's last confirmed sync, the whole sync is
skipped (no phases, no enrichment). Two additive per-tenant-schema columns
support it:

  * ``connected_orgs.setup_audit_watermark TIMESTAMPTZ NULL`` — the Salesforce
    *server* time of the org's last confirmed setup state. NULL = never synced
    (always run a full sync). Advanced — to the SF server time captured at fetch
    start — only on a successful full sync; left unchanged on a skip. TIMESTAMPTZ
    (not bare TIMESTAMP) because it is an absolute UTC instant from SF's clock,
    matching the rest of this schema's TIMESTAMPTZ columns.

  * ``sync_runs.skipped BOOLEAN NOT NULL DEFAULT false`` — true marks an
    org-level skip. The run is still terminal ``status='success'`` (so the
    D-183 staleness clock, which reads ``status IN ('success','partial_success')``,
    counts it as a recent successful sync) but zero phases / enrichment ran.
    A flag, not a new ``status`` enum value: a 'skipped' status would be invisible
    to that staleness reader's ``status IN (...)`` filter AND would violate the
    ``sync_runs_completion_implies_terminal`` CHECK — so a flag is both correct
    and lower-blast-radius.

Idempotent (ADD COLUMN IF NOT EXISTS) per the migrations-016+ convention.
Additive + reversible.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260619_0010'
down_revision = '20260614_0030'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE connected_orgs "
        "ADD COLUMN IF NOT EXISTS setup_audit_watermark TIMESTAMPTZ")
    op.execute(
        "COMMENT ON COLUMN connected_orgs.setup_audit_watermark IS "
        "'1b.1: Salesforce server time of the last confirmed setup state. "
        "NULL = never synced. The sync engine skips a full sync when "
        "SetupAuditTrail has no CreatedDate > this watermark; advanced to the "
        "SF server time captured at fetch start on a successful full sync, left "
        "unchanged on a skip.'")
    op.execute(
        "ALTER TABLE sync_runs "
        "ADD COLUMN IF NOT EXISTS skipped BOOLEAN NOT NULL DEFAULT false")
    op.execute(
        "COMMENT ON COLUMN sync_runs.skipped IS "
        "'1b.1: true = org-level skip (no setup changes since the watermark). "
        "Terminal status=success / phase=done with zero counters and no "
        "enrichment; a flag (not a status enum value) so the skip is visible in "
        "history while the staleness clock still counts the run.'")


def downgrade():
    op.execute("ALTER TABLE sync_runs DROP COLUMN IF EXISTS skipped")
    op.execute(
        "ALTER TABLE connected_orgs DROP COLUMN IF EXISTS setup_audit_watermark")

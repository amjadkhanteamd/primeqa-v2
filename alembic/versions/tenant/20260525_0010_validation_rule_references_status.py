"""substrate-1: add references_status to validation_rule_details

Revision ID: 20260525_0010
Revises: 20260522_0040
Create Date: 2026-05-25

Per DECISIONS_LOG.md D-107 (.3 / 3a). Closing corrections-log §17's REFERENCES
edge needs a way to record, per ValidationRule, whether REFERENCES extraction is
known-complete. `references_status` is a 4-state marker on validation_rule_details:

  - 'pending'   — REFERENCES extraction has not run for this row yet (the
                  honest default: rows that predate the writer, or any VR not
                  yet re-synced). NOT a claim about the formula — just
                  "not evaluated." The sync-time writer overwrites it.
  - 'complete'  — formula fully parsed; every field ref it implies became a
                  validation_rule_field_refs row (same-object refs; ISNEW
                  references no field and does not reduce completeness).
  - 'partial'   — formula parsed, but >= 1 ref could not be materialized:
                  a cross-object dotted ref (deferred, D-107 / 2a) or a
                  same-object ref that did not resolve to a synced Field entity.
  - 'unparsed'  — the formula did not fully parse (fail-loud, D-107 .1): no
                  partial / guessed edges; REFERENCES is known-incomplete.

Default 'pending' (distinct from 'unparsed', which is a real parse verdict the
writer sets): an un-evaluated row must not masquerade as a failed parse.

Tenant-branch migration; validation_rule_details lives in the per-tenant schema.
Idempotent (ADD COLUMN IF NOT EXISTS + guarded CHECK). Schema only.
"""
from alembic import op


revision = "20260525_0010"
down_revision = "20260522_0040"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE validation_rule_details "
        "ADD COLUMN IF NOT EXISTS references_status VARCHAR(10) "
        "NOT NULL DEFAULT 'pending'"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
              SELECT 1 FROM pg_constraint
              WHERE conname = 'validation_rule_details_references_status_known'
          ) THEN
            ALTER TABLE validation_rule_details
              ADD CONSTRAINT validation_rule_details_references_status_known
              CHECK (references_status IN ('pending', 'complete', 'partial', 'unparsed'));
          END IF;
        END $$;
        """
    )


def downgrade():
    op.execute(
        "ALTER TABLE validation_rule_details "
        "DROP CONSTRAINT IF EXISTS validation_rule_details_references_status_known"
    )
    op.execute(
        "ALTER TABLE validation_rule_details DROP COLUMN IF EXISTS references_status"
    )

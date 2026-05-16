"""Phase 2 tenant schema: add 'sync_run' to logical_versions version_type enum.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §§3-5 and the Object phase
cycle: the sync engine allocates one logical_versions row per
sync_run; the existing enum values (genesis, deploy_detected,
sandbox_refresh, manual_checkpoint, scheduled_milestone) don't
semantically match. Adds 'sync_run' as the canonical
version_type for sync-allocated versions.

Rejected alternative: re-using 'deploy_detected' for
sync-allocated versions. Operationally close (sync runs detect
Salesforce metadata changes) but semantic-mismatch debt
accumulates. Future contributors reading
version_type='deploy_detected' on a sync-allocated row would
either trust the name and write incorrect downstream logic, or
spend time discovering the convention through tribal knowledge.
Explicit naming is worth the migration cost.

Single change: ALTER the existing logical_versions_version_type_known
CHECK constraint (created in 20260427_0010 phase0_foundation_tables)
to widen its allowed-value list. Migration drops the constraint and
re-adds it with the wider enum; Postgres doesn't support ALTER CHECK
in place.

Revision ID: 20260512_0020
Revises: 20260512_0010
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260512_0020"
down_revision: Union[str, Sequence[str], None] = "20260512_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE logical_versions
            DROP CONSTRAINT logical_versions_version_type_known
    """)
    op.execute("""
        ALTER TABLE logical_versions
            ADD CONSTRAINT logical_versions_version_type_known
            CHECK (
                version_type IN (
                    'genesis',
                    'deploy_detected',
                    'sandbox_refresh',
                    'manual_checkpoint',
                    'scheduled_milestone',
                    'sync_run'
                )
            )
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE logical_versions
            DROP CONSTRAINT logical_versions_version_type_known
    """)
    op.execute("""
        ALTER TABLE logical_versions
            ADD CONSTRAINT logical_versions_version_type_known
            CHECK (
                version_type IN (
                    'genesis',
                    'deploy_detected',
                    'sandbox_refresh',
                    'manual_checkpoint',
                    'scheduled_milestone'
                )
            )
    """)

"""ui-s2.5: evidence custody columns on s4_ui_inspection_results (spike).

Adds the CAPTURED -> UPLOADED -> VERIFIED -> REFERENCED lifecycle state
plus the object keys / checksums / sizes / content types that make a
result row a REFERENCE to verified evidence objects. Default state is
EVIDENCE_INCOMPLETE: a row carrying no evidence record is incomplete by
definition, never silently complete.

Production has never had this table (2.3+2.4+2.5 land at branch merge,
MIGRATE-FIRST per D-285). Design: docs/ui-testing/LLD_EVIDENCE_STORE.md.
"""

from alembic import op

revision = '20260823_0020'
down_revision = '20260823_0010'
branch_labels = None
depends_on = None

_COLS = ("evidence_state", "evidence_keys", "evidence_checksums",
         "evidence_sizes", "evidence_content_types", "evidence_detail",
         "evidence_verified_at")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE s4_ui_inspection_results
            ADD COLUMN evidence_state VARCHAR(24) NOT NULL
                DEFAULT 'EVIDENCE_INCOMPLETE',
            ADD COLUMN evidence_keys JSONB,
            ADD COLUMN evidence_checksums JSONB,
            ADD COLUMN evidence_sizes JSONB,
            ADD COLUMN evidence_content_types JSONB,
            ADD COLUMN evidence_detail JSONB,
            ADD COLUMN evidence_verified_at TIMESTAMPTZ,
            ADD CONSTRAINT s4_ui_inspection_results_evidence_state_known CHECK (
                evidence_state IN ('CAPTURED', 'UPLOADED', 'VERIFIED',
                                   'REFERENCED', 'EVIDENCE_INCOMPLETE')
            ),
            ADD CONSTRAINT s4_ui_inspection_results_referenced_has_keys CHECK (
                evidence_state <> 'REFERENCED'
                OR (evidence_keys IS NOT NULL AND evidence_checksums IS NOT NULL
                    AND evidence_sizes IS NOT NULL
                    AND evidence_verified_at IS NOT NULL)
            )
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE s4_ui_inspection_results
            DROP CONSTRAINT IF EXISTS s4_ui_inspection_results_referenced_has_keys,
            DROP CONSTRAINT IF EXISTS s4_ui_inspection_results_evidence_state_known
    """)
    for col in _COLS:
        op.execute(f"ALTER TABLE s4_ui_inspection_results DROP COLUMN IF EXISTS {col}")

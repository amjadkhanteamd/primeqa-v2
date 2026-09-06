"""Step A — the repair-proposal three-verdict gate (LLD_STEP_A_REPAIR_GATE).

ADDITIVE on ``repair_proposals``: every proposal carries a GATE VERDICT derived
from recorded facts (S1, the platform error string, the claim's asserted
fields) — DERIVED / SPECULATIVE / SEMANTIC — plus the grounding source
that produced it, the classifier version, and the retro-revert record
(D3: a machine-applied edit whose retro verdict is not DERIVED is
reverted to its pre-edit content as a NEW recipe version).

``gate_verdict`` (NOT ``verdict`` — that column already holds the S6
verdict the proposal was triaged from) is NULLABLE on purpose: a default verdict would be a lie
about the rows that predate the gate. Code treats NULL as NOT
APPLICABLE; retro-classification fills every row; a later migration may
tighten to NOT NULL once every tenant has run retro.

``confidence`` stays as an AUDIT column — the LLM's self-report — and is
NON-DECISIONAL from this revision: no apply path reads it.

Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260906_0010"
down_revision = "20260904_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE repair_proposals
            ADD COLUMN IF NOT EXISTS gate_verdict TEXT
                CONSTRAINT repair_proposals_gate_verdict_known
                CHECK (gate_verdict IS NULL OR gate_verdict IN
                       ('DERIVED', 'SPECULATIVE', 'SEMANTIC')),
            ADD COLUMN IF NOT EXISTS grounding_source JSONB,
            ADD COLUMN IF NOT EXISTS classified_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS classifier_version TEXT,
            ADD COLUMN IF NOT EXISTS revert_recipe_version_seq INTEGER,
            ADD COLUMN IF NOT EXISTS reverted_at TIMESTAMPTZ
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_repair_proposals_status_gate_verdict
            ON repair_proposals (status, gate_verdict)
    """)
    op.execute("""
        COMMENT ON COLUMN repair_proposals.confidence IS
        'AUDIT ONLY — the LLM''s self-reported confidence, parsed from its '
        'output. NON-DECISIONAL since Step A (LLD_STEP_A_REPAIR_GATE): no '
        'apply path reads it; the gate_verdict column decides applicability.'
    """)
    op.execute("""
        COMMENT ON COLUMN repair_proposals.gate_verdict IS
        'Step A gate verdict: DERIVED (remedy derived from a recorded fact; '
        'applicable), SPECULATIVE (inference or a chosen value; operator '
        'edits), SEMANTIC (touches an asserted field or unclassifiable; '
        'refused). NULL = unclassified = NOT applicable.'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_repair_proposals_status_gate_verdict")
    op.execute("""
        ALTER TABLE repair_proposals
            DROP COLUMN IF EXISTS reverted_at,
            DROP COLUMN IF EXISTS revert_recipe_version_seq,
            DROP COLUMN IF EXISTS classifier_version,
            DROP COLUMN IF EXISTS classified_at,
            DROP COLUMN IF EXISTS grounding_source,
            DROP COLUMN IF EXISTS gate_verdict
    """)

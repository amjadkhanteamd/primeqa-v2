"""repair_proposals LLM-proposal columns (theme #6, D-236)

Revision ID: 20260614_0030
Revises: 20260614_0020
Create Date: 2026-06-14

Per DECISIONS_LOG.md D-236. The D-215.1 spine's deterministic proposals
(rerun / regenerate_from_current_org) carried no LLM detail. The auto-fix
agent's LLM layer adds a `recipe_edit` proposal kind that needs to record the
model's CONFIDENCE (0–1), the proposed edit (`proposed_payload`), and whether
the apply was AUTONOMOUS (`auto_applied`, vs a human Approve).

Additive on the per-tenant `repair_proposals` table (alembic tenant branch).
The repair-agent reads these best-effort, so the code deploys before this is
applied (the D-230 ordering — a missing column reads as "no LLM detail"). The
per-tenant auto-apply FLAG + the trust thresholds + the attempt cap live on the
PUBLIC `tenant_agent_settings` (migrations/054), reusing the v1 agent's existing
`trust_threshold_high` / `max_fix_attempts_per_run` columns.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260614_0030'
down_revision = '20260614_0020'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE repair_proposals "
        "ADD COLUMN IF NOT EXISTS confidence NUMERIC, "
        "ADD COLUMN IF NOT EXISTS proposed_payload JSONB NOT NULL DEFAULT '{}'::jsonb, "
        "ADD COLUMN IF NOT EXISTS auto_applied BOOLEAN NOT NULL DEFAULT false")


def downgrade():
    op.execute(
        "ALTER TABLE repair_proposals "
        "DROP COLUMN IF EXISTS confidence, "
        "DROP COLUMN IF EXISTS proposed_payload, "
        "DROP COLUMN IF EXISTS auto_applied")

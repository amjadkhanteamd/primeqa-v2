"""1c: split the enrichment gate — embed-input + summary-input hashes.

Adds the per-entity-version hashes that let the materialize gate carry a prior
version's embedding/summary forward when the *exact enrichment input* is
unchanged, instead of re-embedding / re-summarizing on every changed entity:

  - entities.semantic_text_hash VARCHAR(64) — SHA-256 of the EXACT embed input
    (entities.semantic_text, the string the worker feeds Voyage). On a changed
    entity, if this matches the prior version's, the prior embedding is carried
    forward (no Voyage call).
  - flow_details.summary_input_hash / validation_rule_details.summary_input_hash
    VARCHAR(64) — SHA-256 of the EXACT summary input (the composite context the
    worker assembles + feeds the summary LLM). On a changed Flow/VR, if this
    matches the prior version's, the prior summary is carried forward (no LLM
    call). Lives on the detail tables, beside summary_text/summary_embedding.

All three are nullable + additive (ADD COLUMN IF NOT EXISTS) → DEPLOY-SAFE: a
missing column makes the gate find no prior hash → it re-enriches exactly like
today (no carry-forward, never stale). Rows synced before this migration have
NULL hashes; their next change re-enriches once, then carry-forward engages.

See DECISIONS_LOG D-252.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260620_0010'
down_revision = '20260619_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE entities "
        "ADD COLUMN IF NOT EXISTS semantic_text_hash VARCHAR(64)")
    op.execute(
        "COMMENT ON COLUMN entities.semantic_text_hash IS "
        "'1c: SHA-256 of the exact embed input (semantic_text). The materialize "
        "gate carries a prior version''s embedding forward when this is unchanged "
        "(no Voyage call). NULL = pre-1c row / never hashed → re-embed.'")
    op.execute(
        "ALTER TABLE flow_details "
        "ADD COLUMN IF NOT EXISTS summary_input_hash VARCHAR(64)")
    op.execute(
        "COMMENT ON COLUMN flow_details.summary_input_hash IS "
        "'1c: SHA-256 of the exact summary-LLM input (the composite context). "
        "Carries a prior version''s summary forward when unchanged (no LLM call).'")
    op.execute(
        "ALTER TABLE validation_rule_details "
        "ADD COLUMN IF NOT EXISTS summary_input_hash VARCHAR(64)")
    op.execute(
        "COMMENT ON COLUMN validation_rule_details.summary_input_hash IS "
        "'1c: SHA-256 of the exact summary-LLM input (the composite context). "
        "Carries a prior version''s summary forward when unchanged (no LLM call).'")


def downgrade():
    op.execute(
        "ALTER TABLE validation_rule_details DROP COLUMN IF EXISTS summary_input_hash")
    op.execute("ALTER TABLE flow_details DROP COLUMN IF EXISTS summary_input_hash")
    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS semantic_text_hash")

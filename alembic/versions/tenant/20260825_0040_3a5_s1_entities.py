"""3A-5 — S1 entity types Surface + LightningComponentBundle (LLD 3A-5 §a).

The D-308 recipe applied — with one grounding correction to the LLD:
``entities.entity_type`` carries NO db CHECK (the closed list there is
application-side: the normalization / presentation / semantic-text
registries fail-closed on unknown types). The two REAL closed lists
widen here:

  - sync_runs.last_completed_phase: +LightningComponentBundle ONLY —
    Surface is a DECLARED entity with no sync phase and must never be
    legal there.
  - ai_enrichment_queue.entity_type: +LightningComponentBundle ONLY —
    the materialize enqueue is unconditional for synced types; Surface
    rows are never enqueued (declared entities carry their own
    description).

Plus the DE-11 CONFIRMED-tier column: s6_ui_verdicts.owner_bundle_ref
(nullable UUID — the resolved bundle entity id, set exactly when the
tag→DeveloperName join resolves).

MIGRATE-FIRST (D-285): prod tenants take this before any code that
writes the new types deploys. Plain DDL in env.py's transaction
(D-459: no autocommit_block).
"""
from alembic import op

revision = "20260825_0040"
down_revision = "20260825_0030"
branch_labels = None
depends_on = None

_OLD_13 = ("'Object','PicklistValueSet','PicklistValue','Field',"
           "'RecordType','Layout','ValidationRule','Profile',"
           "'PermissionSet','User','FlowDefinition','Flow',"
           "'ApprovalProcess'")
_NEW_SYNCED = _OLD_13 + ",'LightningComponentBundle'"


def upgrade() -> None:
    op.execute("ALTER TABLE sync_runs DROP CONSTRAINT IF EXISTS "
               "sync_runs_last_completed_phase_known")
    op.execute(f"ALTER TABLE sync_runs "
               f"ADD CONSTRAINT sync_runs_last_completed_phase_known "
               f"CHECK (last_completed_phase IS NULL "
               f"OR last_completed_phase IN ({_NEW_SYNCED}))")

    op.execute("ALTER TABLE ai_enrichment_queue DROP CONSTRAINT IF EXISTS "
               "ai_enrichment_queue_entity_type_known")
    op.execute(f"ALTER TABLE ai_enrichment_queue "
               f"ADD CONSTRAINT ai_enrichment_queue_entity_type_known "
               f"CHECK (entity_type IN ({_NEW_SYNCED}))")

    op.execute("ALTER TABLE s6_ui_verdicts "
               "ADD COLUMN IF NOT EXISTS owner_bundle_ref UUID")


def downgrade() -> None:
    op.execute("ALTER TABLE s6_ui_verdicts "
               "DROP COLUMN IF EXISTS owner_bundle_ref")
    op.execute("DELETE FROM ai_enrichment_queue "
               "WHERE entity_type = 'LightningComponentBundle'")
    op.execute("ALTER TABLE ai_enrichment_queue DROP CONSTRAINT IF EXISTS "
               "ai_enrichment_queue_entity_type_known")
    op.execute(f"ALTER TABLE ai_enrichment_queue "
               f"ADD CONSTRAINT ai_enrichment_queue_entity_type_known "
               f"CHECK (entity_type IN ({_OLD_13}))")
    op.execute("ALTER TABLE sync_runs DROP CONSTRAINT IF EXISTS "
               "sync_runs_last_completed_phase_known")
    op.execute(f"ALTER TABLE sync_runs "
               f"ADD CONSTRAINT sync_runs_last_completed_phase_known "
               f"CHECK (last_completed_phase IS NULL "
               f"OR last_completed_phase IN ({_OLD_13}))")
    op.execute("DELETE FROM entities "
               "WHERE entity_type IN ('Surface','LightningComponentBundle')")

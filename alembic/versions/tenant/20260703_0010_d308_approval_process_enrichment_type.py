"""D-308: widen ai_enrichment_queue's entity-type CHECK for 'ApprovalProcess'.

The D-307/D-308 lever adds the ApprovalProcess entity type (row-level
approval-process definitions on the TRIGGERS_ON rail). The generic
entities/edges store needs no DDL, but the enrichment queue's
``ai_enrichment_queue_entity_type_known`` CHECK is a closed list — the
first live sync from the branch failed loud on it (exactly the "verify
no CHECK constrains the type" caveat the D-308 entry carried).

MIGRATE-FIRST (D-285): the sync WRITES this type the moment the branch
code runs, so tenants must carry the widened CHECK before that code
executes against them.

Tenant-branch migration; down_revision = the prior tenant head
(20260702_0010). Downgrade restores the original list (safe only if no
ApprovalProcess rows are queued; it deletes them first — the queue is
re-populated by the next sync, so no data is lost that a sync cannot
recreate).
"""
from alembic import op

revision = '20260703_0010'
down_revision = '20260702_0010'
branch_labels = None
depends_on = None

_OLD = ("'Object', 'PicklistValueSet', 'PicklistValue', 'Field', "
        "'RecordType', 'Layout', 'ValidationRule', 'Profile', "
        "'PermissionSet', 'User', 'FlowDefinition', 'Flow'")
_NEW = _OLD + ", 'ApprovalProcess'"


def upgrade():
    op.execute(
        "ALTER TABLE ai_enrichment_queue "
        "DROP CONSTRAINT IF EXISTS ai_enrichment_queue_entity_type_known"
    )
    op.execute(
        f"ALTER TABLE ai_enrichment_queue "
        f"ADD CONSTRAINT ai_enrichment_queue_entity_type_known "
        f"CHECK (entity_type IN ({_NEW}))"
    )


def downgrade():
    op.execute(
        "DELETE FROM ai_enrichment_queue WHERE entity_type = 'ApprovalProcess'"
    )
    op.execute(
        "ALTER TABLE ai_enrichment_queue "
        "DROP CONSTRAINT IF EXISTS ai_enrichment_queue_entity_type_known"
    )
    op.execute(
        f"ALTER TABLE ai_enrichment_queue "
        f"ADD CONSTRAINT ai_enrichment_queue_entity_type_known "
        f"CHECK (entity_type IN ({_OLD}))"
    )

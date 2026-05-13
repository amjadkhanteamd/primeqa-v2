"""Phase 2 tenant schema: add last_seed_hash to edges for property-bearing supersession.

Per corrections-log §11. Edges with properties_schema=None
(BELONGS_TO, HAS_RELATIONSHIP_TO, HAS_PICKLIST_VALUES, HAS_PROFILE)
use identity-based supersession on (source, target, edge_type)
— established in Field cycle (commit 3dcda00). Edges with
property schemas (INCLUDES_FIELD, GRANTS_OBJECT_ACCESS,
GRANTS_FIELD_ACCESS, ASSIGNED_TO_PROFILE_RECORDTYPE,
HAS_PERMISSION_SET, TRIGGERS_ON, REFERENCES, CONSTRAINS_PICKLIST_VALUES)
need hash-based supersession because property changes drive
re-write decisions.

Adds last_seed_hash VARCHAR(64) nullable column to edges. NULL for
property-less edges (existing rows + future property-less writes
stay NULL — there's nothing to hash, identity is the key).
Populated for property-bearing edges (SHA-256 of canonical-JSON
properties — same idiom as entities.last_seed_hash).

First use lands in the Layout phase's INCLUDES_FIELD edges
(this cycle). Subsequent phases reuse the same column:
- Profile (GRANTS_OBJECT_ACCESS, ASSIGNED_TO_PROFILE_RECORDTYPE)
- PermissionSet (GRANTS_FIELD_ACCESS, HAS_PERMISSION_SET)
- ValidationRule (REFERENCES)
- Flow (TRIGGERS_ON)
- Deferred CONSTRAINS_PICKLIST_VALUES (per §14 once §10 lands)

Revision ID: 20260512_0040
Revises: 20260512_0030
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260512_0040"
down_revision: Union[str, Sequence[str], None] = "20260512_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE edges ADD COLUMN last_seed_hash VARCHAR(64)
    """)
    op.execute("""
        COMMENT ON COLUMN edges.last_seed_hash IS
          'SHA-256 hex digest of edge properties JSONB. NULL for '
          'property-less edge types (BELONGS_TO, HAS_RELATIONSHIP_TO, '
          'HAS_PICKLIST_VALUES, HAS_PROFILE) where edge identity is '
          '(source, target, edge_type) alone. Populated for property-'
          'bearing edge types (INCLUDES_FIELD, GRANTS_OBJECT_ACCESS, '
          'GRANTS_FIELD_ACCESS, ASSIGNED_TO_PROFILE_RECORDTYPE, '
          'HAS_PERMISSION_SET, TRIGGERS_ON, REFERENCES, '
          'CONSTRAINS_PICKLIST_VALUES) where property changes drive '
          'supersession. Per design doc and corrections-log §11.'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE edges DROP COLUMN last_seed_hash")

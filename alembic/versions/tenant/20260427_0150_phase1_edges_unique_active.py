"""Phase 1 tenant schema: edges unique active partial index (defense-in-depth)

Adds a partial unique index on edges to prevent duplicate currently-active
edges with the same (source, target, edge_type) tuple, except for
REFERENCES edges where multiple instances per pair are legitimate.

Why except REFERENCES:
  REFERENCES edges (validation_rule -> field) carry reference_type as a
  property: 'read', 'priorvalue', 'ischanged', 'isnew'. The same rule can
  reference the same field multiple times with different types
  (e.g., Amount referenced via both 'read' and 'priorvalue' in the same
  formula). This produces multiple REFERENCES edges with same source +
  target + edge_type but different properties, which is correct.

  Other edge types are deterministic per (source, target): one HAS_PROFILE
  edge per User+Profile pair, one BELONGS_TO per child+parent, etc.

This index is a guardrail against buggy derivation logic. Correct
derivation will never produce duplicates; this index makes failure modes
loud (IntegrityError on insert) rather than silent (duplicate active
edges accumulating).

The existing idx_edges_unique_containment from 20260427_0020 remains in
place; it covers the STRUCTURAL category at the (source, edge_type,
valid_from_seq) level, which is a different invariant (no two STRUCTURAL
edges from the same source at the same version_seq). The two indexes
guard different failure modes.

Revision ID: 20260427_0150
Revises: 20260427_0140
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0150"
down_revision: Union[str, Sequence[str], None] = "20260427_0140"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX idx_edges_unique_active_non_references
            ON edges(source_entity_id, target_entity_id, edge_type)
            WHERE valid_to_seq IS NULL
              AND edge_type != 'REFERENCES'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_edges_unique_active_non_references")

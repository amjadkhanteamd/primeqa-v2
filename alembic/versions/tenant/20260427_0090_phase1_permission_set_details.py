"""Phase 1 tenant schema: permission_set_details (per D-025)

Seventh detail table. Hot/queryable attributes for entity_type='PermissionSet'.

Per D-025:
  (1) Per-entity-version: entity_id PK, FK to entities ON DELETE CASCADE.
  (2) Hot columns capture queryable PermissionSet metadata.
  (3) No tenant_id column.

Containment:
  None. PermissionSet is top-level (like Profile). PermissionSets are
  referenced by edges (HAS_PERMISSION_SET from User, GRANTS_OBJECT_ACCESS
  to Object, GRANTS_FIELD_ACCESS to Field, INHERITS_PERMISSION_SET to
  another PermissionSet) but they don't belong to anything in the org
  structure.

Hot columns (3 + PK + audit):
  Boolean flags (1):     is_custom — Salesforce ships some standard
                         PermissionSets via managed packages; custom ones are
                         user-created. Filter by source.
  License type (1):      license_type VARCHAR(40) — like profile's
                         user_license_type. PermissionSets may require a
                         specific license to be assignable. Stored as license
                         name string per D-025 design call (UserLicense isn't
                         a Tier 1 entity).
  Audit (1):             created_at

PermissionSet has unusually thin hot metadata. Most of its semantic weight
lives in outgoing edges (GRANTS_OBJECT_ACCESS, GRANTS_FIELD_ACCESS,
INHERITS_PERMISSION_SET) and inbound HAS_PERMISSION_SET edges from Users.
The PermissionSet entity itself is a hub.

Note on is_managed: managed-package PermissionSets carry a namespace_prefix
in their JSONB attributes. We do NOT add an is_managed BOOLEAN column derived
from namespace_prefix presence — that would denormalize the same fact across
two columns and require sync to keep them aligned. If queries need to filter
managed-vs-unmanaged, a partial index on namespace_prefix in the JSONB GIN
serves that purpose without column duplication. (Per D-017's spirit: avoid
denormalized columns that duplicate authoritative data.)

JSONB attributes (sparse, in entities.attributes via PermissionSetAttributes):
  description, namespace_prefix.

Indexes:
  - Custom-only filter (common): is_custom partial on TRUE
  - License type filter (generation paths): license_type

Revision ID: 20260427_0090
Revises: 20260427_0080
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0090"
down_revision: Union[str, Sequence[str], None] = "20260427_0080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE permission_set_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            is_custom BOOLEAN NOT NULL DEFAULT FALSE,

            license_type VARCHAR(40) NOT NULL,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX idx_permission_set_details_custom
            ON permission_set_details(entity_id)
            WHERE is_custom = TRUE
    """)

    op.execute("""
        CREATE INDEX idx_permission_set_details_license
            ON permission_set_details(license_type)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS permission_set_details CASCADE")

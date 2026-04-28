"""Phase 1 tenant schema: profile_details (per D-025)

Sixth detail table. Hot/queryable attributes for entity_type='Profile'.

Per D-025:
  (1) Per-entity-version: entity_id PK, FK to entities ON DELETE CASCADE.
  (2) Hot columns capture queryable Profile metadata.
  (3) No tenant_id column.

Containment:
  None. Profile is a top-level entity. Profiles are referenced by edges
  (HAS_PROFILE from User, GRANTS_OBJECT_ACCESS to Object,
  GRANTS_FIELD_ACCESS to Field, ASSIGNED_TO_PROFILE_RECORDTYPE from Layout)
  but they don't belong to anything in the org structure.

Hot columns (4 + PK + audit):
  Boolean flags (2):     is_active — heavily filtered ("active profiles only")
                         is_custom — Salesforce ships standard profiles
                                    (System Administrator, Standard User, etc.);
                                    custom profiles can be created. Filter
                                    "custom only" / "standard only" common.
  License type (1):      user_license_type VARCHAR(40) — 'Salesforce',
                         'Salesforce Platform', 'Customer Community Plus',
                         etc. Stored as the human-readable name (per D-025
                         design call) rather than UserLicense sf_id, since
                         UserLicense isn't a Tier 1 entity and queries reference
                         license by name.
  Audit (1):             created_at

JSONB attributes (sparse, in entities.attributes via ProfileAttributes):
  description, user_type.

  user_type is distinct from user_license_type — describes Standard vs Power
  User vs Customer Portal etc. Sparse because most queries care about
  license type, not user type subdivisions.

Indexes:
  - Active filter (common): is_active partial on TRUE
  - License type filter (generation paths): user_license_type

Revision ID: 20260427_0080
Revises: 20260427_0070
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0080"
down_revision: Union[str, Sequence[str], None] = "20260427_0070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE profile_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_custom BOOLEAN NOT NULL DEFAULT FALSE,

            user_license_type VARCHAR(40) NOT NULL,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX idx_profile_details_active
            ON profile_details(entity_id)
            WHERE is_active = TRUE
    """)

    op.execute("""
        CREATE INDEX idx_profile_details_license
            ON profile_details(user_license_type)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS profile_details CASCADE")

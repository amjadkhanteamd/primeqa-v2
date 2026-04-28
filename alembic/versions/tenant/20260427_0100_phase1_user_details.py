"""Phase 1 tenant schema: user_details (per D-025)

Eighth detail table. Hot/queryable attributes for entity_type='User'.

Per D-025:
  (1) Per-entity-version: entity_id PK, FK to entities ON DELETE CASCADE.
  (2) Hot columns capture queryable User metadata.
  (3) No tenant_id column.

Assignment column (NOT containment):
  profile_entity_id UUID NOT NULL — every User has exactly one Profile
  assigned. This is an assignment FK, not a containment FK. A User can
  change profiles over time; Users exist independently of any specific
  Profile in the bitemporal sense. Per D-019, HAS_PROFILE is registered
  as a PERMISSION-category edge (not STRUCTURAL), correctly reflecting
  that this is assignment, not part-of relationship.

  The column exists on user_details for fast lookup; the derivation
  logic (Phase 1 final piece) auto-generates HAS_PROFILE edges from this
  column. The derivation is structurally similar to BELONGS_TO derivation
  but tracked separately because edge_category differs.

Hot columns (5 + PK + audit):
  Assignment (1):     profile_entity_id NOT NULL FK
  Boolean flags (2):  is_active — heavily filtered ("active users only");
                      inactive users still produce records but generation
                      paths skip them
                      is_external — Customer Portal / Partner / Customer
                      Community vs internal; very common filter, mixed
                      distribution (varies by org type)
  User type (1):      user_type VARCHAR(40) NOT NULL — Salesforce taxonomy:
                      'Standard', 'CustomerSuccess', 'PowerCustomerSuccess',
                      'CspLitePortal', 'Guest', etc. Frequently filtered.
  Audit (1):          created_at

JSONB attributes (sparse, in entities.attributes via UserAttributes):
  email, username — privacy-sensitive personal identifiers; kept in JSONB
  per D-025 default. Promoted to columns later if cross-population query
  patterns emerge (e.g., "users with @company.com emails" — hasn't yet).
  Application-layer masking discipline: never expose email/username in
  logs or diff output by default. (Documented for Phase 2 sync engine.)

  time_zone_sid_key, locale_sid_key, language_locale_key — per-user locale
  attributes; not queried across population.

Indexes:
  - is_active partial on TRUE (dominant filter; most users active in most
    orgs; partial saves index size when active is the common case)
  - is_external plain (mixed distribution; we filter both directions)
  - composite (profile_entity_id, is_active) — supports "active users in
    profile X" which is the common shape for permission-resolution queries.
    Also serves profile_entity_id alone for HAS_PROFILE derivation lookups.

Note on D-020: User receives higher-frequency sync than other entities
because user activation/deactivation, license changes, profile reassignments
move on the timescale of HR events rather than deploys. This is a sync
engine concern; the schema doesn't change because of it.

Revision ID: 20260427_0100
Revises: 20260427_0090
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0100"
down_revision: Union[str, Sequence[str], None] = "20260427_0090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE user_details (
            entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,

            profile_entity_id UUID NOT NULL REFERENCES entities(id),

            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_external BOOLEAN NOT NULL DEFAULT FALSE,
            user_type VARCHAR(40) NOT NULL,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX idx_user_details_active
            ON user_details(entity_id)
            WHERE is_active = TRUE
    """)

    op.execute("""
        CREATE INDEX idx_user_details_external
            ON user_details(is_external)
    """)

    op.execute("""
        CREATE INDEX idx_user_details_profile_active
            ON user_details(profile_entity_id, is_active)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_details CASCADE")

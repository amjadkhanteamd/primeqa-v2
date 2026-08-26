"""3A-3 — declared surface inventory + claim_sets (LLD 3A-3 §a/§d).

Four tenant-schema tables:
  - ui_surface_inventories / ui_surface_inventory_members: the declared,
    versioned surface universe. Versions are IMMUTABLE — membership is
    recorded at creation (D-281 law), never recomputed; the service layer
    is the only writer. ``surface_entity_ref`` stays empty until the S1
    Surface entity lands (3A-5) — identity-EXCLUDED operational linkage.
  - claim_sets / claim_set_members: one human act approves
    (persona × inventory version × catalogue release) as a recorded set;
    membership + per-member applicability recorded at set creation.
    ``test_id`` is a logical FK to test_claims (a member references the
    TEST across versions, not one version row — same posture as
    requirement links, D-α A6).

Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260825_0020"
down_revision = "20260825_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ui_surface_inventories (
            inventory_version INTEGER PRIMARY KEY,
            notes             TEXT NOT NULL DEFAULT '',
            created_by        INTEGER NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE ui_surface_inventory_members (
            inventory_version  INTEGER NOT NULL
                REFERENCES ui_surface_inventories(inventory_version),
            surface_key        TEXT NOT NULL,
            site               TEXT NOT NULL,
            path               TEXT NOT NULL,
            persona_scope      TEXT NOT NULL,
            record_context_ref TEXT,
            viewport           TEXT,
            display_name       TEXT NOT NULL DEFAULT '',
            notes              TEXT NOT NULL DEFAULT '',
            auth_required      BOOLEAN NOT NULL DEFAULT FALSE,
            surface_entity_ref UUID,
            PRIMARY KEY (inventory_version, surface_key)
        )
    """)
    op.execute("""
        CREATE TABLE claim_sets (
            id                   UUID PRIMARY KEY,
            persona_scope        TEXT NOT NULL,
            inventory_version    INTEGER NOT NULL
                REFERENCES ui_surface_inventories(inventory_version),
            catalogue_release_id INTEGER NOT NULL,
            standard_profile     TEXT NOT NULL DEFAULT 'WCAG22',
            status               TEXT NOT NULL DEFAULT 'draft'
                CONSTRAINT claim_sets_status_known
                CHECK (status IN ('draft','approved','revoked')),
            created_by           INTEGER NOT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            approved_by          INTEGER,
            approved_at          TIMESTAMPTZ,
            member_count         INTEGER
        )
    """)
    op.execute("""
        CREATE TABLE claim_set_members (
            claim_set_id  UUID NOT NULL REFERENCES claim_sets(id),
            test_id       UUID NOT NULL,
            applicability TEXT NOT NULL
                CONSTRAINT claim_set_members_applicability_known
                CHECK (applicability IN
                       ('APPLICABLE','NOT_APPLICABLE','HUMAN_REVIEW')),
            executable    BOOLEAN NOT NULL,
            revoked_at    TIMESTAMPTZ,
            revoked_by    INTEGER,
            PRIMARY KEY (claim_set_id, test_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_claim_set_members_test_id
        ON claim_set_members (test_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS claim_set_members")
    op.execute("DROP TABLE IF EXISTS claim_sets")
    op.execute("DROP TABLE IF EXISTS ui_surface_inventory_members")
    op.execute("DROP TABLE IF EXISTS ui_surface_inventories")

"""Step 3 — the requirement → surface link (LLD_STEP_3_REQUIREMENT_SURFACE §a).

ADDITIVE, tenant schema: two new tables, two triggers, three indexes; no
data write; the DROPs live in the downgrade only.

  requirement_surface_links        one DECLARED row per (requirement identity ×
                                   frozen surface key × inventory version), with
                                   actor + time; unlink is a STATE CHANGE (active
                                   → false with actor/time/reason) — DELETE is
                                   refused by trigger, the identity columns are
                                   immutable by trigger.
  requirement_surface_link_claims  the materialisation ledger: every S2
                                   ``verifies`` link a declaration wrote, whether
                                   this declaration CREATED it (so unlink removes
                                   only what it made), and when it was
                                   deactivated.

``source`` carries the TA R2 vocabulary (DECLARED / DERIVED /
VERIFIED_DERIVED) under one CHECK, and a SECOND, named v1 write guard
(``…_v1_declared_only``) that the step introducing derivation drops under
its own entry. Plain DDL in env.py's transaction (D-459).
"""
from alembic import op

revision = "20260910_0010"
down_revision = "20260909_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS requirement_surface_links (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            external_system      external_system NOT NULL DEFAULT 'jira',
            requirement_key      TEXT NOT NULL,
            surface_key          TEXT NOT NULL,
            inventory_version    INTEGER NOT NULL,
            source               TEXT NOT NULL
                CONSTRAINT requirement_surface_links_source_known
                    CHECK (source IN ('DECLARED', 'DERIVED', 'VERIFIED_DERIVED'))
                CONSTRAINT requirement_surface_links_v1_declared_only
                    CHECK (source = 'DECLARED'),
            declared_by          INTEGER NOT NULL,
            declared_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            active               BOOLEAN NOT NULL DEFAULT TRUE,
            deactivated_by       INTEGER,
            deactivated_at       TIMESTAMPTZ,
            deactivation_reason  TEXT,
            CONSTRAINT requirement_surface_links_deactivation_complete
                CHECK ((active AND deactivated_by IS NULL AND deactivated_at IS NULL)
                       OR (NOT active AND deactivated_by IS NOT NULL
                           AND deactivated_at IS NOT NULL)),
            CONSTRAINT fk_requirement_surface_links_member
                FOREIGN KEY (inventory_version, surface_key)
                REFERENCES ui_surface_inventory_members (inventory_version, surface_key),
            CONSTRAINT fk_requirement_surface_links_identity
                FOREIGN KEY (external_system, requirement_key)
                REFERENCES requirement_identities (external_system, external_key)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_requirement_surface_links_active
            ON requirement_surface_links
               (external_system, requirement_key, surface_key, inventory_version)
            WHERE active
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_requirement_surface_links_requirement
            ON requirement_surface_links (external_system, requirement_key)
            WHERE active
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS requirement_surface_link_claims (
            link_id          UUID NOT NULL REFERENCES requirement_surface_links (id),
            test_id          UUID NOT NULL,
            claim_set_id     UUID NOT NULL REFERENCES claim_sets (id),
            created_link     BOOLEAN NOT NULL,
            materialised_by  INTEGER NOT NULL,
            materialised_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            deactivated_by   INTEGER,
            deactivated_at   TIMESTAMPTZ,
            PRIMARY KEY (link_id, test_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_requirement_surface_link_claims_test
            ON requirement_surface_link_claims (test_id)
    """)
    # Unlink is a state change, never a delete — refused at the table.
    op.execute("""
        CREATE OR REPLACE FUNCTION requirement_surface_links_refuse_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'requirement surface links are never deleted — unlink is a '
                'state change (active → false with actor/time/reason); table %',
                TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS requirement_surface_links_no_delete "
               "ON requirement_surface_links")
    op.execute("""
        CREATE TRIGGER requirement_surface_links_no_delete
            BEFORE DELETE ON requirement_surface_links
            FOR EACH ROW EXECUTE FUNCTION requirement_surface_links_refuse_delete()
    """)
    op.execute("DROP TRIGGER IF EXISTS requirement_surface_link_claims_no_delete "
               "ON requirement_surface_link_claims")
    op.execute("""
        CREATE TRIGGER requirement_surface_link_claims_no_delete
            BEFORE DELETE ON requirement_surface_link_claims
            FOR EACH ROW EXECUTE FUNCTION requirement_surface_links_refuse_delete()
    """)
    # The identity of a declaration is immutable; only the deactivation
    # columns may change.
    op.execute("""
        CREATE OR REPLACE FUNCTION requirement_surface_links_no_rekey()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.external_system   IS DISTINCT FROM OLD.external_system
               OR NEW.requirement_key   IS DISTINCT FROM OLD.requirement_key
               OR NEW.surface_key       IS DISTINCT FROM OLD.surface_key
               OR NEW.inventory_version IS DISTINCT FROM OLD.inventory_version
               OR NEW.source            IS DISTINCT FROM OLD.source
               OR NEW.declared_by       IS DISTINCT FROM OLD.declared_by
               OR NEW.declared_at       IS DISTINCT FROM OLD.declared_at THEN
                RAISE EXCEPTION
                    'a requirement surface link is immutable once declared '
                    '(link %); only its deactivation may be recorded', OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS requirement_surface_links_immutable "
               "ON requirement_surface_links")
    op.execute("""
        CREATE TRIGGER requirement_surface_links_immutable
            BEFORE UPDATE ON requirement_surface_links
            FOR EACH ROW EXECUTE FUNCTION requirement_surface_links_no_rekey()
    """)
    op.execute("COMMENT ON TABLE requirement_surface_links IS "
               "'Step 3 (TA R2): a requirement DECLARED verified on a frozen "
               "inventory surface. DECLARED only at v1 (write guard "
               "requirement_surface_links_v1_declared_only); DERIVED / "
               "VERIFIED_DERIVED are reserved vocabulary. Unlink = active false "
               "with actor/time/reason; never deleted.'")
    op.execute("COMMENT ON TABLE requirement_surface_link_claims IS "
               "'Step 3: the materialisation ledger — each S2 verifies link a "
               "declaration wrote, whether the declaration created it, and when "
               "it was deactivated. Never deleted.'")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS requirement_surface_links_immutable "
               "ON requirement_surface_links")
    op.execute("DROP TRIGGER IF EXISTS requirement_surface_link_claims_no_delete "
               "ON requirement_surface_link_claims")
    op.execute("DROP TRIGGER IF EXISTS requirement_surface_links_no_delete "
               "ON requirement_surface_links")
    op.execute("DROP FUNCTION IF EXISTS requirement_surface_links_no_rekey()")
    op.execute("DROP FUNCTION IF EXISTS requirement_surface_links_refuse_delete()")
    op.execute("DROP TABLE IF EXISTS requirement_surface_link_claims")
    op.execute("DROP TABLE IF EXISTS requirement_surface_links")

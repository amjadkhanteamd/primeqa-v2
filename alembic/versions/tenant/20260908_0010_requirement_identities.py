"""Step 1 — provenance + identity (LLD_STEP_1_PROVENANCE §a, §b).

ADDITIVE: a new table. The requirement IDENTITY becomes a first-class
object (Fork 1 lean B, ratified): ``(external_system, external_key)`` is
the identity; ``origin`` is a SEPARATE field on it, never on a
``requirements`` row (31 of the 42 identities on tenant 1 have no row,
so a row column cannot represent them).

  origin            jira | manual | fixture | probe | CANNOT_CLASSIFY
  origin_evidence   the RULE that classified it, never a guess
  established_*     who established the identity, and when
  classifier_version  origin@v1

Invariant 1 (immutable once established) is enforced by a trigger: the
key columns are refused on UPDATE. Decoration is NOT a column here — an
identity is decorated iff a live ``requirements`` row carries its key
(one read, no dual write, nothing to drift).

Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260908_0010"
down_revision = "20260907_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS requirement_identities (
            external_system     external_system NOT NULL,
            external_key        TEXT NOT NULL,
            origin              TEXT NOT NULL
                CONSTRAINT requirement_identities_origin_known
                CHECK (origin IN ('jira', 'manual', 'fixture', 'probe',
                                  'CANNOT_CLASSIFY')),
            origin_evidence     JSONB NOT NULL,
            established_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            established_by      TEXT NOT NULL,
            classifier_version  TEXT NOT NULL,
            CONSTRAINT pk_requirement_identities
                PRIMARY KEY (external_system, external_key)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_requirement_identities_origin
            ON requirement_identities (origin)
    """)
    # Invariant 1 — immutable once established. The key columns identify
    # the object; changing either would BE a re-key (invariant 6), so the
    # write is refused at the table, not merely avoided in the callers.
    op.execute("""
        CREATE OR REPLACE FUNCTION requirement_identities_no_rekey()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.external_system IS DISTINCT FROM OLD.external_system
               OR NEW.external_key IS DISTINCT FROM OLD.external_key THEN
                RAISE EXCEPTION
                    'requirement identity is immutable once established '
                    '(% / % -> % / %)',
                    OLD.external_system, OLD.external_key,
                    NEW.external_system, NEW.external_key;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS requirement_identities_immutable "
               "ON requirement_identities")
    op.execute("""
        CREATE TRIGGER requirement_identities_immutable
            BEFORE UPDATE ON requirement_identities
            FOR EACH ROW EXECUTE FUNCTION requirement_identities_no_rekey()
    """)
    op.execute("COMMENT ON TABLE requirement_identities IS "
               "'Step 1 (Fork 1 lean B): the requirement identity — the link "
               "key is first-class; origin is a separate, evidence-classified "
               "field. Decoration by a requirements row is a READ, not a column.'")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS requirement_identities_immutable "
               "ON requirement_identities")
    op.execute("DROP FUNCTION IF EXISTS requirement_identities_no_rekey()")
    op.execute("DROP TABLE IF EXISTS requirement_identities")

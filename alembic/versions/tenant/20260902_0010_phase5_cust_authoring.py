"""Phase 5 Part 2 — customer rule authoring, tenant store
(LLD_PHASE5_AUTHORING §f/§g/§i; D-471).

Custom rules live in the TENANT schema (the AK directive: nothing
per-tenant outside the tenant), under the disjoint PLM-CUST-nnnnn
namespace (five digits, RULED — the public CHECK widens once in
migrations/068, before any custom id is minted).

Five tables + the release-union record:

  cust_rules            — id spine (CHECK pins the five-digit namespace)
  cust_rule_versions    — immutable versions riding the s5 lifecycle
                          SHAPE unchanged (§i: no new lifecycle
                          machinery); `definition` is the grammar-
                          validated content, content-hashed; APPROVED
                          additionally records reviewed_no_conflict —
                          the ratification conflict gate (§h)
  cust_predicates       — the same content, normalised one row per term
                          for queryability (written together with
                          definition from one validated object)
  cust_token_sets       — versioned, immutable value domains: a rule
                          pins (key, version), so a drifting design
                          system invalidates the projection rather than
                          silently changing verdicts (§h)
  cust_authoring_ledger — every guideline outcome: drafted OR refused
                          with its class; refusal is a feature (§f)
  cust_release_members  — the tenant release UNION recorded at cut time
                          (D-281: recorded membership, never recomputed)

Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260902_0010"
down_revision = "20260826_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE cust_rules (
            rule_id    TEXT PRIMARY KEY
                CONSTRAINT cust_rules_id_shape
                CHECK (rule_id ~ '^PLM-CUST-[0-9]{5}$'),
            created_by INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE cust_rule_versions (
            rule_id               TEXT NOT NULL REFERENCES cust_rules (rule_id),
            version               INTEGER NOT NULL,
            name                  TEXT NOT NULL,
            guideline_thread_id   TEXT NOT NULL,
            state                 TEXT NOT NULL DEFAULT 'DRAFT'
                CONSTRAINT cust_rule_versions_state_known
                CHECK (state IN ('DRAFT','REVIEW','APPROVED','VERSIONED',
                                 'ACTIVE','RETIRED')),
            definition            JSONB NOT NULL,
            content_hash          CHAR(64) NOT NULL,
            census_schema_version INTEGER NOT NULL,
            reviewed_no_conflict  BOOLEAN NOT NULL DEFAULT FALSE,
            created_by            INTEGER NOT NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_by           INTEGER,
            reviewed_at           TIMESTAMPTZ,
            state_changed_by      INTEGER,
            state_changed_at      TIMESTAMPTZ,
            PRIMARY KEY (rule_id, version)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX cust_rule_versions_single_active
            ON cust_rule_versions (rule_id) WHERE state = 'ACTIVE'
    """)
    op.execute("""
        CREATE TABLE cust_predicates (
            id           BIGSERIAL PRIMARY KEY,
            rule_id      TEXT NOT NULL,
            rule_version INTEGER NOT NULL,
            slot         TEXT NOT NULL
                CONSTRAINT cust_predicates_slot_known
                CHECK (slot IN ('selector','predicate','applicability')),
            ordinal      INTEGER NOT NULL,
            term         TEXT NOT NULL,
            operand      JSONB NOT NULL DEFAULT '{}'::jsonb,
            FOREIGN KEY (rule_id, rule_version)
                REFERENCES cust_rule_versions (rule_id, version),
            CONSTRAINT cust_predicates_unique
                UNIQUE (rule_id, rule_version, slot, ordinal)
        )
    """)
    op.execute("""
        CREATE TABLE cust_token_sets (
            set_key    TEXT NOT NULL,
            version    INTEGER NOT NULL,
            tokens     JSONB NOT NULL,
            notes      TEXT NOT NULL DEFAULT '',
            created_by INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (set_key, version)
        )
    """)
    op.execute("""
        CREATE TABLE cust_authoring_ledger (
            id                  BIGSERIAL PRIMARY KEY,
            guideline_thread_id TEXT NOT NULL,
            prose               TEXT NOT NULL,
            outcome             TEXT NOT NULL
                CONSTRAINT cust_ledger_outcome_known
                CHECK (outcome IN ('drafted','refused')),
            refusal_class       TEXT
                CONSTRAINT cust_ledger_class_known
                CHECK (refusal_class IS NULL OR refusal_class IN
                       ('needs_prohibited_operator',
                        'needs_capability_not_captured',
                        'needs_interaction', 'not_observable',
                        'belongs_to_public_catalogue',
                        'ambiguous_guideline')),
            refusal_reason      TEXT,
            nearest_expressible JSONB,
            rule_id             TEXT,
            actor_user_id       INTEGER NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT cust_ledger_refusal_shape CHECK (
                (outcome = 'refused') = (refusal_class IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE TABLE cust_release_members (
            platform_release_id INTEGER NOT NULL,
            rule_id             TEXT NOT NULL,
            rule_version        INTEGER NOT NULL,
            recorded_by         INTEGER NOT NULL,
            recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (platform_release_id, rule_id, rule_version),
            FOREIGN KEY (rule_id, rule_version)
                REFERENCES cust_rule_versions (rule_id, version)
        )
    """)


def downgrade() -> None:
    for t in ("cust_release_members", "cust_authoring_ledger",
              "cust_token_sets", "cust_predicates", "cust_rule_versions",
              "cust_rules"):
        op.execute(f"DROP TABLE IF EXISTS {t}")

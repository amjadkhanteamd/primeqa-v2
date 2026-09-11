"""Step 5 — the Release Quality Policy (LLD_STEP_5_QUALITY_POLICY §a, §c).

ADDITIVE, tenant schema:

  quality_policies       a versioned tenant policy: name + version, status
                         draft → active → retired; ONE active per tenant;
                         immutable once a decision has graded under it
                         (first_used_at); never deleted.
  quality_policy_rules   the CLOSED declarative vocabulary as ROWS —
                         axis × condition × effect × scope (+ one numeric
                         parameter where the condition declares one); every
                         word CHECKed at the table; immutable with its policy.
  quality_waivers        a minimal waiver: item, axis, reviewer, reason,
                         expiry (REQUIRED — no expiry, no waiver; ruling 4),
                         actor; revocation is the state change; never deleted.

The seed (ruling 3): "Plimsol default v1" is written as a DRAFT with its ten
rules — a data write of rows no graded object references, so the migration
stays dumpless (D-476 posture: additive, no state change on any existing
row). A human ACTIVATES it (audited; CLI in v1) before any policy grades.
Idempotent: the seed is skipped when (name, version) exists.
"""
from alembic import op

revision = "20260912_0010"
down_revision = "20260911_0010"
branch_labels = None
depends_on = None

# The closed vocabulary — mirrored in primeqa/intelligence/quality_policy.py.
AXES = ("functional", "conformance", "regressions", "waivers", "human_reviews",
        "environments", "grading")
CONDITIONS = (
    "failure_present", "pass_rate_below",
    "level_a_failed", "level_aa_failed", "level_aaa_failed", "not_determined_present",
    "new_fail_present", "tool_drift_only", "not_comparable_present",
    "active_waiver_covers_item",
    "needs_human_pending",
    "environment_drift", "scope_not_current", "production_target_ungraded_on_metadata",
    "ungraded_present",
)
PAIRS = (
    ("functional", "failure_present"), ("functional", "pass_rate_below"),
    ("conformance", "level_a_failed"), ("conformance", "level_aa_failed"),
    ("conformance", "level_aaa_failed"), ("conformance", "not_determined_present"),
    ("regressions", "new_fail_present"), ("regressions", "tool_drift_only"),
    ("regressions", "not_comparable_present"),
    ("waivers", "active_waiver_covers_item"),
    ("human_reviews", "needs_human_pending"),
    ("environments", "environment_drift"), ("environments", "scope_not_current"),
    ("environments", "production_target_ungraded_on_metadata"),
    ("grading", "ungraded_present"),
)
EFFECTS = ("ALLOW", "CONDITIONAL", "REVIEW", "BLOCK")

# "Plimsol default v1" — the TA's example rules (1–8) + §d (9) + the legacy
# threshold as a parameter (10).
SEED_NAME, SEED_VERSION = "Plimsol default", 1
SEED_RULES = (
    (1, "functional", "failure_present", None, "BLOCK", "release",
     "critical functional failure — v1: every functional failure is critical (ruling 1)"),
    (2, "conformance", "level_a_failed", None, "BLOCK", "release", "critical accessibility failure"),
    (3, "conformance", "level_aa_failed", None, "CONDITIONAL", "release", "AA failure"),
    (4, "human_reviews", "needs_human_pending", None, "REVIEW", "release", "NEEDS_HUMAN pending"),
    (5, "waivers", "active_waiver_covers_item", None, "ALLOW", "item", "an active waiver allows THAT item"),
    (6, "regressions", "tool_drift_only", None, "ALLOW", "release", "tool drift only"),
    (7, "environments", "environment_drift", None, "REVIEW", "release", "environment drift"),
    (8, "grading", "ungraded_present", None, "BLOCK", "release", "ungraded (D9)"),
    (9, "environments", "production_target_ungraded_on_metadata", None, "BLOCK", "release",
     "a production read-only target grades on its metadata evidence (§d)"),
    (10, "functional", "pass_rate_below", 95, "BLOCK", "release",
     "the legacy threshold, carried as a rule parameter"),
)


def _sql_list(values):
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS quality_policies (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name           TEXT NOT NULL,
            version        INTEGER NOT NULL CHECK (version >= 1),
            status         TEXT NOT NULL DEFAULT 'draft'
                CONSTRAINT quality_policies_status_known
                    CHECK (status IN ('draft', 'active', 'retired')),
            note           TEXT NOT NULL DEFAULT '',
            created_by     INTEGER,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            activated_by   INTEGER,
            activated_at   TIMESTAMPTZ,
            retired_by     INTEGER,
            retired_at     TIMESTAMPTZ,
            first_used_at  TIMESTAMPTZ,
            CONSTRAINT quality_policies_name_version UNIQUE (name, version),
            CONSTRAINT quality_policies_activation_complete
                CHECK ((status = 'draft' AND activated_at IS NULL AND retired_at IS NULL)
                       OR (status = 'active' AND activated_at IS NOT NULL AND retired_at IS NULL)
                       OR (status = 'retired' AND activated_at IS NOT NULL AND retired_at IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_quality_policies_one_active
            ON quality_policies ((status)) WHERE status = 'active'
    """)
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS quality_policy_rules (
            policy_id  UUID NOT NULL REFERENCES quality_policies (id),
            position   INTEGER NOT NULL CHECK (position >= 1),
            axis       TEXT NOT NULL
                CONSTRAINT quality_policy_rules_axis_known
                    CHECK (axis IN ({_sql_list(AXES)})),
            condition  TEXT NOT NULL
                CONSTRAINT quality_policy_rules_condition_known
                    CHECK (condition IN ({_sql_list(CONDITIONS)})),
            parameter  NUMERIC,
            effect     TEXT NOT NULL
                CONSTRAINT quality_policy_rules_effect_known
                    CHECK (effect IN ({_sql_list(EFFECTS)})),
            scope      TEXT NOT NULL DEFAULT 'release'
                CONSTRAINT quality_policy_rules_scope_known
                    CHECK (scope IN ('release', 'item')),
            note       TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (policy_id, position),
            CONSTRAINT quality_policy_rules_pair_admitted CHECK (
                (axis, condition) IN ({", ".join(f"('{a}', '{c}')" for a, c in PAIRS)})
            ),
            CONSTRAINT quality_policy_rules_parameter_declared CHECK (
                (condition = 'pass_rate_below' AND parameter IS NOT NULL AND parameter >= 0 AND parameter <= 100)
                OR (condition <> 'pass_rate_below' AND parameter IS NULL)
            )
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS quality_waivers (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            release_id         INTEGER,
            item_kind          TEXT NOT NULL
                CONSTRAINT quality_waivers_item_kind_known
                    CHECK (item_kind IN ('claim', 'rule', 'surface')),
            item_ref           TEXT NOT NULL CHECK (length(item_ref) > 0),
            axis               TEXT NOT NULL
                CONSTRAINT quality_waivers_axis_known
                    CHECK (axis IN ('functional', 'conformance', 'human_reviews')),
            reviewer_user_id   INTEGER NOT NULL,
            reason             TEXT NOT NULL CHECK (length(btrim(reason)) > 0),
            expires_at         TIMESTAMPTZ NOT NULL,
            created_by         INTEGER NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_by         INTEGER,
            revoked_at         TIMESTAMPTZ,
            revocation_reason  TEXT,
            CONSTRAINT quality_waivers_expiry_after_creation CHECK (expires_at > created_at),
            CONSTRAINT quality_waivers_revocation_complete
                CHECK ((revoked_by IS NULL AND revoked_at IS NULL)
                       OR (revoked_by IS NOT NULL AND revoked_at IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_quality_waivers_release
            ON quality_waivers (release_id, expires_at) WHERE revoked_at IS NULL
    """)
    # never deleted
    op.execute("""
        CREATE OR REPLACE FUNCTION quality_policy_refuse_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'quality policies, rules and waivers are never deleted (table %)', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
    """)
    for t in ("quality_policies", "quality_policy_rules", "quality_waivers"):
        op.execute(f"DROP TRIGGER IF EXISTS {t}_no_delete ON {t}")
        op.execute(f"""
            CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t}
                FOR EACH ROW EXECUTE FUNCTION quality_policy_refuse_delete()
        """)
    # immutable once used: the identity and the rules freeze at first_used_at;
    # the status may still move active → retired (a state change with actor).
    op.execute("""
        CREATE OR REPLACE FUNCTION quality_policies_immutable_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.first_used_at IS NOT NULL THEN
                IF NEW.name IS DISTINCT FROM OLD.name
                   OR NEW.version IS DISTINCT FROM OLD.version
                   OR NEW.note IS DISTINCT FROM OLD.note
                   OR NEW.created_by IS DISTINCT FROM OLD.created_by
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.activated_by IS DISTINCT FROM OLD.activated_by
                   OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
                   OR NEW.first_used_at IS DISTINCT FROM OLD.first_used_at THEN
                    RAISE EXCEPTION 'quality policy % v% is immutable: a decision graded under it (first used %)',
                        OLD.name, OLD.version, OLD.first_used_at;
                END IF;
                IF NEW.status IS DISTINCT FROM OLD.status
                   AND NOT (OLD.status = 'active' AND NEW.status = 'retired') THEN
                    RAISE EXCEPTION 'quality policy % v% may only retire once used (% -> %)',
                        OLD.name, OLD.version, OLD.status, NEW.status;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS quality_policies_immutable ON quality_policies")
    op.execute("""
        CREATE TRIGGER quality_policies_immutable BEFORE UPDATE ON quality_policies
            FOR EACH ROW EXECUTE FUNCTION quality_policies_immutable_guard()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION quality_policy_rules_immutable_guard()
        RETURNS TRIGGER AS $$
        DECLARE used TIMESTAMPTZ; st TEXT;
        BEGIN
            SELECT first_used_at, status INTO used, st FROM quality_policies
                WHERE id = COALESCE(NEW.policy_id, OLD.policy_id);
            IF used IS NOT NULL THEN
                RAISE EXCEPTION 'the rules of a used quality policy are immutable (policy %, first used %)',
                    COALESCE(NEW.policy_id, OLD.policy_id), used;
            END IF;
            IF st <> 'draft' THEN
                RAISE EXCEPTION 'rules change only on a DRAFT policy (policy % is %)',
                    COALESCE(NEW.policy_id, OLD.policy_id), st;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS quality_policy_rules_immutable ON quality_policy_rules")
    op.execute("""
        CREATE TRIGGER quality_policy_rules_immutable BEFORE INSERT OR UPDATE ON quality_policy_rules
            FOR EACH ROW EXECUTE FUNCTION quality_policy_rules_immutable_guard()
    """)
    # a waiver's substance never changes; revocation is the only write
    op.execute("""
        CREATE OR REPLACE FUNCTION quality_waivers_immutable_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.release_id IS DISTINCT FROM OLD.release_id OR NEW.item_kind IS DISTINCT FROM OLD.item_kind
               OR NEW.item_ref IS DISTINCT FROM OLD.item_ref OR NEW.axis IS DISTINCT FROM OLD.axis
               OR NEW.reviewer_user_id IS DISTINCT FROM OLD.reviewer_user_id OR NEW.reason IS DISTINCT FROM OLD.reason
               OR NEW.expires_at IS DISTINCT FROM OLD.expires_at OR NEW.created_by IS DISTINCT FROM OLD.created_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'a waiver is immutable; revoke it and record a new one (waiver %)', OLD.id;
            END IF;
            IF OLD.revoked_at IS NOT NULL AND (NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
               OR NEW.revoked_by IS DISTINCT FROM OLD.revoked_by) THEN
                RAISE EXCEPTION 'a revoked waiver stays revoked (waiver %)', OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS quality_waivers_immutable ON quality_waivers")
    op.execute("""
        CREATE TRIGGER quality_waivers_immutable BEFORE UPDATE ON quality_waivers
            FOR EACH ROW EXECUTE FUNCTION quality_waivers_immutable_guard()
    """)
    # the seed — a DRAFT (ruling 3), idempotent
    op.execute(f"""
        INSERT INTO quality_policies (name, version, status, note, created_by)
        SELECT '{SEED_NAME}', {SEED_VERSION}, 'draft',
               'seed: Plimsol default v1 (the TA example rules; activate before any decision grades)', NULL
        WHERE NOT EXISTS (SELECT 1 FROM quality_policies WHERE name = '{SEED_NAME}' AND version = {SEED_VERSION})
    """)
    for pos, axis, cond, param, effect, scope, note in SEED_RULES:
        p = "NULL" if param is None else str(param)
        n = note.replace("'", "''")
        op.execute(f"""
            INSERT INTO quality_policy_rules (policy_id, position, axis, condition, parameter, effect, scope, note)
            SELECT id, {pos}, '{axis}', '{cond}', {p}, '{effect}', '{scope}', '{n}'
            FROM quality_policies WHERE name = '{SEED_NAME}' AND version = {SEED_VERSION}
              AND NOT EXISTS (SELECT 1 FROM quality_policy_rules r
                              WHERE r.policy_id = quality_policies.id AND r.position = {pos})
        """)


def downgrade() -> None:
    for t in ("quality_waivers", "quality_policy_rules", "quality_policies"):
        op.execute(f"DROP TRIGGER IF EXISTS {t}_no_delete ON {t}")
    op.execute("DROP TRIGGER IF EXISTS quality_waivers_immutable ON quality_waivers")
    op.execute("DROP TRIGGER IF EXISTS quality_policy_rules_immutable ON quality_policy_rules")
    op.execute("DROP TRIGGER IF EXISTS quality_policies_immutable ON quality_policies")
    op.execute("DROP TABLE IF EXISTS quality_waivers")
    op.execute("DROP TABLE IF EXISTS quality_policy_rules")
    op.execute("DROP TABLE IF EXISTS quality_policies")
    op.execute("DROP FUNCTION IF EXISTS quality_waivers_immutable_guard()")
    op.execute("DROP FUNCTION IF EXISTS quality_policy_rules_immutable_guard()")
    op.execute("DROP FUNCTION IF EXISTS quality_policies_immutable_guard()")
    op.execute("DROP FUNCTION IF EXISTS quality_policy_refuse_delete()")

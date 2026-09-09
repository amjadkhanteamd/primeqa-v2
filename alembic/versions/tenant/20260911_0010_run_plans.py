"""Step 4 — the Run Planner (LLD_STEP_4_RUN_PLANNER §a, §d, §e).

ADDITIVE, tenant schema:

  run_plans          the RECORDED plan: scope, inputs, resolution, exclusions,
                     who planned / who ran; immutable once written except the
                     one-time execution stamp; never deleted.
  release_targets    a release's DECLARED target environments with actor;
                     removal is a state change (the Step 3 pattern).
  s4_execution_jobs.plan_id, s4_execution_runs.plan_id
                     "Run this plan" stamps the jobs; the consumer carries the id
                     to the run row — what ran is traceable to what was shown.
  s4_run_schedules.plan_template / authorised_by / authorised_at /
                     last_plan_id / last_refusal / last_refused_at
                     a schedule references a plan template; its authority is
                     authorised_by ("claim this schedule") else created_by — both
                     NULL → the tick refuses (ruling 2, extends D-477).

No data write; every DROP is the downgrade. Plain DDL in env.py's
transaction (D-459).
"""
from alembic import op

revision = "20260911_0010"
down_revision = "20260910_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS run_plans (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scope_kind     TEXT NOT NULL
                CONSTRAINT run_plans_scope_kind_known
                    CHECK (scope_kind IN ('requirement', 'release', 'schedule', 'tenant')),
            scope_ref      TEXT NOT NULL,
            inputs         JSONB NOT NULL,
            resolution     JSONB NOT NULL,
            exclusions     JSONB NOT NULL,
            planned_by     INTEGER,
            authorised_by  INTEGER,
            planned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            executed_by    INTEGER,
            executed_at    TIMESTAMPTZ,
            execution      JSONB,
            CONSTRAINT run_plans_actor_present
                CHECK (planned_by IS NOT NULL OR authorised_by IS NOT NULL),
            CONSTRAINT run_plans_execution_complete
                CHECK ((executed_at IS NULL AND execution IS NULL)
                       OR (executed_at IS NOT NULL AND execution IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_run_plans_scope
            ON run_plans (scope_kind, scope_ref, planned_at DESC)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS release_targets (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            release_id           INTEGER NOT NULL,
            environment_id       INTEGER NOT NULL,
            declared_by          INTEGER NOT NULL,
            declared_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            active               BOOLEAN NOT NULL DEFAULT TRUE,
            deactivated_by       INTEGER,
            deactivated_at       TIMESTAMPTZ,
            deactivation_reason  TEXT,
            CONSTRAINT release_targets_deactivation_complete
                CHECK ((active AND deactivated_by IS NULL AND deactivated_at IS NULL)
                       OR (NOT active AND deactivated_by IS NOT NULL
                           AND deactivated_at IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_release_targets_active
            ON release_targets (release_id, environment_id) WHERE active
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_release_targets_release
            ON release_targets (release_id) WHERE active
    """)
    op.execute("""
        ALTER TABLE s4_execution_jobs ADD COLUMN IF NOT EXISTS plan_id UUID
    """)
    op.execute("""
        ALTER TABLE s4_execution_runs ADD COLUMN IF NOT EXISTS plan_id UUID
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_s4_execution_runs_plan
            ON s4_execution_runs (plan_id) WHERE plan_id IS NOT NULL
    """)
    op.execute("""
        ALTER TABLE s4_run_schedules
            ADD COLUMN IF NOT EXISTS plan_template   JSONB,
            ADD COLUMN IF NOT EXISTS authorised_by   INTEGER,
            ADD COLUMN IF NOT EXISTS authorised_at   TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS last_plan_id    UUID,
            ADD COLUMN IF NOT EXISTS last_refusal    TEXT,
            ADD COLUMN IF NOT EXISTS last_refused_at TIMESTAMPTZ
    """)
    # run_plans: immutable once written; the execution stamp is written ONCE.
    op.execute("""
        CREATE OR REPLACE FUNCTION run_plans_immutable_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.scope_kind    IS DISTINCT FROM OLD.scope_kind
               OR NEW.scope_ref     IS DISTINCT FROM OLD.scope_ref
               OR NEW.inputs        IS DISTINCT FROM OLD.inputs
               OR NEW.resolution    IS DISTINCT FROM OLD.resolution
               OR NEW.exclusions    IS DISTINCT FROM OLD.exclusions
               OR NEW.planned_by    IS DISTINCT FROM OLD.planned_by
               OR NEW.authorised_by IS DISTINCT FROM OLD.authorised_by
               OR NEW.planned_at    IS DISTINCT FROM OLD.planned_at THEN
                RAISE EXCEPTION 'a run plan is immutable once recorded (plan %)', OLD.id;
            END IF;
            IF OLD.executed_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'plan % was executed at % — one execution per plan; run again = re-plan',
                    OLD.id, OLD.executed_at;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS run_plans_immutable ON run_plans")
    op.execute("""
        CREATE TRIGGER run_plans_immutable
            BEFORE UPDATE ON run_plans
            FOR EACH ROW EXECUTE FUNCTION run_plans_immutable_guard()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION run_plans_refuse_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'run plans and release targets are never deleted (table %)', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS run_plans_no_delete ON run_plans")
    op.execute("""
        CREATE TRIGGER run_plans_no_delete
            BEFORE DELETE ON run_plans
            FOR EACH ROW EXECUTE FUNCTION run_plans_refuse_delete()
    """)
    op.execute("DROP TRIGGER IF EXISTS release_targets_no_delete ON release_targets")
    op.execute("""
        CREATE TRIGGER release_targets_no_delete
            BEFORE DELETE ON release_targets
            FOR EACH ROW EXECUTE FUNCTION run_plans_refuse_delete()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION release_targets_no_rekey()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.release_id IS DISTINCT FROM OLD.release_id
               OR NEW.environment_id IS DISTINCT FROM OLD.environment_id
               OR NEW.declared_by IS DISTINCT FROM OLD.declared_by
               OR NEW.declared_at IS DISTINCT FROM OLD.declared_at THEN
                RAISE EXCEPTION 'a release target is immutable once declared (target %)', OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS release_targets_immutable ON release_targets")
    op.execute("""
        CREATE TRIGGER release_targets_immutable
            BEFORE UPDATE ON release_targets
            FOR EACH ROW EXECUTE FUNCTION release_targets_no_rekey()
    """)
    op.execute("COMMENT ON TABLE run_plans IS "
               "'Step 4: the recorded plan — what will run, why, and what was excluded; "
               "\"Run this plan\" executes the row by id, once. Never deleted.'")
    op.execute("COMMENT ON TABLE release_targets IS "
               "'Step 4: a release''s DECLARED target environments with actor; "
               "removal is a state change. Never deleted.'")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS release_targets_immutable ON release_targets")
    op.execute("DROP TRIGGER IF EXISTS release_targets_no_delete ON release_targets")
    op.execute("DROP TRIGGER IF EXISTS run_plans_no_delete ON run_plans")
    op.execute("DROP TRIGGER IF EXISTS run_plans_immutable ON run_plans")
    op.execute("DROP FUNCTION IF EXISTS release_targets_no_rekey()")
    op.execute("DROP FUNCTION IF EXISTS run_plans_refuse_delete()")
    op.execute("DROP FUNCTION IF EXISTS run_plans_immutable_guard()")
    op.execute("ALTER TABLE s4_run_schedules DROP COLUMN IF EXISTS last_refused_at, "
               "DROP COLUMN IF EXISTS last_refusal, DROP COLUMN IF EXISTS last_plan_id, "
               "DROP COLUMN IF EXISTS authorised_at, DROP COLUMN IF EXISTS authorised_by, "
               "DROP COLUMN IF EXISTS plan_template")
    op.execute("DROP INDEX IF EXISTS ix_s4_execution_runs_plan")
    op.execute("ALTER TABLE s4_execution_runs DROP COLUMN IF EXISTS plan_id")
    op.execute("ALTER TABLE s4_execution_jobs DROP COLUMN IF EXISTS plan_id")
    op.execute("DROP TABLE IF EXISTS release_targets")
    op.execute("DROP TABLE IF EXISTS run_plans")

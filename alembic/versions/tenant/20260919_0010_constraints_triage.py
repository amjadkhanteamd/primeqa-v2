"""Triage batch (2026-09-19) — "guarded by convention, not by constraint":
four invariants that were held by every reader's join, a route's form
handling, or a caller's if-statement now hold at the table.

Tenant schema, REJECTING (the constraints refuse rows that violate them;
zero such rows exist on production, proven read-only before this landed —
and the merge is dump-first regardless, per D-476):

  s6_interpretations.run_id  → FOREIGN KEY to s4_execution_runs(run_id),
                               NO ACTION: a verdict cannot exist without the
                               run it interprets, and a run with verdicts
                               cannot be deleted from under them (AUD-026).
  quality_waivers            CHECK: a revoked waiver carries a non-empty
                               revocation_reason (AUD-020).
  release_targets            CHECK: a deactivated target carries a non-empty
                               deactivation_reason (AUD-023).
  repair_proposals           TRIGGER: a proposal may enter 'approved' or
                               'applied' only with gate_verdict = 'DERIVED'
                               and a recorded grounding_source — the apply
                               rule the callers checked, now at the table
                               (pass-4 attack #5).

Existing rows are untouched: the FK and the CHECKs validate the current rows
(none violate); the trigger judges transitions only, so rows already
'applied' before the gate stay as history.

Idempotent: every constraint and trigger is created only when absent.
"""
from alembic import op

revision = "20260919_0010"
down_revision = "20260912_0010"
branch_labels = None
depends_on = None


def _add_constraint_if_absent(table: str, name: str, definition: str) -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE c.conname = '{name}' AND t.relname = '{table}'
                  AND n.nspname = current_schema()
            ) THEN
                ALTER TABLE {table} ADD CONSTRAINT {name} {definition};
            END IF;
        END $$;
    """)


def upgrade() -> None:
    # AUD-026 — a verdict cannot exist without its run.
    _add_constraint_if_absent(
        "s6_interpretations", "fk_s6_interpretations_run",
        "FOREIGN KEY (run_id) REFERENCES s4_execution_runs (run_id)")

    # AUD-020 — a revocation says why (creation already does: length(btrim(reason)) > 0).
    _add_constraint_if_absent(
        "quality_waivers", "ck_quality_waivers_revocation_reason",
        "CHECK (revoked_at IS NULL OR length(btrim(COALESCE(revocation_reason, ''))) > 0)")

    # AUD-023 — a removal says why.
    _add_constraint_if_absent(
        "release_targets", "ck_release_targets_deactivation_reason",
        "CHECK (active OR length(btrim(COALESCE(deactivation_reason, ''))) > 0)")

    # Attack #5 — only a DERIVED proposal with a recorded grounding source
    # may be approved or applied; the transition is refused at the table.
    op.execute("""
        CREATE OR REPLACE FUNCTION repair_proposals_apply_guard()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.status IN ('approved', 'applied')
               AND (TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status) THEN
                IF NEW.gate_verdict IS DISTINCT FROM 'DERIVED' THEN
                    RAISE EXCEPTION 'repair proposal % cannot be %: gate verdict is %, only DERIVED applies',
                        NEW.id, NEW.status, COALESCE(NEW.gate_verdict, 'UNCLASSIFIED');
                END IF;
                IF NEW.grounding_source IS NULL
                   OR jsonb_typeof(NEW.grounding_source) <> 'object'
                   OR NEW.grounding_source = '{}'::jsonb THEN
                    RAISE EXCEPTION 'repair proposal % cannot be %: DERIVED without a recorded grounding source',
                        NEW.id, NEW.status;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS repair_proposals_apply_guard ON repair_proposals")
    op.execute("""
        CREATE TRIGGER repair_proposals_apply_guard
        BEFORE INSERT OR UPDATE OF status ON repair_proposals
        FOR EACH ROW EXECUTE FUNCTION repair_proposals_apply_guard()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS repair_proposals_apply_guard ON repair_proposals")
    op.execute("DROP FUNCTION IF EXISTS repair_proposals_apply_guard()")
    op.execute("ALTER TABLE release_targets DROP CONSTRAINT IF EXISTS ck_release_targets_deactivation_reason")
    op.execute("ALTER TABLE quality_waivers DROP CONSTRAINT IF EXISTS ck_quality_waivers_revocation_reason")
    op.execute("ALTER TABLE s6_interpretations DROP CONSTRAINT IF EXISTS fk_s6_interpretations_run")

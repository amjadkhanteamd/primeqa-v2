"""Round 4, part A (AUD-028): the run row exists BEFORE any record is provisioned,
and every run_id column that named a run is a foreign key.

Tenant schema. Classification: REJECTING (dump-first per D-476/D-285) — the
two NOT VALID keys refuse every NEW orphan, the plain key refuses any orphan,
and the CHECK refuses a row whose finished_at disagrees with its outcome.

  run_outcome              + 'running' — the write-ahead state of a run that
                             has been minted but not finalized. Written by
                             ``StrandedRecordSink.run_opened`` before the first
                             Salesforce create; replaced by the final outcome at
                             finalize, or by 'errored' when the run is
                             interrupted (worker shutdown / a raise / the
                             stale-run reaper).
  s4_execution_runs        finished_at NULLABLE — NULL exactly while running.
                           CHECK ck_s4_execution_runs_running_unfinished:
                             (finished_at IS NULL) = (outcome::text = 'running')
                           Every reader carries ``finished_at IS NOT NULL``
                           (the gate tests/unit/test_running_rows_invisible.py),
                           so a running row is invisible to the product until
                           it is finalized — exactly the visibility a run had
                           before this migration, when no row existed until
                           finalize.
  s4_created_records       FK run_id → s4_execution_runs(run_id)  NOT VALID
  repair_proposals         FK run_id → s4_execution_runs(run_id)  NOT VALID
  s6_reinterpretations     FK run_id → s4_execution_runs(run_id)  (valid: the
                           table holds zero orphans on production)

THE KEYS STAY NOT VALID PERMANENTLY (AK's ruling, D-501 memo / D-502). Seven
rows on production carry a run_id no run row matches — three
``s4_created_records`` rows from two runs cut off by the 2026-09-09 deploy
(jobs 721 and 778, ``worker_shutdown``) and four July ``repair_proposals``
whose claims and runs were deleted without a cascade. They are the only
surviving evidence of both incidents. ``VALIDATE CONSTRAINT`` would need them
deleted or their run ids rewritten, and both destroy evidence, so it is never
run; the rows are named exceptions in the run_id census ledger
(tests/integration/test_constraints_triage.py::NAMED_ORPHANS).

``ALTER TYPE … ADD VALUE IF NOT EXISTS`` is transaction-safe on PG 12+ as long
as the new label is not USED in the same transaction; the CHECK compares
``outcome::text``, which does not use the label (proven on scratch PG 16 in one
transaction, 2026-09-22). Idempotent: every piece is created only when absent.
"""
from alembic import op

revision = "20260922_0010"
down_revision = "20260921_0010"
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
    # 1. the write-ahead state
    op.execute("ALTER TYPE run_outcome ADD VALUE IF NOT EXISTS 'running'")
    op.execute("ALTER TABLE s4_execution_runs ALTER COLUMN finished_at DROP NOT NULL")
    _add_constraint_if_absent(
        "s4_execution_runs", "ck_s4_execution_runs_running_unfinished",
        "CHECK ((finished_at IS NULL) = (outcome::text = 'running'))")

    # 2. the keys — NOT VALID where production holds named orphans, permanently
    _add_constraint_if_absent(
        "s4_created_records", "fk_s4_created_records_run",
        "FOREIGN KEY (run_id) REFERENCES s4_execution_runs (run_id) NOT VALID")
    _add_constraint_if_absent(
        "repair_proposals", "fk_repair_proposals_run",
        "FOREIGN KEY (run_id) REFERENCES s4_execution_runs (run_id) NOT VALID")
    _add_constraint_if_absent(
        "s6_reinterpretations", "fk_s6_reinterpretations_run",
        "FOREIGN KEY (run_id) REFERENCES s4_execution_runs (run_id)")
    op.execute(
        "COMMENT ON CONSTRAINT fk_s4_created_records_run ON s4_created_records IS "
        "'AUD-028 (round 4, D-502): NOT VALID PERMANENTLY. Three rows from runs 71c0e78a and "
        "d89c2336 (jobs 721/778, worker_shutdown on the 2026-09-09 deploy) carry a run_id no "
        "run row matches; they are the evidence of the deploy-interrupt incident and are named "
        "in the census ledger. Never VALIDATE: it would need them deleted or rewritten.'")
    op.execute(
        "COMMENT ON CONSTRAINT fk_repair_proposals_run ON repair_proposals IS "
        "'AUD-028 (round 4, D-502): NOT VALID PERMANENTLY. Four proposals of 2026-07-10 "
        "(runs 8c09fc83, cff08d72, f95f9b3b, f7ef9417) name claims and runs a July deletion "
        "removed without a cascade; they are the evidence of that incident and are named in "
        "the census ledger. Never VALIDATE: it would need them deleted or rewritten.'")


def downgrade() -> None:
    op.execute("ALTER TABLE s6_reinterpretations DROP CONSTRAINT IF EXISTS fk_s6_reinterpretations_run")
    op.execute("ALTER TABLE repair_proposals DROP CONSTRAINT IF EXISTS fk_repair_proposals_run")
    op.execute("ALTER TABLE s4_created_records DROP CONSTRAINT IF EXISTS fk_s4_created_records_run")
    op.execute("ALTER TABLE s4_execution_runs DROP CONSTRAINT IF EXISTS ck_s4_execution_runs_running_unfinished")
    # finished_at stays nullable and the enum keeps its label: a label cannot be
    # dropped from a PG enum, and a running row (if any) would refuse NOT NULL.

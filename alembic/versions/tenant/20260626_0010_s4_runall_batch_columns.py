"""S6 run-all: batch_id + source correlation columns on s4_execution_runs.

Revision ID: 20260626_0010
Revises: 20260624_0020
Create Date: 2026-06-26

D-275 (S6 build, Slice 3.2). Adds the run-all batch-correlation columns so a
multi-probe (bva) run can stamp every probe-run of one invocation with a shared
id — the atomic unit strict-AND grades over (completeness semantics land in
Slice 4). Both columns are NULL on every existing row and every single-recipe
run; they are DORMANT until the run-all loop (Slice 3.3) passes a batch_id.

  * ``s4_execution_runs.batch_id UUID``  (nullable; the run-all invocation id)
  * ``s4_execution_runs.source VARCHAR`` (nullable; the trigger discriminator —
    open text, NO CHECK, code owns the vocabulary: runall_probe today; the
    deferred suite-run grouping reuses this column with manual_bulk / scheduled
    / release_bulk; see DEFERRED_ITEMS.md §3 + D-275)

Additive, nullable, NO default, NO backfill — a pure ADD COLUMN (non-locking on
Postgres for nullable-no-default; zero rows rewritten). Idempotent (ADD COLUMN
IF NOT EXISTS) per the migrations-016+ convention. No index this slice (no reader
filters by batch_id yet).
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260626_0010'
down_revision = '20260624_0020'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE s4_execution_runs "
        "ADD COLUMN IF NOT EXISTS batch_id UUID")
    op.execute(
        "ALTER TABLE s4_execution_runs "
        "ADD COLUMN IF NOT EXISTS source VARCHAR")
    op.execute(
        "COMMENT ON COLUMN s4_execution_runs.batch_id IS "
        "'D-275 (S6 run-all): the run-all invocation id — minted at run time by "
        "the run-all loop, stamping each of a claim''s probe-runs so strict-AND "
        "grades ONE batch. NULL on every single-recipe run (dormant until Slice "
        "3.3). Completeness semantics (expected probe membership) live in Slice 4. "
        "Shared-design with the deferred suite-run grouping (DEFERRED_ITEMS 3).'")
    op.execute(
        "COMMENT ON COLUMN s4_execution_runs.source IS "
        "'D-275 (S6 run-all): the batch trigger discriminator — open text, no "
        "CHECK (code owns the vocabulary). runall_probe for run-all; the deferred "
        "suite-run grouping reuses this column with manual_bulk / scheduled / "
        "release_bulk. NULL on single runs.'")


def downgrade():
    op.execute("ALTER TABLE s4_execution_runs DROP COLUMN IF EXISTS source")
    op.execute("ALTER TABLE s4_execution_runs DROP COLUMN IF EXISTS batch_id")

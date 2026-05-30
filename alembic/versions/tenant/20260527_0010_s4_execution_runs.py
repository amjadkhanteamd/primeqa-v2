"""substrate-4 execution result store: s4_execution_runs (D-108.2 slice 3)

Revision ID: 20260527_0010
Revises: 20260525_0030
Create Date: 2026-05-27

Per DECISIONS_LOG.md D-108.2 (+ SPEC substrate_4_execution §4). The S4 result
store: one row per metadata-inspection run — the captured truth + grounded
outcome the executor (slice 2) produces.

Placement (D-108.2, PER-TENANT): execution truth for one tenant's recipes
against one tenant's orgs — isolated tenant data. Lands in the per-tenant schema
(alembic tenant branch, unqualified, **no tenant_id column** — isolation by
schema, the substrate-1/-2/-3 convention), beside test_recipes /
test_recipe_runtime_state.

Schema (D-108.2 decision = A, run-entity + JSONB captured-trace):
  - Typed identity / outcome columns (queryable): run_id (UUID PK), recipe_id,
    recipe_version_seq, claim_test_id, claim_version_seq (NULL), environment_id,
    outcome, started_at, finished_at, duration_ms.
  - evidence JSONB: the per-step captured trace (translated queries, structured
    filters, returned rows, per-step timings + errors) — raw observation, the
    extensible part that grows per recipe kind (F2). NOT semantic data, so the
    no-JSONB-blob rule (which targets the semantic store) does not apply.

``outcome`` REUSES the existing ``run_outcome`` PG enum (passed/failed/errored/
skipped, create_type=False) — verified to match the S4 vocabulary AND slice 4's
``report_run_outcome`` signature exactly, so the run row reconciles to the S2
boundary verbatim. (The executor produces only passed/failed/errored today;
'skipped' is a selection-time outcome the column allows but execution never
writes.)

Index: recipe_id (the obvious lookup — "runs for this recipe"). Other indexes
deferred until real query patterns emerge (don't over-index). The A->B forward
migration (per-step facts -> structured child table) is recorded in D-108.2 and
lands when a concrete per-step query need does.

Tenant-branch migration; table is per-tenant. Schema only — no behavior.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260527_0010'
down_revision = '20260525_0030'
branch_labels = None
depends_on = None


# Reuse the run_outcome PG enum created by the substrate-2 migration
# (20260518_1014). create_type=False — the type already exists; this is a
# reference for the column, not a CREATE.
run_outcome_enum = postgresql.ENUM(
    'passed', 'failed', 'errored', 'skipped',
    name='run_outcome', create_type=False,
)


def upgrade():
    op.create_table(
        's4_execution_runs',
        # Identity: the run self-identifies from birth (executor-minted uuid4).
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=False),
        # What was run (logical FKs to per-tenant S2 rows; not DB-enforced —
        # recipe_id is not unique in test_recipes, compound PK with version_seq).
        sa.Column('recipe_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('recipe_version_seq', sa.Integer(), nullable=False),
        sa.Column('claim_test_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('claim_version_seq', sa.Integer(), nullable=True),
        # Where it ran (loose int — environments are v1-side, not per-tenant).
        sa.Column('environment_id', sa.Integer(), nullable=False),
        # The grounded run outcome (reused enum).
        sa.Column('outcome', run_outcome_enum, nullable=False),
        # Run timing.
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        # The captured trace — raw observation, extensible per recipe kind (F2).
        sa.Column('evidence', postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint('run_id', name='pk_s4_execution_runs'),
    )
    # The obvious lookup: "runs for this recipe". Other indexes deferred.
    op.create_index(
        'idx_s4_execution_runs_recipe_id',
        's4_execution_runs',
        ['recipe_id'],
    )
    op.execute(
        "COMMENT ON TABLE s4_execution_runs IS "
        "'Substrate-4 execution result store (D-108.2). Per-tenant; one row per "
        "run — typed identity/outcome columns + an evidence JSONB captured-trace. "
        "Hands run identity to S2 report_run_outcome at slice 4; feeds S6.'"
    )


def downgrade():
    op.drop_index('idx_s4_execution_runs_recipe_id', table_name='s4_execution_runs')
    op.drop_table('s4_execution_runs')

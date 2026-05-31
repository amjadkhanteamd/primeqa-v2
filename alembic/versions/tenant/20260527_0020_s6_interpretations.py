"""substrate-6 interpretation store: s6_interpretations (D-111.2)

Revision ID: 20260527_0020
Revises: 20260527_0010
Create Date: 2026-05-31

Per DECISIONS_LOG.md D-111.2 (+ SPEC substrate_6_intelligence). The S6
interpretation store: one row per interpreted run — S6's structured reading of
S4's captured truth (verdict + attribution + deeper cause), produced + persisted
eagerly at run time (the interpret stage in the run-path).

Placement (D-111.2, PER-TENANT, mirror-S4): one tenant's interpretations of one
tenant's runs — isolated tenant data. Lands in the per-tenant schema (alembic
tenant branch, unqualified, **no tenant_id column** — isolation by schema, the
substrate-1/-2/-3/-4 convention), beside s4_execution_runs (the run truth it
interprets) / test_recipes / test_recipe_runtime_state.

Schema (D-111.2 decision C = mirror the S4 result store):
  - Typed identity / semantic columns (the queryable axes): run_id (UUID PK —
    one interpretation per run, the logical 1:1 to s4_execution_runs.run_id),
    recipe_id, claim_test_id, outcome, verdict.
  - detail JSONB: the rich part — attribution (prose), evidence_refs (the
    pointers back into RunEvidence), cause ({cause_kind, vr_name, detail} or
    null). The extensible companion to the typed columns.

``outcome`` REUSES the existing ``run_outcome`` PG enum (passed/failed/errored/
skipped, create_type=False) — S6 carries S4's outcome **verbatim** (never
recomputes it), so the column is the same type as s4_execution_runs.outcome and
reconciles exactly.

``verdict`` is TEXT (not a PG enum): the Python ``Verdict`` Literal
(``primeqa/interpretation/model.py``) is the single source of truth and the
taxonomy *grows with recipe kinds* — a TEXT column avoids an ALTER TYPE
migration on every addition while staying a typed, queryable axis
(``WHERE verdict = …``). A CHECK / enum can be added later if DB-level integrity
is wanted; the value is already constrained at the dataclass layer.

Index: recipe_id (mirror S4 — "interpretations for this recipe"). run_id lookups
use the PK. A verdict / cause_kind index is deferred until cause-clustering
lands (the D-111.2 + DEFERRED item-2 trigger; don't over-index — S4 discipline).

Tenant-branch migration; table is per-tenant. Schema only — no behavior.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260527_0020'
down_revision = '20260527_0010'
branch_labels = None
depends_on = None


# Reuse the run_outcome PG enum created by the substrate-2 migration
# (20260518_1014). create_type=False — the type already exists; this is a
# reference for the column, not a CREATE. S6 carries S4's outcome verbatim.
run_outcome_enum = postgresql.ENUM(
    'passed', 'failed', 'errored', 'skipped',
    name='run_outcome', create_type=False,
)


def upgrade():
    op.create_table(
        's6_interpretations',
        # Identity: one interpretation per run (logical 1:1 to
        # s4_execution_runs.run_id; not DB-enforced FK — the substrate
        # logical-FK convention).
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=False),
        # What was interpreted (logical FKs to per-tenant S2 rows; not
        # DB-enforced — recipe_id is not unique in test_recipes, compound PK).
        sa.Column('recipe_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('claim_test_id', postgresql.UUID(as_uuid=True), nullable=False),
        # The S4 outcome, carried verbatim (reused enum — S6 never recomputes it).
        sa.Column('outcome', run_outcome_enum, nullable=False),
        # The semantic verdict (TEXT — Python Literal is the source of truth;
        # the taxonomy grows with recipe kinds).
        sa.Column('verdict', sa.Text(), nullable=False),
        # The rich part: attribution prose + evidence_refs + cause. The
        # extensible companion — cause_kind/vr_name promote to columns when
        # cause-clustering lands.
        sa.Column('detail', postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint('run_id', name='pk_s6_interpretations'),
    )
    # The lookup mirror-S4 uses: "interpretations for this recipe". run_id
    # lookups use the PK. Verdict/cause indexes deferred (clustering trigger).
    op.create_index(
        'idx_s6_interpretations_recipe_id',
        's6_interpretations',
        ['recipe_id'],
    )
    op.execute(
        "COMMENT ON TABLE s6_interpretations IS "
        "'Substrate-6 interpretation store (D-111.2). Per-tenant; one row per "
        "interpreted run — typed identity/outcome/verdict columns + a detail "
        "JSONB (attribution, evidence_refs, cause). Produced + persisted eagerly "
        "at run time; best-effort (a S6 failure never rolls back the S4 run row).'"
    )


def downgrade():
    op.drop_index('idx_s6_interpretations_recipe_id', table_name='s6_interpretations')
    op.drop_table('s6_interpretations')

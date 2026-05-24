"""create substrate-3 generation ledger

Revision ID: 20260520_0010
Revises: 123534d82956
Create Date: 2026-05-20

Substrate-3 (Generation Engine) Slice 0 — the generation-ledger schema.
Per docs/architecture/substrate_3_generation/SPEC.md §3.5 (two-surface
ledger) + DECISIONS_LOG.md D-074 (generation_outcomes) and D-087 (b)
(llm_calls).

Tenant-branch migration (down_revision = substrate-2's head). Runs against
each tenant_<id> schema via alembic/env.py's SET LOCAL search_path; tables
are created unqualified (no schema= arg) so they land in the per-tenant
schema — the substrate-1/-2 convention. No tenant_id column: isolation is
by schema (matching substrate-2).

Creates four enums and two tables:
  - generation_outcomes  — semantic ledger (D-074)
  - llm_calls            — operational telemetry (D-087 b); FK to outcomes

Design decisions applied (Slice 0):
  - Native PG enums, create_type=False, lifecycle managed in upgrade/
    downgrade (substrate-2 idiom).
  - Substrate-3-scoped enum names (no collision with substrate-1/-2 enums
    in the shared per-tenant schema).
  - Row-level typed-provenance columns (refusal_kind, refusal_policy_version,
    refusal_schema_version, explanation_hash, dismissal_taxonomy_version)
    are top-level, NOT in JSONB — the substrate-2 forward-commitment (D-074).
  - Real FK on llm_calls.generation_outcome_id (target is a unique
    single-column PK), vs substrate-2's logical-only FKs (non-unique
    targets).
  - Binary-protocol CHECK on generation_outcomes (refusal_kind present iff
    refusal) per D-072.

Slice 0: schema only. No data, no behavior.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260520_0010'
down_revision = '123534d82956'
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Enum definitions — create_type=False; lifecycle managed explicitly in
# upgrade/downgrade. Names are substrate-3-scoped. Values match the Pydantic
# enums in primeqa/generation/enums.py and the SQLAlchemy bindings in
# primeqa/generation/models_db.py exactly.
# ---------------------------------------------------------------------------

generation_outcome_kind_enum = postgresql.ENUM(
    'draft',
    'refusal',
    name='generation_outcome_kind',
    create_type=False,
)

admissibility_layer_enum = postgresql.ENUM(
    'layer_1',
    'layer_2',
    name='admissibility_layer',
    create_type=False,
)

refusal_kind_enum = postgresql.ENUM(
    # invalidity (5)
    'underspecified-requirement',
    'no-relevant-context',
    'ambiguous-reference',
    'ungrounded-claim',
    'structural-validation-failure',
    # policy (2)
    'low-generation-confidence',
    'no-admissible-negative-scenario-found',
    # operational (1)
    'operational-budget-exhausted',
    name='refusal_kind',
    create_type=False,
)

llm_call_outcome_enum = postgresql.ENUM(
    'success',
    'transient_failure',
    'operational_error',
    'rejected_for_correction',
    name='llm_call_outcome',
    create_type=False,
)

ALL_ENUMS = [
    generation_outcome_kind_enum,
    admissibility_layer_enum,
    refusal_kind_enum,
    llm_call_outcome_enum,
]


def upgrade():
    bind = op.get_bind()

    # -----------------------------------------------------------------------
    # 1. Create enum types
    # -----------------------------------------------------------------------
    for enum_type in ALL_ENUMS:
        enum_type.create(bind, checkfirst=False)

    # -----------------------------------------------------------------------
    # 2. generation_requests — semantic ledger, request half (D-074)
    # -----------------------------------------------------------------------
    # Persists the three-axis context separation (D-071) so replay — an
    # architectural invariant (D-088 / D-090) — can read back the
    # (semantic_context, governance_context) that produced each outcome. One
    # request carries many requirements and produces many generation_outcomes
    # rows (D-071 / D-072 / D-077).
    op.create_table(
        'generation_requests',
        sa.Column('request_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('semantic_context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('governance_context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('operational_context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('prior_request_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('deltas', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('request_id', name='pk_generation_requests'),
        # Real self-FK — single-column-unique target (the S3 FK convention).
        sa.ForeignKeyConstraint(
            ['prior_request_id'],
            ['generation_requests.request_id'],
            name='fk_generation_requests_prior_request_id',
        ),
        # D-071 binary discriminator: a regeneration carries deltas; a fresh
        # request does not.
        sa.CheckConstraint(
            "(prior_request_id IS NULL) = (deltas IS NULL)",
            name='ck_generation_requests_lineage',
        ),
    )
    op.create_index(
        'idx_generation_requests_prior_request_id',
        'generation_requests',
        ['prior_request_id'],
        postgresql_where=sa.text('prior_request_id IS NOT NULL'),
    )
    op.execute(
        "COMMENT ON TABLE generation_requests IS "
        "'Substrate-3 semantic ledger (request half). Persists the D-071 "
        "three-context separation for replay. See substrate_3_generation/"
        "SPEC.md §3.5 + DECISIONS_LOG.md D-074.'"
    )

    # -----------------------------------------------------------------------
    # 3. generation_outcomes — semantic ledger, outcome half (D-074)
    # -----------------------------------------------------------------------
    op.create_table(
        'generation_outcomes',
        sa.Column('outcome_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('request_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('requirement_ref', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('outcome_kind', generation_outcome_kind_enum, nullable=False),
        # draft variant (NULL on refusal)
        sa.Column('claims_written', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('recipes_written', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('equivalent_existing', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('admissibility_layer', admissibility_layer_enum, nullable=True),
        # refusal variant (NULL on draft); refusal_kind row-level exposed (D-074)
        sa.Column('refusal_kind', refusal_kind_enum, nullable=True),
        sa.Column('refusal_policy_version', sa.Text(), nullable=True),
        sa.Column('refusal_schema_version', sa.Text(), nullable=True),
        sa.Column('refusals', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # mandatory on every outcome (D-072)
        sa.Column('attempted_interpretation', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('explanation_hash', sa.Text(), nullable=False),
        sa.Column('dismissal_taxonomy_version', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('outcome_id', name='pk_generation_outcomes'),
        # Real FK to the request half — one request → many outcomes
        # (D-071 / D-072 / D-077). No ondelete cascade: the semantic ledger
        # is audit-grade (default RESTRICT).
        sa.ForeignKeyConstraint(
            ['request_id'],
            ['generation_requests.request_id'],
            name='fk_generation_outcomes_request_id',
        ),
        # Binary protocol per D-072: refusal_kind present iff refusal.
        sa.CheckConstraint(
            "(outcome_kind = 'refusal') = (refusal_kind IS NOT NULL)",
            name='ck_generation_outcomes_kind',
        ),
    )
    op.create_index(
        'idx_generation_outcomes_request_id',
        'generation_outcomes',
        ['request_id'],
    )
    op.create_index(
        'idx_generation_outcomes_explanation_hash',
        'generation_outcomes',
        ['explanation_hash'],
    )
    op.create_index(
        'idx_generation_outcomes_refusal_kind',
        'generation_outcomes',
        ['refusal_kind'],
        postgresql_where=sa.text('refusal_kind IS NOT NULL'),
    )
    op.execute(
        "COMMENT ON TABLE generation_outcomes IS "
        "'Substrate-3 semantic ledger. See substrate_3_generation/SPEC.md "
        "§3.5 + DECISIONS_LOG.md D-074.'"
    )
    op.execute(
        "COMMENT ON COLUMN generation_outcomes.explanation_hash IS "
        "'Substrate-3 explanation-equivalence fingerprint per D-088. "
        "Row-level exposed for replay/drift (D-090).'"
    )
    op.execute(
        "COMMENT ON COLUMN generation_outcomes.request_id IS "
        "'Real FK to generation_requests.request_id (D-071). One request → "
        "many outcomes (D-072 / D-077).'"
    )

    # -----------------------------------------------------------------------
    # 4. llm_calls — operational telemetry (D-087 b)
    # -----------------------------------------------------------------------
    op.create_table(
        'llm_calls',
        sa.Column('call_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('generation_outcome_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tool_name', sa.Text(), nullable=False),
        sa.Column('raw_parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('raw_response', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('operational_outcome', llm_call_outcome_enum, nullable=False),
        sa.Column('attempt_index', sa.Integer(), nullable=False),
        sa.Column('timing_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('timing_duration_ms', sa.Integer(), nullable=True),
        sa.Column('token_count_input', sa.Integer(), nullable=True),
        sa.Column('token_count_output', sa.Integer(), nullable=True),
        sa.Column('model_identifier', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('call_id', name='pk_llm_calls'),
        sa.ForeignKeyConstraint(
            ['generation_outcome_id'],
            ['generation_outcomes.outcome_id'],
            name='fk_llm_calls_generation_outcome_id',
            ondelete='CASCADE',
        ),
    )
    op.create_index(
        'idx_llm_calls_generation_outcome_id',
        'llm_calls',
        ['generation_outcome_id'],
    )
    op.execute(
        "COMMENT ON TABLE llm_calls IS "
        "'Substrate-3 operational telemetry (NOT semantic provenance; "
        "permanently substrate-3-adjacent). See substrate_3_generation/"
        "SPEC.md §6.5 + DECISIONS_LOG.md D-087.'"
    )


def downgrade():
    op.drop_index('idx_llm_calls_generation_outcome_id', table_name='llm_calls')
    op.drop_table('llm_calls')
    op.drop_index('idx_generation_outcomes_refusal_kind', table_name='generation_outcomes')
    op.drop_index('idx_generation_outcomes_explanation_hash', table_name='generation_outcomes')
    op.drop_index('idx_generation_outcomes_request_id', table_name='generation_outcomes')
    op.drop_table('generation_outcomes')
    op.drop_index('idx_generation_requests_prior_request_id', table_name='generation_requests')
    op.drop_table('generation_requests')

    bind = op.get_bind()
    for enum_type in reversed(ALL_ENUMS):
        enum_type.drop(bind, checkfirst=False)

"""create substrate-2 tables

Revision ID: 123534d82956
Revises: 20260515_0010
Create Date: 2026-05-18

Substrate-2 (Test Representation) initial schema. Per
docs/architecture/substrate_2_test_representation/SPEC.md §4.1.

Creates ten enums and six tables:
  Core tables (internal coherence):
    - test_claims              §4.1
    - test_recipes             §4.1
    - test_provenance          §4.1
    - test_claim_coverage      §4.1
  Boundary tables (external interfaces):
    - test_recipe_runtime_state  §4.1 + §8 (S4 boundary)
    - test_requirement_links     §4.1 + §9 (external system boundary)

Design decisions applied (Track A, Phase 4):
  A1 — Single migration (greenfield; tables are co-specified).
  A2 — Native PG enums (DB-level invariant per D-060 §4.7.1).
  A3 — Specified indexes only (no premature optimization).
  A4 — No conflict with substrate-1 (different table names; public schema shared).
  A5 — Default `public` schema (consistency with substrate-1).
  A6 — Logical-only FKs (test_id and recipe_id are not unique in their
       home tables — compound PK with version_seq; cross-substrate FKs
       to S1 entities are logical-only per substrate-isolation principle.
       Coordinator validates referential integrity at write time per
       D-060 §4.7.6 write-flow orchestration).
  A7 — Sparing table comments referencing SPEC sections.
  A8 — Minimal DB-level JSONB validation (key-presence only;
       deep validation is Pydantic-layer per D-060 §4.7.1).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '123534d82956'
down_revision = '20260515_0010'
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Enum definitions
#
# create_type=False — enum lifecycle managed explicitly in upgrade/downgrade
# rather than inline with table creation. This separates enum creation from
# table creation for cleaner migration rollback semantics.
#
# Values preserved verbatim from SPEC §3 and §4.1 (some kebab-case for
# claim/recipe/trigger kinds; some snake_case for event kinds and statuses).
# Pydantic Literal types in Track B will match these values exactly.
# ---------------------------------------------------------------------------

archetype_enum = postgresql.ENUM(
    'data_behavior',
    'configuration',
    'permission',
    'ui',
    'integration',
    name='archetype',
    create_type=False,
)

# All 16 claim_kind values present from day one per SPEC §3 forward-compat
# commitment ("Schema accommodates all five archetypes from day one").
# Pydantic model coverage in Track B will initially be data-behavior only;
# the enum is intentionally wider than the model coverage.
claim_kind_enum = postgresql.ENUM(
    # data-behavior archetype (4)
    'value-claim',
    'state-transition-claim',
    'automation-effect-claim',
    'prohibition-claim',
    # configuration archetype (3)
    'existence-claim',
    'property-claim',
    'metadata-relationship-claim',
    # permission archetype (2)
    'capability-claim',
    'sharing-rule-claim',
    # ui archetype (3)
    'element-state-claim',
    'navigation-claim',
    'layout-claim',
    # integration archetype (4)
    'platform-event-claim',
    'outbound-message-claim',
    'callout-claim',
    'inbound-effect-claim',
    name='claim_kind',
    create_type=False,
)

trigger_kind_enum = postgresql.ENUM(
    'inbound-trigger',
    'data-mutation-trigger',
    'ui-trigger',
    'time-trigger',
    'configuration-trigger',
    name='trigger_kind',
    create_type=False,
)

recipe_kind_enum = postgresql.ENUM(
    'data-recipe',
    'metadata-recipe',
    'ui-recipe',
    'event-subscription-recipe',
    'callout-intercept-recipe',
    name='recipe_kind',
    create_type=False,
)

claim_status_enum = postgresql.ENUM(
    'draft',
    'approved',
    'deprecated',
    name='claim_status',
    create_type=False,
)

recipe_status_enum = postgresql.ENUM(
    'active',
    'deprecated',
    'generated_unapproved',
    'approved',
    name='recipe_status',
    create_type=False,
)

# Per SPEC §4.1; single enum today, multi-stream taxonomy reserved per
# D-061 §7.8 forward-compat.
provenance_event_kind_enum = postgresql.ENUM(
    'claim_created',
    'claim_edited',
    'claim_regenerated',
    'claim_approved',
    'claim_deprecated',
    'recipe_created',
    'recipe_edited',
    'recipe_s8_rewrite',
    'recipe_approved',
    'recipe_deprecated',
    'recipe_priority_changed',
    name='provenance_event_kind',
    create_type=False,
)

# Per SPEC §4.1; binary today, weighted linkage reserved per D-058 §5.8.
reference_kind_enum = postgresql.ENUM(
    'subject',
    'condition',
    name='reference_kind',
    create_type=False,
)

# Per SPEC §4.1 + §8.
run_outcome_enum = postgresql.ENUM(
    'passed',
    'failed',
    'errored',
    'skipped',
    name='run_outcome',
    create_type=False,
)

# Per SPEC §9. Single value today; registry-based evolution reserved per
# D-063 §9.5. Adding values via ALTER TYPE ADD VALUE is supported.
external_system_enum = postgresql.ENUM(
    'jira',
    name='external_system',
    create_type=False,
)

link_kind_enum = postgresql.ENUM(
    'generated_from',
    'verifies',
    'related_to',
    name='link_kind',
    create_type=False,
)


ALL_ENUMS = [
    archetype_enum,
    claim_kind_enum,
    trigger_kind_enum,
    recipe_kind_enum,
    claim_status_enum,
    recipe_status_enum,
    provenance_event_kind_enum,
    reference_kind_enum,
    run_outcome_enum,
    external_system_enum,
    link_kind_enum,
]


def upgrade():
    bind = op.get_bind()

    # -----------------------------------------------------------------------
    # 1. Create enum types
    # -----------------------------------------------------------------------
    for enum_type in ALL_ENUMS:
        enum_type.create(bind, checkfirst=False)

    # -----------------------------------------------------------------------
    # 2. Create tables
    # -----------------------------------------------------------------------

    # test_claims — identity-bearing claim per SPEC §4.1
    op.create_table(
        'test_claims',
        sa.Column('test_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version_seq', sa.Integer(), nullable=False),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('archetype', archetype_enum, nullable=False),
        sa.Column('claim_kind', claim_kind_enum, nullable=False),
        sa.Column('asserted_truth', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('semantic_conditions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('identity_hash', sa.Text(), nullable=False),
        sa.Column('identity_hash_version', sa.Integer(), nullable=False),
        sa.Column('status', claim_status_enum, nullable=False, server_default='draft'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('test_id', 'version_seq', name='pk_test_claims'),
        # JSONB key-presence checks per A8 (universal body convention per SPEC §4.4)
        sa.CheckConstraint(
            "asserted_truth ? 'body_schema_version' AND asserted_truth ? 'kind'",
            name='ck_test_claims_asserted_truth_keys',
        ),
        sa.CheckConstraint(
            "semantic_conditions ? 'body_schema_version' AND semantic_conditions ? 'kind'",
            name='ck_test_claims_semantic_conditions_keys',
        ),
    )
    op.create_index(
        'idx_test_claims_test_id_current',
        'test_claims',
        ['test_id'],
        postgresql_where=sa.text('valid_to IS NULL'),
    )
    op.create_index(
        'idx_test_claims_identity_hash',
        'test_claims',
        ['identity_hash', 'identity_hash_version'],
    )
    op.execute(
        "COMMENT ON TABLE test_claims IS "
        "'Identity-bearing claims. See substrate_2_test_representation/SPEC.md §4.1'"
    )
    op.execute(
        "COMMENT ON COLUMN test_claims.identity_hash IS "
        "'Semantic equivalence fingerprint per D-059. NOT unique; NOT a key.'"
    )
    op.execute(
        "COMMENT ON COLUMN test_claims.identity_hash_version IS "
        "'Canonicalization policy version per D-059 §6.3.7. Hashes only "
        "directly comparable within same identity_hash_version.'"
    )

    # test_recipes — first-class operational entities per SPEC §4.1
    op.create_table(
        'test_recipes',
        sa.Column('recipe_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version_seq', sa.Integer(), nullable=False),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claim_test_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('claim_version_seq', sa.Integer(), nullable=True),
        sa.Column('trigger_kind', trigger_kind_enum, nullable=False),
        sa.Column('recipe_kind', recipe_kind_enum, nullable=False),
        sa.Column('causal_initiation', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('observation_realization', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('execution_environment', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', recipe_status_enum, nullable=False, server_default='generated_unapproved'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('recipe_id', 'version_seq', name='pk_test_recipes'),
        sa.CheckConstraint(
            "causal_initiation ? 'body_schema_version' AND causal_initiation ? 'kind'",
            name='ck_test_recipes_causal_initiation_keys',
        ),
        sa.CheckConstraint(
            "observation_realization ? 'body_schema_version' AND observation_realization ? 'kind'",
            name='ck_test_recipes_observation_realization_keys',
        ),
        sa.CheckConstraint(
            "execution_environment ? 'body_schema_version' AND execution_environment ? 'kind'",
            name='ck_test_recipes_execution_environment_keys',
        ),
    )
    op.create_index(
        'idx_test_recipes_recipe_id_current',
        'test_recipes',
        ['recipe_id'],
        postgresql_where=sa.text('valid_to IS NULL'),
    )
    op.create_index(
        'idx_test_recipes_claim_test_id_current',
        'test_recipes',
        ['claim_test_id'],
        postgresql_where=sa.text('valid_to IS NULL'),
    )
    op.execute(
        "COMMENT ON TABLE test_recipes IS "
        "'First-class operational entities. See substrate_2_test_representation/SPEC.md §4.1'"
    )
    op.execute(
        "COMMENT ON COLUMN test_recipes.claim_test_id IS "
        "'Logical FK to test_claims.test_id (not DB-enforced; test_id is not "
        "unique). Coordinator validates at write time per D-060 §4.7.6.'"
    )
    op.execute(
        "COMMENT ON COLUMN test_recipes.claim_version_seq IS "
        "'Optional pinning to specific claim version per SPEC §6.4. Logical "
        "resolution is the default.'"
    )

    # test_provenance — append-only history, polymorphic across claim/recipe events
    op.create_table(
        'test_provenance',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('claim_test_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('recipe_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event_kind', provenance_event_kind_enum, nullable=False),
        sa.Column('event_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('event_actor', sa.Text(), nullable=False),
        sa.Column('event_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id', name='pk_test_provenance'),
        # Polymorphic constraint per SPEC §4.1: exactly one of claim_test_id / recipe_id
        sa.CheckConstraint(
            '(claim_test_id IS NOT NULL AND recipe_id IS NULL) OR '
            '(claim_test_id IS NULL AND recipe_id IS NOT NULL)',
            name='ck_test_provenance_polymorphic_target',
        ),
    )
    op.create_index(
        'idx_test_provenance_claim_test_id',
        'test_provenance',
        ['claim_test_id'],
        postgresql_where=sa.text('claim_test_id IS NOT NULL'),
    )
    op.create_index(
        'idx_test_provenance_recipe_id',
        'test_provenance',
        ['recipe_id'],
        postgresql_where=sa.text('recipe_id IS NOT NULL'),
    )
    op.execute(
        "COMMENT ON TABLE test_provenance IS "
        "'Append-only history; polymorphic across claim and recipe events. "
        "See substrate_2_test_representation/SPEC.md §4.1'"
    )

    # test_claim_coverage — semantic linkage layer connecting S2 claims to S1 entities
    op.create_table(
        'test_claim_coverage',
        sa.Column('claim_test_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('entity_type', sa.Text(), nullable=False),
        sa.Column('entity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('reference_kind', reference_kind_enum, nullable=False),
        sa.PrimaryKeyConstraint('claim_test_id', 'entity_id', 'reference_kind', name='pk_test_claim_coverage'),
    )
    op.create_index(
        'idx_test_claim_coverage_entity',
        'test_claim_coverage',
        ['entity_id', 'entity_type'],
    )
    op.execute(
        "COMMENT ON TABLE test_claim_coverage IS "
        "'Semantic linkage layer: S2 claims → S1 entities. "
        "See substrate_2_test_representation/SPEC.md §4.1 (semantic linkage layer role) "
        "and §5.4 (coverage derivation rules).'"
    )
    op.execute(
        "COMMENT ON COLUMN test_claim_coverage.entity_id IS "
        "'Logical FK to S1 entity (not DB-enforced; cross-substrate FKs are "
        "logical per substrate-isolation principle). Coordinator validates "
        "at write time.'"
    )

    # test_recipe_runtime_state — S4 boundary; last-run snapshot per recipe
    op.create_table(
        'test_recipe_runtime_state',
        sa.Column('recipe_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('last_run_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_run_outcome', run_outcome_enum, nullable=False),
        sa.Column('last_run_recipe_version_seq', sa.Integer(), nullable=False),
        sa.Column('last_pass_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_failure_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('recipe_id', name='pk_test_recipe_runtime_state'),
    )
    op.create_index(
        'idx_test_recipe_runtime_state_outcome',
        'test_recipe_runtime_state',
        ['last_run_outcome'],
    )
    op.create_index(
        'idx_test_recipe_runtime_state_last_run_at',
        'test_recipe_runtime_state',
        ['last_run_at'],
    )
    op.execute(
        "COMMENT ON TABLE test_recipe_runtime_state IS "
        "'S2/S4 boundary: last-run snapshot per recipe. Pure snapshot — no "
        "aggregate statistics, no history (S4 holds full history). "
        "See substrate_2_test_representation/SPEC.md §8 and D-062.'"
    )
    op.execute(
        "COMMENT ON COLUMN test_recipe_runtime_state.last_run_id IS "
        "'Opaque reference to S4. S2 does not interpret. Idempotency key for "
        "S4 → Coordinator callback per D-062.'"
    )

    # test_requirement_links — external system boundary
    op.create_table(
        'test_requirement_links',
        sa.Column('test_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('external_system', external_system_enum, nullable=False),
        sa.Column('external_key', sa.Text(), nullable=False),
        sa.Column('external_version', sa.Text(), nullable=True),
        sa.Column('link_kind', link_kind_enum, nullable=False),
        sa.Column('linked_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('linked_by', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            'test_id', 'external_system', 'external_key', 'link_kind',
            name='pk_test_requirement_links',
        ),
    )
    op.create_index(
        'idx_test_requirement_links_external_key',
        'test_requirement_links',
        ['external_system', 'external_key'],
    )
    op.execute(
        "COMMENT ON TABLE test_requirement_links IS "
        "'S2/external boundary: typed references to requirement-management "
        "systems. No content replicated. See "
        "substrate_2_test_representation/SPEC.md §9 and D-063.'"
    )


def downgrade():
    bind = op.get_bind()

    # Drop tables in reverse dependency order
    op.drop_table('test_requirement_links')
    op.drop_table('test_recipe_runtime_state')
    op.drop_table('test_claim_coverage')
    op.drop_table('test_provenance')
    op.drop_table('test_recipes')
    op.drop_table('test_claims')

    # Drop enums in reverse creation order
    for enum_type in reversed(ALL_ENUMS):
        enum_type.drop(bind, checkfirst=False)

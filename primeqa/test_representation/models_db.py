"""SQLAlchemy models for substrate-2 (Test Representation).

Tables owned: test_claims, test_recipes, test_provenance,
              test_claim_coverage, test_recipe_runtime_state,
              test_requirement_links

This module is the Python-side projection of what Track A's
migration created in the database. The migration
(``alembic/versions/tenant/20260518_1014_create_substrate_2_tables.py``)
is the source of truth for column types, constraints, and
indexes — every declaration here matches that file exactly so
that ``Base.metadata`` introspection agrees with the actual
schema PostgreSQL holds.

Conventions (per substrate-1 idiom, confirmed at Track D-α HOLD):
  - Project-wide :data:`Base` from ``primeqa.db`` (no
    substrate-local Base).
  - Old-style ``Column(...)`` declarations (no ``Mapped[]`` /
    ``mapped_column()``).
  - Plain singular class names (``TestClaim``, not
    ``TestClaimRow``).
  - Single ``models_db.py`` file (not a ``db/`` package) to
    avoid colliding with the existing ``models/`` directory
    that holds the Pydantic body models.
  - All 11 PG enums declared with ``create_type=False`` because
    Track A's migration already created them.
  - Logical FKs (cross-substrate references + intra-substrate
    references where the target ``test_id`` / ``recipe_id`` is
    not unique on its own) declared without
    :class:`ForeignKey`; documented in column docstrings. The
    Coordinator validates referential integrity at write time
    per D-060 §4.7.6.
  - JSONB columns are raw :class:`JSONB`. Pydantic
    serialization / deserialization is the Coordinator's
    responsibility per D-060 §4.7.1 — the SQLAlchemy model
    treats the column as an opaque JSONB dict.
  - No :func:`relationship` declarations in v1; cross-row
    navigation goes through the repository layer when added.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.sql import func

from primeqa.db import Base


# ---------------------------------------------------------------------------
# Enum types
#
# Mirror the 11 PG enums created by Track A's migration. ``create_type=False``
# tells SQLAlchemy NOT to attempt DDL creation when these types appear in a
# CREATE TABLE statement — the types already exist in the database. The
# `name` of each enum must match the migration exactly so SQLAlchemy
# resolves to the same DB type.
#
# Values are in the same order as the migration; reordering is harmless
# at the DB level (PG stores values by ordinal, set at creation) but the
# Python-side declaration here is for readability + diff-grep clarity
# against the migration.
# ---------------------------------------------------------------------------

ARCHETYPE_ENUM = ENUM(
    "data_behavior",
    "configuration",
    "permission",
    "ui",
    "integration",
    name="archetype",
    create_type=False,
)

CLAIM_KIND_ENUM = ENUM(
    # data-behavior archetype (4)
    "value-claim",
    "state-transition-claim",
    "automation-effect-claim",
    "prohibition-claim",
    # configuration archetype (3)
    "existence-claim",
    "property-claim",
    "metadata-relationship-claim",
    # permission archetype (2)
    "capability-claim",
    "sharing-rule-claim",
    # ui archetype (3)
    "element-state-claim",
    "navigation-claim",
    "layout-claim",
    # integration archetype (4)
    "platform-event-claim",
    "outbound-message-claim",
    "callout-claim",
    "inbound-effect-claim",
    name="claim_kind",
    create_type=False,
)

TRIGGER_KIND_ENUM = ENUM(
    "inbound-trigger",
    "data-mutation-trigger",
    "ui-trigger",
    "time-trigger",
    "configuration-trigger",
    name="trigger_kind",
    create_type=False,
)

RECIPE_KIND_ENUM = ENUM(
    "data-recipe",
    "metadata-recipe",
    "ui-recipe",
    "event-subscription-recipe",
    "callout-intercept-recipe",
    name="recipe_kind",
    create_type=False,
)

CLAIM_STATUS_ENUM = ENUM(
    "draft",
    "approved",
    "deprecated",
    name="claim_status",
    create_type=False,
)

RECIPE_STATUS_ENUM = ENUM(
    "active",
    "deprecated",
    "generated_unapproved",
    "approved",
    name="recipe_status",
    create_type=False,
)

PROVENANCE_EVENT_KIND_ENUM = ENUM(
    "claim_created",
    "claim_edited",
    "claim_regenerated",
    "claim_approved",
    "claim_deprecated",
    "recipe_created",
    "recipe_edited",
    "recipe_s8_rewrite",
    "recipe_approved",
    "recipe_deprecated",
    "recipe_priority_changed",
    name="provenance_event_kind",
    create_type=False,
)

REFERENCE_KIND_ENUM = ENUM(
    "subject",
    "condition",
    name="reference_kind",
    create_type=False,
)

RUN_OUTCOME_ENUM = ENUM(
    "passed",
    "failed",
    "errored",
    "skipped",
    name="run_outcome",
    create_type=False,
)

EXTERNAL_SYSTEM_ENUM = ENUM(
    "jira",
    name="external_system",
    create_type=False,
)

LINK_KIND_ENUM = ENUM(
    "generated_from",
    "verifies",
    "related_to",
    name="link_kind",
    create_type=False,
)


# ---------------------------------------------------------------------------
# test_claims — identity-bearing claims (SPEC §4.1)
# ---------------------------------------------------------------------------

class TestClaim(Base):
    """Identity-bearing claim row.

    Per SPEC §4.1 + D-057 (effective-time supersession). The
    compound PK ``(test_id, version_seq)`` lets multiple rows
    share the same ``test_id`` across the test's version
    timeline; ``valid_to IS NULL`` marks the current version of
    each test_id (enforced by index, not by a unique constraint —
    operational scenarios may temporarily violate the
    "exactly one NULL per test_id" invariant per D-057's
    reconciliation contract).

    ``identity_hash`` + ``identity_hash_version`` together
    fingerprint the claim's semantic content per D-059. The hash
    is NOT a unique key — multiple rows may share it (cross-test
    semantic equivalence) — and comparison is only valid within
    the same ``identity_hash_version``.
    """

    __tablename__ = "test_claims"
    __test__ = False  # pytest collection: not a test class

    test_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    """The test's stable identifier — organizational continuity per
    D-051. Same UUID across the test's version timeline."""

    version_seq = Column(Integer, primary_key=True, nullable=False)
    """Supersession order per D-057. Monotonic per test_id; higher
    values supersede lower ones."""

    valid_from = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    valid_to = Column(DateTime(timezone=True), nullable=True)

    archetype = Column(ARCHETYPE_ENUM, nullable=False)
    claim_kind = Column(CLAIM_KIND_ENUM, nullable=False)

    asserted_truth = Column(JSONB, nullable=False)
    """Identity-bearing JSONB body per SPEC §4.4. Pydantic
    serialization is the Coordinator's responsibility; this
    column stores the canonical dict."""

    semantic_conditions = Column(JSONB, nullable=False)

    identity_hash = Column(Text, nullable=False)
    """Semantic equivalence fingerprint per D-059. NOT unique;
    NOT a key."""

    identity_hash_version = Column(Integer, nullable=False)
    """Canonicalization policy version per D-059 §6.3.7. Hashes
    only directly comparable within the same
    identity_hash_version."""

    status = Column(
        CLAIM_STATUS_ENUM, nullable=False, server_default="draft",
    )

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        # JSONB key-presence checks per A8 + SPEC §4.4 universal body
        # convention. Deep structural validity is enforced by the
        # Pydantic layer (D-060 §4.7.1).
        CheckConstraint(
            "asserted_truth ? 'body_schema_version' "
            "AND asserted_truth ? 'kind'",
            name="ck_test_claims_asserted_truth_keys",
        ),
        CheckConstraint(
            "semantic_conditions ? 'body_schema_version' "
            "AND semantic_conditions ? 'kind'",
            name="ck_test_claims_semantic_conditions_keys",
        ),
        # Current-version lookup: WHERE valid_to IS NULL is the
        # canonical "latest row per test_id" predicate per D-057.
        Index(
            "idx_test_claims_test_id_current",
            "test_id",
            postgresql_where=text("valid_to IS NULL"),
        ),
        # Semantic-equivalence lookup: identity_hash + version
        # together identify "the same meaning under the same policy."
        Index(
            "idx_test_claims_identity_hash",
            "identity_hash", "identity_hash_version",
        ),
    )


# ---------------------------------------------------------------------------
# test_recipes — first-class operational entities (SPEC §4.1)
# ---------------------------------------------------------------------------

class TestRecipe(Base):
    """Operational recipe row — observation realization plus its
    causal initiation and execution-environment assumptions.

    Per SPEC §4.1 + D-057 (recipe-to-claim FK semantics). The
    compound PK ``(recipe_id, version_seq)`` mirrors
    :class:`TestClaim`. The reference to the claim is via
    ``claim_test_id`` (logical FK) plus an optional
    ``claim_version_seq`` for reproducibility-critical contexts
    per SPEC §6.4 — logical resolution is the default.
    """

    __tablename__ = "test_recipes"
    __test__ = False  # pytest collection: not a test class

    recipe_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    version_seq = Column(Integer, primary_key=True, nullable=False)
    valid_from = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    valid_to = Column(DateTime(timezone=True), nullable=True)

    claim_test_id = Column(UUID(as_uuid=True), nullable=False)
    """Logical FK to :attr:`TestClaim.test_id` (NOT DB-enforced;
    test_id is not unique in its home table — compound PK with
    version_seq). The Coordinator validates referential integrity
    at write time per D-060 §4.7.6."""

    claim_version_seq = Column(Integer, nullable=True)
    """Optional pinning to a specific claim version per SPEC
    §6.4. NULL means logical resolution (recipe follows claim
    evolution); a value pins this recipe to that specific
    claim version (for historical replay, audit, etc.)."""

    trigger_kind = Column(TRIGGER_KIND_ENUM, nullable=False)
    recipe_kind = Column(RECIPE_KIND_ENUM, nullable=False)

    causal_initiation = Column(JSONB, nullable=False)
    observation_realization = Column(JSONB, nullable=False)
    execution_environment = Column(JSONB, nullable=False)

    priority = Column(Integer, nullable=False, server_default="0")
    status = Column(
        RECIPE_STATUS_ENUM, nullable=False,
        server_default="generated_unapproved",
    )

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "causal_initiation ? 'body_schema_version' "
            "AND causal_initiation ? 'kind'",
            name="ck_test_recipes_causal_initiation_keys",
        ),
        CheckConstraint(
            "observation_realization ? 'body_schema_version' "
            "AND observation_realization ? 'kind'",
            name="ck_test_recipes_observation_realization_keys",
        ),
        CheckConstraint(
            "execution_environment ? 'body_schema_version' "
            "AND execution_environment ? 'kind'",
            name="ck_test_recipes_execution_environment_keys",
        ),
        Index(
            "idx_test_recipes_recipe_id_current",
            "recipe_id",
            postgresql_where=text("valid_to IS NULL"),
        ),
        Index(
            "idx_test_recipes_claim_test_id_current",
            "claim_test_id",
            postgresql_where=text("valid_to IS NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# test_provenance — append-only history, polymorphic across claim/recipe
# (SPEC §4.1)
# ---------------------------------------------------------------------------

class TestProvenance(Base):
    """Append-only provenance event.

    Per SPEC §4.1 + D-061 §7.8 (forward-compat reservation). The
    table is polymorphic across claim events and recipe events:
    exactly one of ``claim_test_id`` / ``recipe_id`` is non-NULL
    per row, enforced by ``ck_test_provenance_polymorphic_target``.

    Track A added two partial indexes
    (``idx_test_provenance_claim_test_id``,
    ``idx_test_provenance_recipe_id``) beyond SPEC §4.1's literal
    text. These support the read-path queries described in §10.2
    and will land in a future SPEC update.
    """

    __tablename__ = "test_provenance"
    __test__ = False  # pytest collection: not a test class

    id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)

    claim_test_id = Column(UUID(as_uuid=True), nullable=True)
    """Logical FK to :attr:`TestClaim.test_id`. Non-NULL only for
    claim-related events; recipe events leave this NULL."""

    recipe_id = Column(UUID(as_uuid=True), nullable=True)
    """Logical FK to :attr:`TestRecipe.recipe_id`. Non-NULL only
    for recipe-related events."""

    event_kind = Column(PROVENANCE_EVENT_KIND_ENUM, nullable=False)
    event_data = Column(JSONB, nullable=False)
    event_actor = Column(Text, nullable=False)
    event_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "(claim_test_id IS NOT NULL AND recipe_id IS NULL) OR "
            "(claim_test_id IS NULL AND recipe_id IS NOT NULL)",
            name="ck_test_provenance_polymorphic_target",
        ),
        # Added in Track A beyond SPEC §4.1 literal text; future
        # SPEC update. Supports the §10.2 read-path queries
        # ("show me the history of this claim / this recipe").
        Index(
            "idx_test_provenance_claim_test_id",
            "claim_test_id",
            postgresql_where=text("claim_test_id IS NOT NULL"),
        ),
        Index(
            "idx_test_provenance_recipe_id",
            "recipe_id",
            postgresql_where=text("recipe_id IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# test_claim_coverage — semantic linkage S2 → S1 (SPEC §4.1 + §5.4)
# ---------------------------------------------------------------------------

class TestClaimCoverage(Base):
    """Semantic linkage: a claim's coverage over S1 entities.

    Per SPEC §4.1 + §5.4 (coverage derivation rules). Each row
    records that ``claim_test_id`` references an S1 entity
    ``(entity_type, entity_id)`` in the ``reference_kind``
    (subject / condition) slot. The PK is
    ``(claim_test_id, entity_id, reference_kind)`` — the same
    claim may reference the same entity in different slots.
    """

    __tablename__ = "test_claim_coverage"
    __test__ = False  # pytest collection: not a test class

    claim_test_id = Column(
        UUID(as_uuid=True), primary_key=True, nullable=False,
    )
    """Logical FK to :attr:`TestClaim.test_id`."""

    entity_type = Column(Text, nullable=False)
    """The S1 entity type (e.g., ``"Object"``, ``"Field"``). NOT
    part of the PK — the PK is (claim_test_id, entity_id,
    reference_kind), with entity_type denormalized for query
    convenience."""

    entity_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    """Logical FK to the S1 entity row (cross-substrate FK; not
    DB-enforced per substrate-isolation principle, per A6 +
    D-060 §4.7.6)."""

    reference_kind = Column(
        REFERENCE_KIND_ENUM, primary_key=True, nullable=False,
    )

    __table_args__ = (
        Index(
            "idx_test_claim_coverage_entity",
            "entity_id", "entity_type",
        ),
    )


# ---------------------------------------------------------------------------
# test_recipe_runtime_state — S2/S4 boundary (SPEC §4.1 + §8 + D-062)
# ---------------------------------------------------------------------------

class TestRecipeRuntimeState(Base):
    """Last-run snapshot per recipe — the S2/S4 boundary row.

    Per SPEC §4.1 + §8 + D-062. Pure snapshot semantics: NO
    aggregate statistics, NO run history (S4 holds full history;
    S2 holds only the most-recent-run summary). One row per
    recipe; updates replace, not append.
    """

    __tablename__ = "test_recipe_runtime_state"
    __test__ = False  # pytest collection: not a test class

    recipe_id = Column(
        UUID(as_uuid=True), primary_key=True, nullable=False,
    )
    """Logical FK to :attr:`TestRecipe.recipe_id`."""

    last_run_id = Column(UUID(as_uuid=True), nullable=False)
    """Opaque reference to S4 (the run that produced this
    snapshot). S2 does not interpret this; it's the idempotency
    key for the S4 → Coordinator callback per D-062."""

    last_run_at = Column(DateTime(timezone=True), nullable=False)
    last_run_outcome = Column(RUN_OUTCOME_ENUM, nullable=False)
    last_run_recipe_version_seq = Column(Integer, nullable=False)

    last_pass_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)

    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "idx_test_recipe_runtime_state_outcome",
            "last_run_outcome",
        ),
        Index(
            "idx_test_recipe_runtime_state_last_run_at",
            "last_run_at",
        ),
    )


# ---------------------------------------------------------------------------
# test_requirement_links — external system boundary (SPEC §4.1 + §9 + D-063)
# ---------------------------------------------------------------------------

class TestRequirementLink(Base):
    """Typed reference from a test to an external requirement
    system.

    Per SPEC §4.1 + §9 + D-063. No external content is replicated
    into S2 — the row carries only the typed reference plus a
    link kind and audit metadata. The PK is
    ``(test_id, external_system, external_key, link_kind)`` so a
    single test can link to multiple Jira issues, or to the same
    Jira issue under different link kinds.
    """

    __tablename__ = "test_requirement_links"
    __test__ = False  # pytest collection: not a test class

    test_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    """Logical FK to :attr:`TestClaim.test_id`."""

    external_system = Column(
        EXTERNAL_SYSTEM_ENUM, primary_key=True, nullable=False,
    )
    external_key = Column(Text, primary_key=True, nullable=False)
    external_version = Column(Text, nullable=True)
    link_kind = Column(LINK_KIND_ENUM, primary_key=True, nullable=False)

    linked_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    linked_by = Column(Text, nullable=False)

    __table_args__ = (
        Index(
            "idx_test_requirement_links_external_key",
            "external_system", "external_key",
        ),
    )

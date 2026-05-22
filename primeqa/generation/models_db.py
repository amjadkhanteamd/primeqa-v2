"""SQLAlchemy models for substrate-3 (Generation Engine) ledgers.

Tables owned (per-tenant schema):
  - generation_requests  — semantic ledger, request half (D-074)
  - generation_outcomes  — semantic ledger, outcome half (D-074)
  - llm_calls            — operational telemetry (D-074, D-087 b)

Python-side projection of what the Slice-0 migration
(``alembic/versions/tenant/20260520_0010_create_substrate_3_ledger.py``)
creates. Every declaration mirrors that file so ``Base.metadata``
introspection agrees with the schema PostgreSQL holds.

Conventions (mirroring substrate-2 ``test_representation/models_db.py``,
confirmed in the Phase 2 forensic report):
  - Project-wide :data:`Base` from ``primeqa.db`` (no substrate-local
    Base).
  - Old-style ``Column(...)`` declarations (no ``Mapped[]`` /
    ``mapped_column()``).
  - **Schema-per-tenant isolation** (``primeqa/semantic/connection.py``):
    tables live in the per-tenant schema, resolved via ``search_path``.
    NO ``tenant_id`` column and NO ``schema=`` argument — exactly
    substrate-2's model.
  - PG enums declared ``create_type=False`` (the migration owns enum
    lifecycle). Enum type names are substrate-3-scoped so they don't
    collide with substrate-1/-2 enum types in the shared per-tenant
    schema.
  - JSONB columns are opaque dicts; Pydantic ser/deser is the (later)
    coordinator's responsibility.
  - **Typed cross-substrate provenance (D-074):** the row-level fields
    ``refusal_kind`` / ``refusal_policy_version`` / ``refusal_schema_version``
    / ``explanation_hash`` / ``dismissal_taxonomy_version`` are top-level
    columns, NOT buried in JSONB — the substrate-2 forward-commitment.

Slice 0 scope: schema only. No behavior.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.sql import func

from primeqa.db import Base


# ---------------------------------------------------------------------------
# Enum types — mirror the Slice-0 migration. ``create_type=False`` because
# the migration creates them in upgrade(). Names are substrate-3-scoped to
# avoid colliding with substrate-1/-2 enum types in the same per-tenant
# schema (substrate-2 owns ``claim_kind``, ``recipe_status``, ``run_outcome``,
# etc.; none named below).
# ---------------------------------------------------------------------------

GENERATION_OUTCOME_KIND_ENUM = ENUM(
    "draft",
    "refusal",
    name="generation_outcome_kind",
    create_type=False,
)

ADMISSIBILITY_LAYER_ENUM = ENUM(
    "layer_1",
    "layer_2",
    name="admissibility_layer",
    create_type=False,
)

CAVEAT_KIND_ENUM = ENUM(
    "deeper_verification_layer_unparsed",
    name="caveat_kind",
    create_type=False,
)

REFUSAL_KIND_ENUM = ENUM(
    "underspecified-requirement",
    "no-relevant-context",
    "ambiguous-reference",
    "ungrounded-claim",
    "structural-validation-failure",
    "low-generation-confidence",
    "no-admissible-negative-scenario-found",
    "operational-budget-exhausted",
    name="refusal_kind",
    create_type=False,
)

LLM_CALL_OUTCOME_ENUM = ENUM(
    "success",
    "transient_failure",
    "operational_error",
    "rejected_for_correction",
    name="llm_call_outcome",
    create_type=False,
)


# ---------------------------------------------------------------------------
# generation_requests — request half of the semantic ledger (D-074)
# ---------------------------------------------------------------------------

class GenerationRequestRow(Base):
    """One generation request — the request half of the semantic ledger (D-074).

    Persists the three-axis context separation (D-071) as three JSONB columns
    so replay — an architectural invariant (D-088 / D-090; SUBSTRATE_3_WORLDVIEW
    §3) — can read back the ``semantic_context`` + ``governance_context`` that
    produced each outcome. One request carries many requirements and produces
    many :class:`GenerationOutcomeRow` rows (D-071 / D-077 shared interpretation
    context; D-072 one-outcome-per-requirement, no-silent-drops).
    """

    __tablename__ = "generation_requests"

    request_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)

    semantic_context = Column(JSONB, nullable=False)
    """Semantic axis (D-071): requirement refs, S1 version pin, substantive
    content. Half of the replay invariant (D-088)."""
    governance_context = Column(JSONB, nullable=False)
    """Governance axis (D-071 / D-094): policy versions + admissibility-
    confidence threshold. Half of the replay invariant (D-088)."""
    operational_context = Column(JSONB, nullable=False)
    """Operational axis (D-071): prompt / model / retry / budgets. Operational
    variation preserves identity_hash (D-088) — NOT part of the invariant."""

    prior_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "generation_requests.request_id",
            name="fk_generation_requests_prior_request_id",
        ),
        nullable=True,
    )
    """Binary regeneration-lineage discriminator (D-071): NULL → fresh; set →
    the prior request this regenerates. Real self-FK (single-column-unique
    target) per the S3 FK convention."""

    deltas = Column(JSONB, nullable=True)
    """Typed regeneration-delta payload (D-071 / D-074), carrying the
    ``regeneration_kind`` discriminator. Non-NULL iff ``prior_request_id`` is
    set (CHECK below); structural at this slice."""

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        # D-071 binary discriminator: a regeneration carries deltas; a fresh
        # request does not.
        CheckConstraint(
            "(prior_request_id IS NULL) = (deltas IS NULL)",
            name="ck_generation_requests_lineage",
        ),
        # Lineage-chain traversal (D-071 get_request_lineage); partial.
        Index(
            "idx_generation_requests_prior_request_id",
            "prior_request_id",
            postgresql_where=text("prior_request_id IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# generation_outcomes — outcome half of the semantic ledger (D-074)
# ---------------------------------------------------------------------------

class GenerationOutcomeRow(Base):
    """One generation outcome — the semantic-ledger row (D-074).

    Binary protocol (D-072): exactly one of the draft / refusal field
    groups is populated, enforced by ``ck_generation_outcomes_kind``.
    Identity continuity for drafts is via substrate-2's ``identity_hash``
    on the referenced claim (D-088 / D-090) — recorded in
    ``claims_written``, not duplicated here.

    ``request_id`` is a real FK to :class:`GenerationRequestRow` — one
    request produces many outcomes (D-071 / D-077 shared interpretation
    context; D-072 one-outcome-per-requirement). Both rows live in the same
    per-tenant schema and the target is a unique single-column PK, so a real
    FK is idiomatic (the S3 FK convention).
    """

    __tablename__ = "generation_outcomes"

    outcome_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)

    request_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "generation_requests.request_id",
            name="fk_generation_outcomes_request_id",
        ),
        nullable=False,
    )
    """Real FK to :class:`GenerationRequestRow` (D-071). One request → many
    outcomes (D-072 / D-077). No ondelete cascade: the semantic ledger is
    audit-grade, so a request with persisted outcomes is not deletable out
    from under them (default RESTRICT)."""

    requirement_ref = Column(JSONB, nullable=False)
    """The single external requirement this outcome resolves
    (D-072 one-outcome-per-requirement, no-silent-drops)."""

    outcome_kind = Column(GENERATION_OUTCOME_KIND_ENUM, nullable=False)

    # --- draft variant (NULL on refusal) ---
    claims_written = Column(JSONB, nullable=True)
    """List of ``{test_id, version_seq}`` emitted (D-074). May be empty on a
    pure-dedup draft."""
    recipes_written = Column(JSONB, nullable=True)
    """List of ``{recipe_id, version_seq}`` emitted (D-074)."""
    equivalent_existing = Column(JSONB, nullable=True)
    """test_ids of existing claims that satisfy the requirement (dedup,
    D-072)."""
    admissibility_layer = Column(ADMISSIBILITY_LAYER_ENUM, nullable=True)
    """Artifact-level grounding rigor (D-083 e). Populated on drafts."""

    caveat_required = Column(
        Boolean, nullable=False, server_default=text("false"),
    )
    """Emission-time epistemic posture (D-101.3): does the artifact carry a
    plausibility caveat? Persisted (not derived-on-read) so the ledger row is
    self-describing and a future Layer-2 rollout cannot silently rewrite the
    posture of older artifacts. False on refusals and Layer-1-complete drafts."""
    caveat_kind = Column(CAVEAT_KIND_ENUM, nullable=True)
    """The typed qualification class when ``caveat_required`` (D-101.3); the
    semantic-completeness registry verdict snapshot (D-097.3 is the authority).
    Typed posture only — never rendered prose."""

    # --- refusal variant (NULL on draft) ---
    refusal_kind = Column(REFUSAL_KIND_ENUM, nullable=True)
    """Row-level exposed for typed cross-substrate provenance (D-074)."""
    refusal_policy_version = Column(Text, nullable=True)
    """Row-level exposed (D-074); the refusal-policy version in force."""
    refusal_schema_version = Column(Text, nullable=True)
    """Row-level exposed (D-074); the version of the refusals payload
    schema."""
    refusals = Column(JSONB, nullable=True)
    """Flat list of typed refusal payloads (D-073). Structural at this
    slice; per-kind payload models are a later slice."""

    # --- mandatory on every outcome (D-072) ---
    attempted_interpretation = Column(JSONB, nullable=False)
    """The reasoning artifact (D-072 mandatory; D-087 b semantic
    provenance). Structural at this slice; the fully-typed shape is D-075,
    built by the governance-core slice."""
    explanation_hash = Column(Text, nullable=False)
    """Row-level exposed; substrate-3 explanation-equivalence fingerprint
    (D-088) for replay / drift (D-090)."""
    dismissal_taxonomy_version = Column(Text, nullable=False)
    """Row-level exposed (D-074); the ``dismissal_reason`` taxonomy version
    in force (from governance_context, D-076)."""

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        # Binary protocol (D-072): refusal_kind is present iff the outcome
        # is a refusal. The DB enforces the draft/refusal exclusivity that
        # the protocol type expresses by field grouping.
        CheckConstraint(
            "(outcome_kind = 'refusal') = (refusal_kind IS NOT NULL)",
            name="ck_generation_outcomes_kind",
        ),
        # Caveat posture self-consistency (D-101.3): a typed kind is present
        # iff a caveat is required.
        CheckConstraint(
            "caveat_required = (caveat_kind IS NOT NULL)",
            name="ck_generation_outcomes_caveat",
        ),
        # Outcomes-by-request lookup (resolve a request's outcomes).
        Index("idx_generation_outcomes_request_id", "request_id"),
        # Drift / replay lookup by explanation_hash (D-090).
        Index(
            "idx_generation_outcomes_explanation_hash", "explanation_hash",
        ),
        # Refusal analytics by kind (D-090); partial — refusals only.
        Index(
            "idx_generation_outcomes_refusal_kind",
            "refusal_kind",
            postgresql_where=text("refusal_kind IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# llm_calls — operational telemetry (D-087 b)
# ---------------------------------------------------------------------------

class LlmCall(Base):
    """One LLM tool-call telemetry row — operational, NOT semantic (D-087 b).

    Permanently substrate-3-adjacent: unlike :class:`GenerationOutcomeRow`,
    this table does NOT retire to substrate-2 provenance (D-074). Telemetry
    may be lossy (D-087 atomicity discipline) — the non-structural columns
    are nullable; the structural ones (identity, FK, tool, outcome, attempt,
    model) are not.
    """

    __tablename__ = "llm_calls"

    call_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)

    generation_outcome_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "generation_outcomes.outcome_id",
            name="fk_llm_calls_generation_outcome_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    """Real FK — ``outcome_id`` is a unique single-column PK in the same
    per-tenant schema. (Contrast substrate-2's logical-only FKs, which
    target non-unique compound-PK columns; the unique target here makes a
    real FK both possible and idiomatic.)"""

    tool_name = Column(Text, nullable=False)
    """The tool invoked (D-086: propose_semantic_intent / select_canonical /
    emit_outcome). Stored as text per D-087's ``string`` typing — the
    tool-surface vocabulary is a later slice, not locked as an enum here."""

    raw_parameters = Column(JSONB, nullable=True)
    """Untyped tool-call input, for debugging (D-087 b)."""
    raw_response = Column(JSONB, nullable=True)
    """Untyped tool-call response, for debugging (D-087 b)."""

    operational_outcome = Column(LLM_CALL_OUTCOME_ENUM, nullable=False)
    attempt_index = Column(Integer, nullable=False)
    """Correction-loop index within one logical tool emission (D-087 b)."""

    timing_start = Column(DateTime(timezone=True), nullable=True)
    timing_duration_ms = Column(Integer, nullable=True)
    token_count_input = Column(Integer, nullable=True)
    token_count_output = Column(Integer, nullable=True)
    model_identifier = Column(Text, nullable=False)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        Index("idx_llm_calls_generation_outcome_id", "generation_outcome_id"),
    )

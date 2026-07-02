"""Substrate-3 generation protocol types (Pydantic).

The three-context request/outcome protocol per D-071 / D-072 / D-094.
Pure data contracts following substrate-2's concrete-body style
(``ConfigDict(extra="forbid")``, typed fields, field docstrings) — but
these are NOT substrate-2 registry bodies, so they carry no
``body_schema_version`` / ``kind`` discriminators and no
``@register_body``.

Slice 0 scope: the container shapes everything downstream reads and
writes. The refusal payload and ``attempted_interpretation`` are
typed-but-structural here — their full per-kind detail (D-073 / D-083 b)
and the populated reasoning artifact (D-075) arrive with the
governance-core slice. No behavior: no orchestration, admissibility, or
routing.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from primeqa.generation.enums import (
    AdmissibilityLayer,
    CaveatKind,
    OutcomeKind,
    RefusalKind,
    RegenerationKind,
)


# ---------------------------------------------------------------------------
# Type aliases — narrow names for substrate-3 identifiers (cf. substrate-2
# models/common.py). Plain aliases so Pydantic treats them as the
# underlying type; they exist for documentation + grep-ability.
# ---------------------------------------------------------------------------

RequestId = UUID
"""Identifier of a :class:`GenerationRequest`."""

OutcomeId = UUID
"""Identifier of a :class:`GenerationOutcome` (the semantic-ledger row)."""

ExplanationHash = str
"""Substrate-3 explanation-equivalence fingerprint per D-088. Computed
over the ``attempted_interpretation`` semantic substance; the computation
and the replay/drift comparison are the replay controller's concern (a
later slice)."""


class _Base(BaseModel):
    """Local Pydantic base for substrate-3 protocol types.

    Tightens to ``extra='forbid'`` (substrate-2's concrete-body
    discipline) so an unknown/malformed field is rejected at the protocol
    boundary rather than silently carried. Not frozen — construction
    paths assemble these incrementally.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)


# ---------------------------------------------------------------------------
# Three-context separation (D-071, D-094)
# ---------------------------------------------------------------------------

class SemanticContext(_Base):
    """What admissible world state was visible (D-071).

    The semantic axis: the requirement(s) being interpreted, the S1
    version pinned for grounding, and substantive content (domain packs,
    system-rule version, archetype hint). Two requests with the same
    ``SemanticContext`` saw the same semantic world. Identity-bearing
    together with :class:`GovernanceContext` (D-088).
    """

    requirement_refs: list[dict[str, Any]] = Field(default_factory=list)
    """Typed external requirement references (JIRA tickets in v1).
    Structural at this slice — the concrete requirement-ref shape is a
    later slice."""

    s1_version_seq: Optional[int] = None
    """The substrate-1 logical version pinned for grounding (S1's
    ``version_seq``). Snapshot consistency across a generation session
    (PRECONDITIONS §1.4)."""

    s1_version_name: Optional[str] = None
    """Human-facing companion to ``s1_version_seq`` (S1's dual
    identifier)."""

    domain_pack_refs: list[str] = Field(default_factory=list)
    """Domain packs invoked (the S5 knowledge channel), if any."""

    system_rule_version: Optional[str] = None
    """Active system-rule version, if any."""

    archetype_hint: Optional[str] = None
    """Caller's archetype prior (D-077) — guidance, not constraint."""


class GovernanceContext(_Base):
    """What behavioral policy regime was active (D-071, D-094).

    The governance axis: the versioned policies bounding what the substrate
    is willing to assert. Identity-bearing (D-088) — changing governance is
    expected to change ``identity_hash``. Includes the admissibility-
    confidence threshold (semantic risk tolerance) per D-094.
    """

    refusal_policy_version: str = "v1"
    """Versions the refusal calibration (D-073). Refusals are governed
    behavior."""

    dismissal_taxonomy_version: str = "v1"
    """Versions the ``dismissal_reason`` enum (D-076)."""

    transparency_policy_version: str = "v1"
    """Versions the ``explanation_hash`` / transparency commitment
    (D-075)."""

    admissibility_confidence_threshold: Optional[float] = None
    """Semantic risk tolerance per D-094 — how confident the substrate must
    be before asserting a grounded negative (governs the ``policy_restraint``
    refusal cause). Governance, not operational. ``None`` defers to the
    substrate-authored conservative default (the default value itself is a
    later slice's concern)."""


class BudgetSpec(_Base):
    """Per-request operational budget caps per D-088.

    Exhausting any dimension yields an ``operational-budget-exhausted``
    refusal (D-088 c). All optional — absent means unbounded for that
    dimension (the budget policy is a later slice)."""

    token: Optional[int] = None
    time_ms: Optional[int] = None
    tool_call_count: Optional[int] = None


class OperationalContext(_Base):
    """How generation was mechanically executed (D-071).

    The operational axis: execution mechanics that do NOT bear semantic
    identity — operational variation preserves ``identity_hash`` (D-088).
    """

    prompt_template_version: Optional[str] = None
    """The composed prompt version (D-089)."""

    llm_model_identifier: Optional[str] = None
    """The Claude model string for this request (D-091). Operational;
    per-archetype default selection is a later slice."""

    retry_policy: Optional[dict[str, Any]] = None
    """Retry / repair policy. Structural at this slice."""

    budgets: BudgetSpec = Field(default_factory=BudgetSpec)
    """Token / time / tool-call caps (D-088)."""

    enable_bva_boundaries: bool = False
    """D-300: author a bva boundary probe set (firing + just-inside) for
    single-threshold-numeric prohibitions and stamp ``strategy_kind='bva'``.
    OPERATIONAL by construction — the boundary probes are recipes and the
    strategy stamp is identity-external (D-285/D-300), so flipping this flag
    preserves ``identity_hash`` (this axis's defining property; contrast
    GovernanceContext, which is identity-bearing). Loaded from the per-tenant
    ``tenant_agent_settings.llm_enable_bva_boundaries`` (migration 059) by the
    S3 consumer; default OFF = byte-identical authoring."""


# ---------------------------------------------------------------------------
# Request (D-071)
# ---------------------------------------------------------------------------

class GenerationRequest(_Base):
    """The substrate-3 unit of work (D-071).

    Composes the three orthogonal context axes plus an optional typed
    regeneration lineage. The ``(semantic_context, governance_context)``
    pair is the replay invariant (D-088); ``operational_context`` may vary
    without changing semantic identity.
    """

    request_id: RequestId
    semantic_context: SemanticContext
    governance_context: GovernanceContext
    operational_context: OperationalContext

    prior_request_id: Optional[RequestId] = None
    """Binary regeneration discriminator (D-071): ``None`` → fresh request;
    set → regeneration of that prior request."""

    regeneration_kind: Optional[RegenerationKind] = None
    """Typed lineage discriminator (D-071), set iff ``prior_request_id`` is
    set. The full ``deltas`` payload is structural/deferred at this slice."""

    deltas: Optional[dict[str, Any]] = None
    """Typed regeneration-delta payload per ``regeneration_kind`` (D-071).
    Structural at this slice; per-kind detail is a later slice."""


# ---------------------------------------------------------------------------
# Outcome (D-072) + structural sub-shapes
# ---------------------------------------------------------------------------

class ClaimRef(_Base):
    """Reference to an emitted substrate-2 claim version (D-074).

    Identity continuity is via substrate-2's ``identity_hash`` on the
    referenced claim (D-088 / D-090) — this ref points at the claim; it does
    not duplicate the claim's identity here."""

    test_id: UUID
    version_seq: int


class RecipeRef(_Base):
    """Reference to an emitted substrate-2 recipe version (D-074)."""

    recipe_id: UUID
    version_seq: int


class RefusalEntry(_Base):
    """One typed refusal within a refusal outcome (D-073).

    A refusal outcome may carry several (D-073 flat-list multiplicity).
    ``payload`` is structural at this slice — the per-kind typed feedback
    shapes (D-073 / D-083 b) arrive with the governance-core slice."""

    refusal_kind: RefusalKind
    payload: dict[str, Any] = Field(default_factory=dict)


class AttemptedInterpretation(_Base):
    """The reasoning artifact carried on every outcome (D-072, D-087 b).

    Mandatory on draft and refusal alike (D-072). Structural container at
    this slice — the fully-typed shape (scoped neighborhood, candidate
    paths, dismissals with ``dismissal_reason`` codes, selected path) per
    D-075 is built by the governance-core slice. ``extra='allow'`` so the
    skeleton can carry partial content without a schema bump here."""

    model_config = ConfigDict(extra="allow")

    candidate_paths: list[dict[str, Any]] = Field(default_factory=list)
    dismissed_alternatives_by_reason: dict[str, Any] = Field(default_factory=dict)
    selected_path_id: Optional[str] = None
    """``path_id`` is a string (D-086 / D-087); this references the canonical
    candidate's ``path_id``, not a positional integer."""


class GenerationOutcome(_Base):
    """The binary generation outcome (D-072).

    Every requirement resolves to exactly one outcome (the no-silent-drops
    invariant, D-072). Drafts carry the emitted claim/recipe refs +
    ``admissibility_layer``; refusals carry ``refusal_kind`` + structural
    payload(s). Both carry the mandatory ``attempted_interpretation`` +
    ``explanation_hash`` (D-072 / D-088).

    The row-level typed-provenance fields (``refusal_kind``,
    ``refusal_policy_version``, ``refusal_schema_version``,
    ``explanation_hash``, ``dismissal_taxonomy_version``) mirror the
    top-level columns of the semantic-ledger table per D-074 — they are not
    buried inside ``attempted_interpretation``.

    This is the in-memory protocol shape; its persisted projection is
    :class:`primeqa.generation.models_db.GenerationOutcomeRow`.
    """

    outcome_id: OutcomeId
    request_id: RequestId
    requirement_ref: dict[str, Any]
    outcome_kind: OutcomeKind

    # --- draft variant (None on refusal) ---
    claims_written: Optional[list[ClaimRef]] = None
    recipes_written: Optional[list[RecipeRef]] = None
    equivalent_existing: Optional[list[UUID]] = None
    admissibility_layer: Optional[AdmissibilityLayer] = None

    # --- caveat posture (D-101.3) — emission-time epistemic snapshot ---
    caveat_required: bool = False
    """Whether the emitted artifact carries a plausibility caveat. Persisted
    (not derived-on-read) so the ledger row is self-describing and historically
    trustworthy under a future Layer-2 rollout (D-101.3)."""
    caveat_kind: Optional[CaveatKind] = None
    """The typed epistemic-qualification class when ``caveat_required``; the
    registry verdict snapshot (D-097.3 is the authority). Typed posture only —
    never rendered caveat prose (presentation-layer policy, D-101.3)."""

    # --- refusal variant (None on draft) ---
    refusal_kind: Optional[RefusalKind] = None
    refusal_policy_version: Optional[str] = None
    refusal_schema_version: Optional[str] = None
    refusals: Optional[list[RefusalEntry]] = None

    # --- mandatory on every outcome (D-072) ---
    attempted_interpretation: AttemptedInterpretation
    explanation_hash: ExplanationHash
    dismissal_taxonomy_version: str

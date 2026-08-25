"""Semantic Transaction Coordinator — class skeleton (Track D-β.1).

Per SPEC §4.7.5 + §10.1 + D-064. The Coordinator is **substrate-2's
semantic OS infrastructure**: every API-driven write to substrate-2
routes through this class. Consuming substrates (S3, S4, S6, S8)
interact with substrate-2 exclusively through Coordinator
interfaces. Direct DB writes bypass Pydantic-layer invariants and
substrate guarantees and are not supported.

The Coordinator owns the 11-step write-flow orchestration per
SPEC §4.7.6:

  1. Build asserted_truth + semantic_conditions Pydantic models.
  2. Validate references' ontology + identity-bearing status.
  3. Compute the new identity_hash + identity_hash_version.
  4. Look up the prior version (if any) via test_id.
  5. Check authority (via :mod:`authority`).
  6. Short-circuit if authority decided no-op.
  7. Compute new ``version_seq`` (prior + 1, or 1 if no prior).
  8. Set ``status`` per the authority decision.
  9. Extract coverage (via :mod:`coverage`) and DELETE+INSERT.
  10. INSERT the new claim row; UPDATE prior version's
      ``valid_to``.
  11. Append a :class:`TestProvenance` row.

This skeleton (Track D-β.1) defines the class shape + method
signatures + dataclass result types. The methods themselves
raise :class:`NotImplementedError` pointing to the implementing
sub-track:
  - :meth:`SemanticTransactionCoordinator.write_claim` →
    Track D-β.2.
  - :meth:`SemanticTransactionCoordinator.write_recipe` →
    Track D-β.3.

Read interfaces + resolution operations land in Track D-γ.
"""
from __future__ import annotations

import uuid as _uuid_mod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.test_representation.authority import (
    ActorKind,
    check_claim_write_authority,
    check_recipe_write_authority,
    check_runtime_state_write_authority,
    enforce_authority,
)
from primeqa.test_representation.canonicalization import canonicalize
from primeqa.test_representation.coverage import extract_coverage
from primeqa.test_representation.errors import (
    AuthorityViolationError,
    BodyCorruptionError,
    OntologyViolationError,
    SchemaIncompatibilityError,
)
from primeqa.test_representation.identity_hash import (
    IDENTITY_HASH_VERSION,
    compute_identity_hash,
)
from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.environment import (
    ExecutionEnvironmentBody,
)
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
)
from primeqa.test_representation.models.registry import get_body_model
from primeqa.test_representation.models_db import (
    TestClaim,
    TestClaimCoverage,
    TestProvenance,
    TestRecipe,
    TestRecipeRuntimeState,
    TestRequirementLink,
)


# ---------------------------------------------------------------------------
# Discriminator value taxonomies (mirror Track A's PG enums).
# ---------------------------------------------------------------------------

_VALID_ARCHETYPES = frozenset({
    "data_behavior", "configuration", "permission", "ui", "integration",
})

_VALID_CLAIM_KINDS = frozenset({
    # data-behavior (5)
    "value-claim", "state-transition-claim",
    "automation-effect-claim", "prohibition-claim", "acceptance-claim",
    # configuration (3)
    "existence-claim", "property-claim", "metadata-relationship-claim",
    # permission (2)
    "capability-claim", "sharing-rule-claim",
    # ui (4)
    "element-state-claim", "navigation-claim", "layout-claim",
    "conformance-claim",
    # integration (4)
    "platform-event-claim", "outbound-message-claim",
    "callout-claim", "inbound-effect-claim",
})

_VALID_TRIGGER_KINDS = frozenset({
    "inbound-trigger",
    "data-mutation-trigger",
    "ui-trigger",
    "time-trigger",
    "configuration-trigger",
    "inspection-trigger",   # D-099 — invariant-inspective initiation mode
})

_VALID_RECIPE_KINDS = frozenset({
    "data-recipe",
    "metadata-recipe",
    "ui-recipe",
    "event-subscription-recipe",
    "callout-intercept-recipe",
    "ui-inspection",
})


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteClaimResult:
    """The return shape of
    :meth:`SemanticTransactionCoordinator.write_claim`.

    Returned both for newly-written versions and for SPEC §7.7
    S3 same-hash no-op skips (in which case ``was_noop=True`` and
    the metadata fields reflect the EXISTING (unchanged) version
    rather than a fresh write).
    """

    test_id: UUID
    version_seq: int
    identity_hash: str
    status: str
    was_noop: bool


@dataclass(frozen=True)
class WriteRecipeResult:
    """The return shape of
    :meth:`SemanticTransactionCoordinator.write_recipe`."""

    recipe_id: UUID
    version_seq: int
    status: str


# ---------------------------------------------------------------------------
# Read result shapes (Track D-γ.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimRead:
    """Read result for a single claim version.

    Pydantic bodies (``asserted_truth`` /
    ``semantic_conditions``) are deserialized from their JSONB
    storage form into the registered Pydantic body classes for
    their declared ``(kind, body_schema_version)``. Row metadata
    (timestamps, status, hash) is the SQLAlchemy row's column
    values.

    A note on lifecycle: ``valid_to`` and ``status`` are
    orthogonal axes per SPEC §6.6 + D-061. ``valid_to`` closes
    when a row is SUPERSEDED by a newer version (a write of
    ``version_seq + 1``); ``status`` (``draft`` / ``approved`` /
    ``deprecated``) is a separate semantic-lifecycle marker.
    A deprecated current version still has ``valid_to IS NULL``
    and is returned by :meth:`get_latest_claim`; consumers
    check ``status`` separately if they care.
    """

    test_id: UUID
    version_seq: int
    valid_from: datetime
    valid_to: Optional[datetime]
    archetype: str
    claim_kind: str
    asserted_truth: BodyBase
    semantic_conditions: BodyBase
    identity_hash: str
    identity_hash_version: int
    status: str
    created_at: datetime
    updated_at: datetime
    # D-285 (Slice 4f.0): the recorded evaluation strategy (`single` / `bva`),
    # NULL until generation sets it in 4f.2. The router + decision engine READ
    # this (never derive). Trailing optional so existing constructions are
    # unaffected; the projection always supplies the column value.
    strategy_kind: Optional[str] = None


@dataclass(frozen=True)
class RecipeRead:
    """Read result for a single recipe version. Same lifecycle
    semantics as :class:`ClaimRead` per SPEC §6.6."""

    recipe_id: UUID
    version_seq: int
    valid_from: datetime
    valid_to: Optional[datetime]
    claim_test_id: UUID
    claim_version_seq: Optional[int]
    trigger_kind: str
    recipe_kind: str
    causal_initiation: BodyBase
    observation_realization: BodyBase
    execution_environment: BodyBase
    priority: int
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CoverageMatch:
    """A coverage match from
    :meth:`SemanticTransactionCoordinator.list_tests_affected_by_entity`.

    Identifies a test that references the queried entity along
    with the slot (``"subject"`` vs ``"condition"``) the
    reference occupies. The slot is meaningful for S6 impact
    attribution and S8 evolution detection: subject references
    are stronger coupling than condition references.
    """

    test_id: UUID
    reference_kind: str


@dataclass(frozen=True)
class RequirementMatch:
    """A requirement-link match from
    :meth:`SemanticTransactionCoordinator.list_tests_by_requirement`.

    Carries the link kind (``"generated_from"`` /
    ``"verifies"`` / ``"related_to"``) so callers can
    distinguish, for example, a test that was generated from a
    requirement vs one that merely verifies it.
    """

    test_id: UUID
    link_kind: str
    external_version: Optional[str]
    linked_at: datetime


# The link kinds that constitute COVERAGE of a requirement: tests S3
# generated from it plus tests a human curated onto it as verifying it.
# ``related_to`` is deliberately excluded — it is an associative pointer,
# not a coverage assertion. Every business-facing "which tests cover this
# requirement" read (requirement plan, chips, run-from-requirement
# dispatch, release verdicts, GO/NO-GO) filters on THIS set; provenance
# reads ("which requirement generated this claim") stay generated_from.
COVERAGE_LINK_KINDS: tuple = ("generated_from", "verifies")


# ---------------------------------------------------------------------------
# Boundary-interface result shapes (Track D-δ)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecipeRuntimeState:
    """Runtime state snapshot for a recipe per SPEC §8.2.

    One row per ``recipe_id`` (NOT per ``(recipe_id, version_seq)``);
    a new recipe version inherits the snapshot. The
    ``last_run_recipe_version_seq`` column records which version
    was last actually run, so consumers can detect stale state
    (recipe has new content but runtime state predates it).
    """

    recipe_id: UUID
    last_run_id: UUID
    last_run_at: datetime
    last_run_outcome: str
    last_run_recipe_version_seq: int
    last_pass_at: Optional[datetime]
    last_failure_at: Optional[datetime]
    updated_at: datetime


@dataclass(frozen=True)
class RequirementLinkResult:
    """Result of
    :meth:`SemanticTransactionCoordinator.link_requirement` /
    :meth:`SemanticTransactionCoordinator.unlink_requirement`.

    ``was_noop=True`` indicates a re-link with identical
    metadata (including ``external_version``) — no DB write was
    performed.
    """

    test_id: UUID
    external_system: str
    external_key: str
    link_kind: str
    external_version: Optional[str]
    linked_at: datetime
    linked_by: str
    was_noop: bool


# ---------------------------------------------------------------------------
# Coordinator class
# ---------------------------------------------------------------------------


class SemanticTransactionCoordinator:
    """Substrate-2's write surface.

    All API-driven writes route through this class. Sessions are
    caller-provided (Track D-β established the construction
    contract — the Coordinator does not manage its own DB sessions;
    callers pass an active :class:`Session` per write).

    v1 scope:
      - :meth:`write_claim` (Track D-β.2)
      - :meth:`write_recipe` (Track D-β.3)
    Read interfaces and resolution operations arrive in Track D-γ.

    The class is stateless across calls — no per-instance caches,
    no held connections. Construct once at app startup; reuse for
    the process lifetime."""

    def __init__(self) -> None:
        """No instantiation parameters. Sessions are caller-provided
        per write per the substrate D-β construction contract."""
        # No state — the Coordinator is a pure dispatcher.
        # Per-write context lives on method arguments.
        pass

    def write_claim(
        self,
        session: Session,
        *,
        actor: ActorKind,
        test_id: Optional[UUID],
        archetype: str,
        claim_kind: str,
        asserted_truth: BodyBase,
        semantic_conditions: BodyBase,
        strategy_kind: Optional[str] = None,
    ) -> WriteClaimResult:
        """Write a new claim version per SPEC §4.7.6's 11-step
        orchestration.

        Args:
          session: Active SQLAlchemy session. The Coordinator
            writes within the caller's transaction; the caller
            decides when to commit. (Per D-β construction
            contract.)
          actor: The mutation actor — ``"human"`` / ``"s3"`` /
            ``"s8"``. Authority enforcement per
            :mod:`authority`.
          test_id: ``None`` to create a new test; existing UUID
            to add a new version of an existing test.
          archetype: One of the 5 archetype values per D-052.
          claim_kind: One of the 16 claim-kind values per
            D-053. Must belong to ``archetype``.
          asserted_truth: The claim body (any concrete subclass
            of :class:`BodyBase` whose ``kind`` matches
            ``claim_kind``).
          semantic_conditions: The conditions body (typically
            :class:`SemanticConditionsBody`).
          strategy_kind: D-285 (Slice 4f.0) — the claim's recorded
            evaluation strategy (``single`` / ``bva``), or ``None``
            (the default, persisted as NULL → routes single). Set
            by generation in 4f.2; every caller before then omits
            it. READ (never derived) by the router + decision engine.

        Returns:
          A :class:`WriteClaimResult` with the new version's
          metadata. For S3 same-hash no-ops, ``was_noop=True``
          and the metadata fields describe the EXISTING version
          per SPEC §7.7.

        Raises:
          :class:`AuthorityViolationError` — if authority denies.
          :class:`OntologyViolationError` — if reference
          ontology fails.
          Pydantic ``ValidationError`` — if body construction
          fails (the caller usually surfaces these to the API
          layer).

        Per SPEC §4.7.6 — 11 canonical steps, in order:

          0. Resolve ``test_id`` + look up prior version.
          1. Discriminator validation (archetype + claim_kind in
             their closed taxonomies).
          2. Body-shape dispatch via the registry — concrete
             types must match ``(kind, body_schema_version)``.
          3. Body validation — Pydantic models already validated
             at construction; defensive isinstance check here.
          4. Body-row discriminator consistency
             (``asserted_truth.kind == claim_kind``;
             ``semantic_conditions.kind == "conditions"``).
          5. Cross-layer ontology — defensive walk via
             extract_coverage (step 9) which rejects operational
             refs.
          6. Cross-body validation — no-op for claims at v1
             (conditions are claim-kind-independent).
          7. Canonicalization (raises Track C errors on
             violation).
          8. Hash computation.
          9. Coverage extraction.
         10. Authority enforcement (with early no-op return per
             SPEC §7.7 for S3 same-hash regenerations).
         11. DB writes: close prior version, insert new claim,
             rederive coverage, append provenance event.

        Concurrent-write semantics: two callers attempting to add
        version N+1 to the same ``test_id`` will collide on the
        compound primary key ``(test_id, version_seq)``. The
        second writer's INSERT raises :class:`IntegrityError`
        from psycopg2; retry is the caller's responsibility.
        """
        # ----- Step 0: resolve test_id + look up prior version --
        if test_id is None:
            test_id = _uuid_mod.uuid4()
            prior: Optional[TestClaim] = None
        else:
            prior = (
                session.query(TestClaim)
                .filter(
                    TestClaim.test_id == test_id,
                    TestClaim.valid_to.is_(None),
                )
                .first()
            )
        has_prior_version = prior is not None

        # ----- Step 1: discriminator validation -----------------
        if archetype not in _VALID_ARCHETYPES:
            raise ValueError(
                f"unknown archetype: {archetype!r}; expected one of "
                f"{sorted(_VALID_ARCHETYPES)}"
            )
        if claim_kind not in _VALID_CLAIM_KINDS:
            raise ValueError(
                f"unknown claim_kind: {claim_kind!r}; expected one of "
                f"{sorted(_VALID_CLAIM_KINDS)}"
            )

        # ----- Step 2: body-shape dispatch via the registry -----
        cond_cls = get_body_model(
            "conditions", semantic_conditions.body_schema_version,
        )
        if cond_cls is not type(semantic_conditions):
            raise SchemaIncompatibilityError(
                f"semantic_conditions is "
                f"{type(semantic_conditions).__name__}; registry returned "
                f"{cond_cls.__name__} for "
                f"(kind='conditions', body_schema_version="
                f"{semantic_conditions.body_schema_version})",
                kind="conditions",
                body_schema_version=semantic_conditions.body_schema_version,
            )
        claim_cls = get_body_model(
            claim_kind, asserted_truth.body_schema_version,
        )
        if claim_cls is not type(asserted_truth):
            raise SchemaIncompatibilityError(
                f"asserted_truth is {type(asserted_truth).__name__}; "
                f"registry returned {claim_cls.__name__} for "
                f"(kind={claim_kind!r}, body_schema_version="
                f"{asserted_truth.body_schema_version})",
                kind=claim_kind,
                body_schema_version=asserted_truth.body_schema_version,
            )

        # ----- Step 3: body validation (typed-input contract) ---
        # Pydantic enforces structure at construction; defensive
        # isinstance check here keeps the step explicit per SPEC
        # §4.7.6.
        if not isinstance(asserted_truth, BodyBase):
            raise BodyCorruptionError(
                f"asserted_truth must be a BodyBase subclass; "
                f"got {type(asserted_truth).__name__}",
                kind=claim_kind,
            )
        if not isinstance(semantic_conditions, BodyBase):
            raise BodyCorruptionError(
                f"semantic_conditions must be a BodyBase subclass; "
                f"got {type(semantic_conditions).__name__}",
                kind="conditions",
            )

        # ----- Step 4: body-row discriminator consistency -------
        if asserted_truth.kind != claim_kind:
            raise BodyCorruptionError(
                f"asserted_truth.kind={asserted_truth.kind!r} but "
                f"the caller declared claim_kind={claim_kind!r}; "
                f"these MUST match (universal §4.4 body convention)",
                kind=claim_kind,
            )
        if semantic_conditions.kind != "conditions":
            raise BodyCorruptionError(
                f"semantic_conditions.kind="
                f"{semantic_conditions.kind!r} but expected "
                f"'conditions'",
                kind="conditions",
            )

        # ----- Step 5: cross-layer ontology ---------------------
        # Typed Pydantic bodies enforce ref-type discipline at
        # construction (identity-bearing slots accept only
        # IdentityBearingRef). The defensive walk happens inside
        # extract_coverage (step 9), which raises SubstrateError
        # if an operational ref slips through. Step 5 is the
        # SPEC-labeled slot; in v1 it's satisfied by Pydantic
        # field-type discipline plus step 9's walk.

        # ----- Step 6: cross-body validation --------------------
        # Conditions are claim-kind-independent per SPEC §3 — no
        # cross-body check needed at v1. Documented step.

        # ----- Step 7: canonicalization -------------------------
        # Raises Track C errors (FloatInIdentityBearingContentError,
        # UnmarkedArraySemanticsError, etc.) on policy violations.
        # Output unused (recomputed inside step 8); the call here
        # surfaces any canonicalization error early.
        canonicalize(asserted_truth)
        canonicalize(semantic_conditions)

        # ----- Step 8: hash computation -------------------------
        new_hash = compute_identity_hash(
            archetype, claim_kind, asserted_truth, semantic_conditions,
        )
        hash_changed: Optional[bool]
        if prior is None:
            hash_changed = None
        else:
            hash_changed = new_hash != prior.identity_hash

        # ----- Step 9: coverage extraction ----------------------
        # Also performs the step-5 defensive ref-type walk.
        coverage_entries = extract_coverage(
            asserted_truth, semantic_conditions,
        )

        # ----- Step 10: authority enforcement -------------------
        decision = check_claim_write_authority(
            actor, has_prior_version, hash_changed,
        )
        if decision.is_noop:
            # SPEC §7.7: S3 same-hash regeneration is a no-op
            # skip. Return existing version's metadata; no DB
            # writes, no provenance event.
            assert prior is not None  # is_noop implies prior exists
            return WriteClaimResult(
                test_id=prior.test_id,
                version_seq=prior.version_seq,
                identity_hash=prior.identity_hash,
                status=prior.status,
                was_noop=True,
            )
        enforce_authority(decision)

        # ----- Step 11: DB writes -------------------------------
        new_version_seq = 1 if prior is None else prior.version_seq + 1
        if decision.new_status is not None:
            new_status = decision.new_status
        elif prior is not None:
            new_status = prior.status
        else:
            # Defensive: no prior + no explicit new_status from
            # authority would be a policy bug. Fall back to draft.
            new_status = "draft"

        # event_kind selection per SPEC §4.1's
        # provenance_event_kind enum:
        if prior is None:
            event_kind = "claim_created"
        elif actor == "s3" and hash_changed:
            # S3-driven hash change is a regeneration event,
            # distinct from a human edit per the §4.1 taxonomy.
            event_kind = "claim_regenerated"
        else:
            event_kind = "claim_edited"

        # (a) Close the prior version's valid_to window.
        if prior is not None:
            session.execute(
                text(
                    "UPDATE test_claims SET valid_to = NOW() "
                    "WHERE test_id = :tid AND version_seq = :vs"
                ),
                {"tid": str(test_id), "vs": prior.version_seq},
            )

        # (b) INSERT the new claim version.
        new_claim = TestClaim(
            test_id=test_id,
            version_seq=new_version_seq,
            archetype=archetype,
            claim_kind=claim_kind,
            asserted_truth=asserted_truth.model_dump(mode="json"),
            semantic_conditions=semantic_conditions.model_dump(mode="json"),
            identity_hash=new_hash,
            identity_hash_version=IDENTITY_HASH_VERSION,
            status=new_status,
            strategy_kind=strategy_kind,   # D-285: NULL until 4f.2 sets it
        )
        session.add(new_claim)
        session.flush()

        # (c) Delete prior coverage for this test_id (current-only
        # rederivation per SPEC §6.5).
        session.execute(
            text(
                "DELETE FROM test_claim_coverage "
                "WHERE claim_test_id = :tid"
            ),
            {"tid": str(test_id)},
        )

        # (d) Insert fresh coverage from step 9.
        for entry in coverage_entries:
            session.add(TestClaimCoverage(
                claim_test_id=test_id,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                reference_kind=entry.reference_kind,
            ))

        # (e) Append the provenance event.
        event_data = {
            "actor": actor,
            "prior_version_seq": (
                prior.version_seq if prior is not None else None
            ),
            "new_version_seq": new_version_seq,
            "hash_changed": hash_changed,
            "new_status": new_status,
            "new_identity_hash": new_hash,
            "new_identity_hash_version": IDENTITY_HASH_VERSION,
        }
        session.add(TestProvenance(
            id=_uuid_mod.uuid4(),
            claim_test_id=test_id,
            recipe_id=None,
            event_kind=event_kind,
            event_data=event_data,
            event_actor=actor,
        ))

        session.flush()
        # Coordinator does NOT commit. Caller owns transaction
        # boundary per the D-β construction contract.

        return WriteClaimResult(
            test_id=test_id,
            version_seq=new_version_seq,
            identity_hash=new_hash,
            status=new_status,
            was_noop=False,
        )

    def write_recipe(
        self,
        session: Session,
        *,
        actor: ActorKind,
        recipe_id: Optional[UUID],
        claim_test_id: UUID,
        trigger_kind: str,
        recipe_kind: str,
        causal_initiation: BodyBase,
        observation_realization: BodyBase,
        execution_environment: BodyBase,
        claim_version_seq: Optional[int] = None,
        priority: int = 0,
    ) -> WriteRecipeResult:
        """Write a new recipe version per SPEC §4.7.6.

        Per SPEC §4.7.6 — same 11-step canonical order as
        :meth:`write_claim`, with three steps marked N/A for
        recipes:

          0. Resolve ``recipe_id`` + look up prior version.
          1. Discriminator validation (trigger_kind + recipe_kind
             in their closed taxonomies).
          2. Body-shape dispatch via the registry — concrete
             types must match ``(kind, body_schema_version)`` for
             trigger, recipe, and execution-environment bodies.
          3. Body validation — defensive isinstance check.
          4. Body-row discriminator consistency
             (``causal_initiation.kind == trigger_kind``;
             ``observation_realization.kind == recipe_kind``;
             ``execution_environment.kind == "execution_environment"``).
          5. Cross-layer ontology — defensive walk verifying NO
             :class:`IdentityBearingRef` instances appear in
             operational-layer bodies (the inverse of
             :meth:`write_claim`'s step 5; operational layers
             use :data:`OperationalRef` only).
          6. Cross-body validation — REAL for recipes: verify
             ``claim_test_id`` (and optional
             ``claim_version_seq`` pinning per SPEC §6.4)
             resolves to an existing claim row. This is the
             substrate's only logical-FK enforcement in v1 (per
             D-α A6 — cross-substrate FKs are logical-only;
             Coordinator validates).
          7. Canonicalization — **N/A** (recipes have no
             ``identity_hash`` per SPEC §6.4).
          8. Hash computation — **N/A**.
          9. Coverage extraction — **N/A** (coverage is
             claim-only per SPEC §4.5).
         10. Authority enforcement via
             :func:`check_recipe_write_authority`. Current v1
             policy: always allowed, never no-op, always
             ``new_status="generated_unapproved"`` per SPEC
             §7.4's conservative re-approval default. The call
             stays for symmetry and future policy evolution.
         11. DB writes: close prior version, INSERT new recipe,
             append provenance event. No coverage rederivation
             (step 9 N/A).

        Args:
          claim_version_seq: Optional pinning to a specific
            claim version per SPEC §6.4. ``None`` (default) means
            logical resolution — recipe follows claim evolution.
            A non-None value pins this recipe to that specific
            claim version for reproducibility-critical contexts
            (historical replay, audit).

        Returns:
          A :class:`WriteRecipeResult` with the new version's
          metadata. Recipes never produce a no-op result (the
          dataclass has no ``was_noop`` field).

        Concurrent-write semantics: same as
        :meth:`write_claim`. Two callers attempting to add
        version N+1 for the same ``recipe_id`` collide on the
        compound primary key ``(recipe_id, version_seq)``; the
        second writer's INSERT raises :class:`IntegrityError`.
        Retry is the caller's responsibility per SPEC §7.7.
        """
        # ----- Step 0: resolve recipe_id + look up prior version -
        if recipe_id is None:
            recipe_id = _uuid_mod.uuid4()
            prior: Optional[TestRecipe] = None
        else:
            prior = (
                session.query(TestRecipe)
                .filter(
                    TestRecipe.recipe_id == recipe_id,
                    TestRecipe.valid_to.is_(None),
                )
                .first()
            )
        has_prior_version = prior is not None

        # ----- Step 1: discriminator validation ------------------
        if trigger_kind not in _VALID_TRIGGER_KINDS:
            raise ValueError(
                f"unknown trigger_kind: {trigger_kind!r}; expected "
                f"one of {sorted(_VALID_TRIGGER_KINDS)}"
            )
        if recipe_kind not in _VALID_RECIPE_KINDS:
            raise ValueError(
                f"unknown recipe_kind: {recipe_kind!r}; expected "
                f"one of {sorted(_VALID_RECIPE_KINDS)}"
            )

        # ----- Step 2: body-shape dispatch via the registry ------
        trigger_cls = get_body_model(
            trigger_kind, causal_initiation.body_schema_version,
        )
        if trigger_cls is not type(causal_initiation):
            raise SchemaIncompatibilityError(
                f"causal_initiation is "
                f"{type(causal_initiation).__name__}; registry returned "
                f"{trigger_cls.__name__} for "
                f"(kind={trigger_kind!r}, body_schema_version="
                f"{causal_initiation.body_schema_version})",
                kind=trigger_kind,
                body_schema_version=causal_initiation.body_schema_version,
            )
        recipe_cls = get_body_model(
            recipe_kind, observation_realization.body_schema_version,
        )
        if recipe_cls is not type(observation_realization):
            raise SchemaIncompatibilityError(
                f"observation_realization is "
                f"{type(observation_realization).__name__}; registry "
                f"returned {recipe_cls.__name__} for "
                f"(kind={recipe_kind!r}, body_schema_version="
                f"{observation_realization.body_schema_version})",
                kind=recipe_kind,
                body_schema_version=(
                    observation_realization.body_schema_version
                ),
            )
        env_cls = get_body_model(
            "execution_environment",
            execution_environment.body_schema_version,
        )
        if env_cls is not type(execution_environment):
            raise SchemaIncompatibilityError(
                f"execution_environment is "
                f"{type(execution_environment).__name__}; registry "
                f"returned {env_cls.__name__} for "
                f"(kind='execution_environment', body_schema_version="
                f"{execution_environment.body_schema_version})",
                kind="execution_environment",
                body_schema_version=(
                    execution_environment.body_schema_version
                ),
            )

        # ----- Step 3: body validation (typed-input contract) ----
        for label, body in (
            ("causal_initiation", causal_initiation),
            ("observation_realization", observation_realization),
            ("execution_environment", execution_environment),
        ):
            if not isinstance(body, BodyBase):
                raise BodyCorruptionError(
                    f"{label} must be a BodyBase subclass; got "
                    f"{type(body).__name__}",
                    kind=getattr(body, "kind", None),
                )

        # ----- Step 4: body-row discriminator consistency --------
        if causal_initiation.kind != trigger_kind:
            raise BodyCorruptionError(
                f"causal_initiation.kind={causal_initiation.kind!r} "
                f"but the caller declared trigger_kind="
                f"{trigger_kind!r}; these MUST match (universal "
                f"§4.4 body convention)",
                kind=trigger_kind,
            )
        if observation_realization.kind != recipe_kind:
            raise BodyCorruptionError(
                f"observation_realization.kind="
                f"{observation_realization.kind!r} but the caller "
                f"declared recipe_kind={recipe_kind!r}",
                kind=recipe_kind,
            )
        if execution_environment.kind != "execution_environment":
            raise BodyCorruptionError(
                f"execution_environment.kind="
                f"{execution_environment.kind!r} but expected "
                f"'execution_environment'",
                kind="execution_environment",
            )

        # ----- Step 5: cross-layer ontology ----------------------
        # Operational-layer bodies must NOT contain
        # IdentityBearingRef instances — those are reserved for
        # identity-bearing slots. Pydantic's field-type discipline
        # via OperationalRef union enforces this structurally;
        # the walk here is defence-in-depth.
        _verify_no_identity_bearing_refs(
            causal_initiation, "causal_initiation",
        )
        _verify_no_identity_bearing_refs(
            observation_realization, "observation_realization",
        )
        _verify_no_identity_bearing_refs(
            execution_environment, "execution_environment",
        )

        # ----- Step 6: cross-body validation (REAL for recipes) --
        # Verify the recipe's claim_test_id resolves. This is the
        # substrate's only logical-FK enforcement in v1.
        if claim_version_seq is None:
            claim_exists = session.execute(
                text(
                    "SELECT 1 FROM test_claims "
                    "WHERE test_id = :tid LIMIT 1"
                ),
                {"tid": str(claim_test_id)},
            ).first()
            if claim_exists is None:
                raise OntologyViolationError(
                    f"recipe references claim_test_id={claim_test_id!r}, "
                    f"but no claim row exists for that test_id",
                    field_path="claim_test_id",
                    referent=str(claim_test_id),
                )
        else:
            pinned_exists = session.execute(
                text(
                    "SELECT 1 FROM test_claims "
                    "WHERE test_id = :tid AND version_seq = :vs LIMIT 1"
                ),
                {
                    "tid": str(claim_test_id),
                    "vs": claim_version_seq,
                },
            ).first()
            if pinned_exists is None:
                raise OntologyViolationError(
                    f"recipe pins to claim_test_id={claim_test_id!r} "
                    f"version_seq={claim_version_seq!r}, but no such "
                    f"claim version exists",
                    field_path="(claim_test_id, claim_version_seq)",
                    referent=(
                        f"{claim_test_id}@v{claim_version_seq}"
                    ),
                )

        # ----- Step 7: canonicalization --------------------------
        # N/A FOR RECIPES — recipes don't have identity_hash per
        # SPEC §6.4. Slot kept for SPEC §4.7.6 traceability.

        # ----- Step 8: hash computation --------------------------
        # N/A FOR RECIPES — see step 7.

        # ----- Step 9: coverage extraction -----------------------
        # N/A FOR RECIPES — coverage is claim-only per SPEC §4.5.

        # ----- Step 10: authority enforcement --------------------
        decision = check_recipe_write_authority(actor, has_prior_version)
        # The v1 policy always allows; the call stays for
        # symmetry with write_claim and as a forward-compat hook.
        enforce_authority(decision)

        # ----- Step 11: DB writes --------------------------------
        new_version_seq = 1 if prior is None else prior.version_seq + 1
        new_status = decision.new_status  # always "generated_unapproved"

        # event_kind selection per SPEC §4.1's
        # provenance_event_kind enum:
        if prior is None:
            event_kind = "recipe_created"
        elif actor == "s8":
            # S8 rewrites carry their own event kind for future
            # audit / S8-evolution analysis per SPEC §4.1.
            event_kind = "recipe_s8_rewrite"
        else:
            event_kind = "recipe_edited"

        # (a) Close the prior version's valid_to window.
        if prior is not None:
            session.execute(
                text(
                    "UPDATE test_recipes SET valid_to = NOW() "
                    "WHERE recipe_id = :rid AND version_seq = :vs"
                ),
                {"rid": str(recipe_id), "vs": prior.version_seq},
            )

        # (b) INSERT the new recipe version.
        new_recipe = TestRecipe(
            recipe_id=recipe_id,
            version_seq=new_version_seq,
            claim_test_id=claim_test_id,
            claim_version_seq=claim_version_seq,
            trigger_kind=trigger_kind,
            recipe_kind=recipe_kind,
            causal_initiation=causal_initiation.model_dump(mode="json"),
            observation_realization=(
                observation_realization.model_dump(mode="json")
            ),
            execution_environment=(
                execution_environment.model_dump(mode="json")
            ),
            status=new_status,
            priority=priority,
        )
        session.add(new_recipe)
        session.flush()

        # (c) Append the provenance event.
        event_data = {
            "actor": actor,
            "prior_version_seq": (
                prior.version_seq if prior is not None else None
            ),
            "new_version_seq": new_version_seq,
            "new_status": new_status,
            "claim_test_id": str(claim_test_id),
            "claim_version_seq": claim_version_seq,
        }
        session.add(TestProvenance(
            id=_uuid_mod.uuid4(),
            claim_test_id=None,
            recipe_id=recipe_id,
            event_kind=event_kind,
            event_data=event_data,
            event_actor=actor,
        ))

        session.flush()
        # Coordinator does NOT commit. Caller owns transaction
        # boundary per the D-β construction contract.

        return WriteRecipeResult(
            recipe_id=recipe_id,
            version_seq=new_version_seq,
            status=new_status,
        )

    # ------------------------------------------------------------------
    # Read interfaces (Track D-γ.1)
    #
    # Per SPEC §10.2: substrate-2's outward surface is the
    # Coordinator. Callers (S3 / S4 / S6 / S8) read through these
    # methods rather than running raw SQL — the methods guarantee
    # JSONB-to-Pydantic deserialization via the body registry, so
    # callers get typed body instances back rather than dicts.
    #
    # All methods take ``session`` as first positional argument
    # (caller-provided per the D-β construction contract).
    # ------------------------------------------------------------------

    def _deserialize_body(self, jsonb_dict: dict) -> BodyBase:
        """Deserialize a JSONB body dict to a typed Pydantic body.

        Uses the body registry to dispatch to the right model
        class for the dict's declared
        ``(kind, body_schema_version)`` pair.

        Raises:
          :class:`SchemaIncompatibilityError` — if the registry
          has no model for the declared pair. The error contract
          is consistent with the write-path's step 2 dispatch
          (D-060 §4.7.8).
        """
        kind = jsonb_dict["kind"]
        version = jsonb_dict["body_schema_version"]
        body_class = get_body_model(kind, version)
        return body_class.model_validate(jsonb_dict)

    def _hydrate_claim_row(self, row: TestClaim) -> ClaimRead:
        """Build a :class:`ClaimRead` from a SQLAlchemy
        :class:`TestClaim` row."""
        return ClaimRead(
            test_id=row.test_id,
            version_seq=row.version_seq,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            archetype=row.archetype,
            claim_kind=row.claim_kind,
            asserted_truth=self._deserialize_body(row.asserted_truth),
            semantic_conditions=self._deserialize_body(
                row.semantic_conditions,
            ),
            identity_hash=row.identity_hash,
            identity_hash_version=row.identity_hash_version,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
            strategy_kind=row.strategy_kind,   # D-285: read the column, never derive
        )

    def _hydrate_recipe_row(self, row: TestRecipe) -> RecipeRead:
        """Build a :class:`RecipeRead` from a SQLAlchemy
        :class:`TestRecipe` row."""
        return RecipeRead(
            recipe_id=row.recipe_id,
            version_seq=row.version_seq,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            claim_test_id=row.claim_test_id,
            claim_version_seq=row.claim_version_seq,
            trigger_kind=row.trigger_kind,
            recipe_kind=row.recipe_kind,
            causal_initiation=self._deserialize_body(row.causal_initiation),
            observation_realization=self._deserialize_body(
                row.observation_realization,
            ),
            execution_environment=self._deserialize_body(
                row.execution_environment,
            ),
            priority=row.priority,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    # ----- Plain claim reads -----

    def get_latest_claim(
        self,
        session: Session,
        test_id: UUID,
    ) -> Optional[ClaimRead]:
        """Return the current valid version of a claim.

        "Current" means ``valid_to IS NULL`` per D-057 effective-
        time supersession. Independent of ``status`` — a
        ``deprecated`` current version is still returned;
        consumers check ``status`` separately if they care
        (SPEC §6.6).

        Returns ``None`` if no row matches the ``test_id``.
        """
        row = (
            session.query(TestClaim)
            .filter(
                TestClaim.test_id == test_id,
                TestClaim.valid_to.is_(None),
            )
            .first()
        )
        if row is None:
            return None
        return self._hydrate_claim_row(row)

    def get_latest_claims(
        self,
        session: Session,
        test_ids: Sequence[UUID],
    ) -> dict[UUID, "ClaimRead"]:
        """Batch form of :meth:`get_latest_claim` — one query for
        many tests.

        Returns ``{test_id: ClaimRead}`` for every given test that
        has a current (``valid_to IS NULL``) version; tests with no
        row are simply absent. Same per-test semantics as the
        singular read (status-independent; first row wins should
        the one-NULL-per-test_id invariant be temporarily
        violated per D-057).
        """
        if not test_ids:
            return {}
        rows = (
            session.query(TestClaim)
            .filter(
                TestClaim.test_id.in_(list(test_ids)),
                TestClaim.valid_to.is_(None),
            )
            .all()
        )
        out: dict[UUID, ClaimRead] = {}
        for row in rows:
            if row.test_id not in out:
                out[row.test_id] = self._hydrate_claim_row(row)
        return out

    def get_claim_version(
        self,
        session: Session,
        test_id: UUID,
        version_seq: int,
    ) -> Optional[ClaimRead]:
        """Return a specific historical version of a claim, or
        ``None`` if ``(test_id, version_seq)`` doesn't exist.

        Distinct from :meth:`get_latest_claim`: this returns a
        row regardless of its ``valid_to`` state. Used for
        replay, audit, and the D-γ.2 resolution operations.
        """
        row = (
            session.query(TestClaim)
            .filter(
                TestClaim.test_id == test_id,
                TestClaim.version_seq == version_seq,
            )
            .first()
        )
        if row is None:
            return None
        return self._hydrate_claim_row(row)

    # ----- Plain recipe reads -----

    def list_active_recipes(
        self,
        session: Session,
        claim_test_id: UUID,
    ) -> list[RecipeRead]:
        """Return all currently-valid recipes for a claim.

        "Current" means ``valid_to IS NULL`` for each recipe.
        Recipes are sorted by ``recipe_id`` ascending for
        deterministic output.

        Returns an empty list if no recipes exist for the
        claim — including the case where the claim itself
        doesn't exist (no logical-FK enforcement on read).
        """
        rows = (
            session.query(TestRecipe)
            .filter(
                TestRecipe.claim_test_id == claim_test_id,
                TestRecipe.valid_to.is_(None),
            )
            .order_by(TestRecipe.recipe_id)
            .all()
        )
        return [self._hydrate_recipe_row(r) for r in rows]

    def get_recipe_latest(
        self,
        session: Session,
        recipe_id: UUID,
    ) -> Optional[RecipeRead]:
        """Return the current valid version of a recipe, or
        ``None`` if no such recipe exists."""
        row = (
            session.query(TestRecipe)
            .filter(
                TestRecipe.recipe_id == recipe_id,
                TestRecipe.valid_to.is_(None),
            )
            .first()
        )
        if row is None:
            return None
        return self._hydrate_recipe_row(row)

    def get_recipe_version(
        self,
        session: Session,
        recipe_id: UUID,
        version_seq: int,
    ) -> Optional[RecipeRead]:
        """Return a specific historical version of a recipe, or
        ``None`` if ``(recipe_id, version_seq)`` doesn't exist."""
        row = (
            session.query(TestRecipe)
            .filter(
                TestRecipe.recipe_id == recipe_id,
                TestRecipe.version_seq == version_seq,
            )
            .first()
        )
        if row is None:
            return None
        return self._hydrate_recipe_row(row)

    # ----- Equivalence + discovery -----

    def query_equivalent_claims(
        self,
        session: Session,
        *,
        identity_hash: str,
        identity_hash_version: int,
    ) -> list[ClaimRead]:
        """Return current-valid claims with the same identity_hash
        AND identity_hash_version.

        Per SPEC §6.3.9 Rule 4: cross-test semantic equivalence
        is scoped to the same canonicalization policy version.
        Both parameters are REQUIRED — passing only the hash
        would silently return cross-version matches that aren't
        actually equivalent (the canonicalization rules may
        have changed). Keyword-only to make accidental
        single-arg calls a syntax error.

        Filters out historical (closed) versions
        (``valid_to IS NULL``) — same-content historical rows
        of any test_id may exist but the current canonical
        version of each test_id is what consumers care about.

        Result order: deterministic by ``test_id`` string
        ascending.
        """
        rows = (
            session.query(TestClaim)
            .filter(
                TestClaim.identity_hash == identity_hash,
                TestClaim.identity_hash_version
                == identity_hash_version,
                TestClaim.valid_to.is_(None),
            )
            .order_by(TestClaim.test_id)
            .all()
        )
        return [self._hydrate_claim_row(r) for r in rows]

    def list_tests_affected_by_entity(
        self,
        session: Session,
        *,
        entity_id: UUID,
        entity_type: Optional[str] = None,
        reference_kind: Optional[
            Literal["subject", "condition"]
        ] = None,
    ) -> list[CoverageMatch]:
        """Return tests that reference an S1 entity in their
        identity-bearing content.

        Reads from :class:`TestClaimCoverage` (already
        deduplicated on
        ``(claim_test_id, entity_id, reference_kind)`` per the
        Track D-α PK). Drives S6 impact attribution and S8
        evolution detection.

        Filters:
          entity_type: restricts to a single entity type when
            the same ``entity_id`` could conceivably collide
            across types.
          reference_kind: restricts to ``"subject"`` or
            ``"condition"`` references only.

        Result order: deterministic by
        ``(test_id, reference_kind)`` ascending.
        """
        query = session.query(TestClaimCoverage).filter(
            TestClaimCoverage.entity_id == entity_id,
        )
        if entity_type is not None:
            query = query.filter(
                TestClaimCoverage.entity_type == entity_type,
            )
        if reference_kind is not None:
            query = query.filter(
                TestClaimCoverage.reference_kind == reference_kind,
            )
        rows = query.order_by(
            TestClaimCoverage.claim_test_id,
            TestClaimCoverage.reference_kind,
        ).all()
        return [
            CoverageMatch(
                test_id=row.claim_test_id,
                reference_kind=row.reference_kind,
            )
            for row in rows
        ]

    def list_tests_by_requirement(
        self,
        session: Session,
        *,
        external_system: str,
        external_key: str,
        link_kind: Optional[
            Union[
                Literal["generated_from", "verifies", "related_to"],
                Sequence[str],
            ]
        ] = None,
    ) -> list[RequirementMatch]:
        """Return tests linked to an external requirement.

        Reads from :class:`TestRequirementLink`. Filter:

          link_kind: restricts to a specific link kind, or to any of a
            sequence of kinds (e.g. :data:`COVERAGE_LINK_KINDS`).
            ``None`` returns all link kinds.

        A test linked under more than one matching kind returns one
        match per link row — callers that want distinct tests dedupe
        on ``test_id`` (order puts ``generated_from`` first).

        Result order: deterministic by
        ``(test_id, link_kind)`` ascending.
        """
        query = session.query(TestRequirementLink).filter(
            TestRequirementLink.external_system == external_system,
            TestRequirementLink.external_key == external_key,
        )
        if link_kind is not None:
            kinds = (
                (link_kind,) if isinstance(link_kind, str)
                else tuple(link_kind)
            )
            query = query.filter(
                TestRequirementLink.link_kind.in_(kinds),
            )
        rows = query.order_by(
            TestRequirementLink.test_id,
            TestRequirementLink.link_kind,
        ).all()
        return [
            RequirementMatch(
                test_id=row.test_id,
                link_kind=row.link_kind,
                external_version=row.external_version,
                linked_at=row.linked_at,
            )
            for row in rows
        ]

    def list_tests_by_requirements(
        self,
        session: Session,
        *,
        external_system: str,
        external_keys: Sequence[str],
        link_kind: Optional[
            Union[
                Literal["generated_from", "verifies", "related_to"],
                Sequence[str],
            ]
        ] = None,
    ) -> dict[str, list[RequirementMatch]]:
        """Batch form of :meth:`list_tests_by_requirement` — one
        query for many external keys.

        Returns ``{external_key: [RequirementMatch, ...]}`` with
        each key's list ordered exactly as the singular read
        (``(test_id, link_kind)`` ascending). Keys with no links
        are absent from the dict.
        """
        if not external_keys:
            return {}
        query = session.query(TestRequirementLink).filter(
            TestRequirementLink.external_system == external_system,
            TestRequirementLink.external_key.in_(list(external_keys)),
        )
        if link_kind is not None:
            kinds = (
                (link_kind,) if isinstance(link_kind, str)
                else tuple(link_kind)
            )
            query = query.filter(
                TestRequirementLink.link_kind.in_(kinds),
            )
        rows = query.order_by(
            TestRequirementLink.external_key,
            TestRequirementLink.test_id,
            TestRequirementLink.link_kind,
        ).all()
        out: dict[str, list[RequirementMatch]] = {}
        for row in rows:
            out.setdefault(row.external_key, []).append(
                RequirementMatch(
                    test_id=row.test_id,
                    link_kind=row.link_kind,
                    external_version=row.external_version,
                    linked_at=row.linked_at,
                )
            )
        return out

    # ------------------------------------------------------------------
    # Resolution-class operations (Track D-γ.2)
    #
    # Per SPEC §10.4: resolution operations COMPOSE substrate
    # rules (version ordering, approval state, environment
    # satisfiability, runtime outcomes) into single answers
    # that callers commonly need. The substrate owns the
    # composition policy so it stays consistent across S3 / S4
    # / S6 / S8.
    # ------------------------------------------------------------------

    def get_current_approved_claim(
        self,
        session: Session,
        test_id: UUID,
    ) -> Optional[ClaimRead]:
        """Governance resolution per SPEC §7.5 + §10.4.

        Composes version ordering with approval-state
        filtering: returns the highest-``version_seq`` row whose
        ``status='approved'``, or ``None`` if no approved version
        exists in the test's history.

        Distinct from :meth:`get_latest_claim`:

          - :meth:`get_latest_claim` returns the current
            ``valid_to IS NULL`` row regardless of status. After a
            hash-changing edit per D-059 §6.3.9 Rule 2, the
            current valid row is ``draft``; the prior approved
            row sits at a lower ``version_seq`` with ``valid_to``
            closed.
          - This method walks back through history to find the
            last approved row, regardless of supersession state.

        Per SPEC §6.6: deprecation is orthogonal to supersession.
        A ``deprecated`` current valid row is skipped here because
        it's not ``approved``; the resolution continues walking
        back to find an earlier approved row if one exists.
        """
        row = (
            session.query(TestClaim)
            .filter(
                TestClaim.test_id == test_id,
                TestClaim.status == "approved",
            )
            .order_by(TestClaim.version_seq.desc())
            .first()
        )
        if row is None:
            return None
        return self._hydrate_claim_row(row)

    def get_current_approved_claims(
        self,
        session: Session,
        test_ids: Sequence[UUID],
    ) -> dict[UUID, "ClaimRead"]:
        """Batch form of :meth:`get_current_approved_claim` — one
        query for many tests.

        Returns ``{test_id: ClaimRead}`` mapping each given test to
        its highest-``version_seq`` ``approved`` row; tests with no
        approved version in their history are absent. Postgres
        ``DISTINCT ON`` keyed by ``test_id``, newest approved
        version first — the exact singular resolution, set-based.
        """
        if not test_ids:
            return {}
        rows = (
            session.query(TestClaim)
            .filter(
                TestClaim.test_id.in_(list(test_ids)),
                TestClaim.status == "approved",
            )
            .distinct(TestClaim.test_id)
            .order_by(TestClaim.test_id, TestClaim.version_seq.desc())
            .all()
        )
        return {row.test_id: self._hydrate_claim_row(row) for row in rows}

    def get_test_runtime_status(
        self,
        session: Session,
        test_id: UUID,
    ) -> Literal["passing", "failing", "untested", "mixed"]:
        """Policy resolution per SPEC §8.4 + §10.4.

        Composes:
          - current-approved claim resolution
          - active-recipe filtering
          - per-recipe runtime state aggregation
          - SPEC §8.4 conservative outcome composition

        Returns one of four states:

          - ``"untested"`` — no approved claim, OR no eligible
            recipes, OR no eligible recipes have a runtime state
            row, OR all runtime states are ``"skipped"``.
          - ``"failing"`` — any eligible recipe's runtime state
            shows ``"failed"`` or ``"errored"``.
          - ``"passing"`` — every eligible recipe has a runtime
            state row, every outcome is ``"passed"`` or
            ``"skipped"``, AND at least one is ``"passed"``.
          - ``"mixed"`` — any other combination (typically: some
            recipes have runtime state, others don't, with no
            failures observed).

        Eligibility per D-γ.2's "no autonomous execution"
        commitment: a recipe is eligible iff
        ``status IN ('active', 'approved')`` AND
        ``valid_to IS NULL``. ``generated_unapproved`` and
        ``deprecated`` recipes are EXCLUDED from runtime-state
        resolution.

        Per SPEC §8.2: runtime state is keyed by ``recipe_id``
        only (not ``recipe_id + version_seq``). A new recipe
        version inherits its predecessor's runtime-state row;
        the row's ``last_run_recipe_version_seq`` column
        records which version was actually run (consumers
        detect "runtime state is stale" via that column).

        Per SPEC §8.5: the conservative composition policy has
        acknowledged pressure that v1 cannot fully address.
        SPEC §8.6 reserves richer resolution
        (recipe-priority weighting, primary-recipe designation,
        etc.) for future evolution.
        """
        approved = self.get_current_approved_claim(session, test_id)
        if approved is None:
            return "untested"

        eligible = (
            session.query(TestRecipe)
            .filter(
                TestRecipe.claim_test_id == test_id,
                TestRecipe.valid_to.is_(None),
                TestRecipe.status.in_(["active", "approved"]),
            )
            .all()
        )
        if not eligible:
            return "untested"

        # Look up runtime-state row per eligible recipe_id. The
        # PK is recipe_id alone; one row per recipe regardless
        # of version_seq history.
        recipe_ids = [r.recipe_id for r in eligible]
        states = (
            session.query(TestRecipeRuntimeState)
            .filter(
                TestRecipeRuntimeState.recipe_id.in_(recipe_ids),
            )
            .all()
        )
        outcomes_by_id = {
            s.recipe_id: s.last_run_outcome for s in states
        }

        # Composition rule.
        has_failure = any(
            outcomes_by_id.get(rid) in ("failed", "errored")
            for rid in recipe_ids
        )
        if has_failure:
            return "failing"

        outcomes = [outcomes_by_id.get(rid) for rid in recipe_ids]
        # Tally the categories.
        none_count = sum(1 for o in outcomes if o is None)
        passed_count = sum(1 for o in outcomes if o == "passed")
        skipped_count = sum(1 for o in outcomes if o == "skipped")

        # All recipes have runtime state, all are passed or
        # skipped, at least one passed → passing.
        if (
            none_count == 0
            and passed_count >= 1
            and (passed_count + skipped_count) == len(outcomes)
        ):
            return "passing"

        # No recipe has any runtime state row, OR all rows are
        # skipped → untested. "All skipped" is treated as "no
        # recipe actually exercised" per the SPEC §8.4
        # conservative policy.
        if none_count == len(outcomes):
            return "untested"
        if passed_count == 0 and none_count == 0:
            # All recipes have state, none passed, none failed
            # (since has_failure was False) → all are skipped.
            return "untested"

        return "mixed"

    def _matching_recipes_for_execution(
        self,
        session: Session,
        test_id: UUID,
        *,
        available_environment: ExecutionEnvironmentBody,
        replay_mode: Literal["live"] = "live",
    ) -> list[TestRecipe]:
        """Shared resolution behind :meth:`select_recipe_for_execution` and
        :meth:`select_recipes_for_execution` (D-275, S6 Slice 3.1) — the single
        source of truth for run-path recipe resolution.

        Composes (per SPEC §10.4):
          - current-approved claim resolution
          - eligible-recipe filtering (D-γ.2's "no autonomous execution"
            commitment: ``status IN ('active', 'approved')`` AND
            ``valid_to IS NULL``)
          - environment satisfiability matching
            (see :meth:`_environment_satisfies`)
          - deterministic ordering (priority DESC, version_seq DESC, recipe_id ASC)

        Returns the **full sorted set** of matching (un-hydrated) ``TestRecipe``
        rows — ``[]`` when any composition step yields no match. Callers hydrate
        what they need (the singular takes ``[0]``; the plural takes all).

        ``replay_mode='live'`` is the only supported value at v1; SPEC §6.8
        reserves historical and semantic replay modes for future evolution. Other
        values raise :class:`NotImplementedError` with a SPEC reference.
        """
        if replay_mode != "live":
            raise NotImplementedError(
                f"replay_mode={replay_mode!r} is reserved per "
                f"SPEC §6.8; only 'live' is supported at v1"
            )

        approved = self.get_current_approved_claim(session, test_id)
        if approved is None:
            return []

        eligible = (
            session.query(TestRecipe)
            .filter(
                TestRecipe.claim_test_id == test_id,
                TestRecipe.valid_to.is_(None),
                TestRecipe.status.in_(["active", "approved"]),
            )
            .all()
        )

        # Filter by environment satisfiability.
        matching = []
        for row in eligible:
            recipe_env = self._deserialize_body(row.execution_environment)
            if self._environment_satisfies(
                recipe_env=recipe_env,
                available_env=available_environment,
            ):
                matching.append(row)

        # Sort: priority DESC, version_seq DESC, recipe_id ASC.
        matching.sort(key=lambda r: (
            -r.priority,
            -r.version_seq,
            str(r.recipe_id),
        ))
        return matching

    def select_recipe_for_execution(
        self,
        session: Session,
        test_id: UUID,
        *,
        available_environment: ExecutionEnvironmentBody,
        replay_mode: Literal["live"] = "live",
    ) -> Optional[RecipeRead]:
        """Policy resolution per SPEC §10.4 — the canonical example of
        resolution-class operations. Returns the **top** eligible +
        environment-satisfiable recipe (priority DESC, version_seq DESC,
        recipe_id ASC), or ``None`` if any composition step yields no match.

        The N=1 head of :meth:`_matching_recipes_for_execution`; byte-identical to
        the pre-D-275 inline resolution (the run path's single-recipe selection).

        ``replay_mode='live'`` is the only supported value at v1; SPEC §6.8
        reserves historical and semantic replay modes for future evolution. Other
        values raise :class:`NotImplementedError` with a SPEC reference.
        """
        matching = self._matching_recipes_for_execution(
            session, test_id,
            available_environment=available_environment,
            replay_mode=replay_mode,
        )
        return self._hydrate_recipe_row(matching[0]) if matching else None

    def select_recipes_for_execution(
        self,
        session: Session,
        test_id: UUID,
        *,
        available_environment: ExecutionEnvironmentBody,
        replay_mode: Literal["live"] = "live",
    ) -> list[RecipeRead]:
        """The FULL env-satisfiable applicable-recipe set for ``test_id`` — every
        eligible + environment-satisfiable recipe, in the same deterministic order
        as :meth:`select_recipe_for_execution` (priority DESC, version_seq DESC,
        recipe_id ASC). Returns ``[]`` when none match (never raises on empty).

        D-275, S6 Slice 3.1 — the run-all keystone's applicable-probe read. **Pure
        read; wired to nothing in this slice** (Slice 3.3's run-all loop iterates
        this set, running every probe of a ``bva`` claim). For a single-recipe
        claim, ``select_recipes_for_execution(...)[0]`` equals
        ``select_recipe_for_execution(...)``.
        """
        matching = self._matching_recipes_for_execution(
            session, test_id,
            available_environment=available_environment,
            replay_mode=replay_mode,
        )
        return [self._hydrate_recipe_row(row) for row in matching]

    def current_data_recipe_ids(
        self,
        session: Session,
        test_id: UUID,
    ) -> list[UUID]:
        """The claim's AUTHORED data-probe membership — every CURRENT
        (``valid_to IS NULL``) data-recipe id, **status-blind** and env-blind
        (D-300 review-fix B2). The run-all batch manifest records THIS set, not
        the approval/env-filtered selection: an unapproved or deprecated member
        stays expected, never runs, and the completeness reader marks the batch
        INCOMPLETE → not-Verified — so partial approval or per-recipe
        deprecation can only ever produce an honest red, never a boundary
        verdict folded over a reduced probe set (the wrong-green vector).
        Deterministic order (recipe_id ASC)."""
        rows = (
            session.query(TestRecipe.recipe_id)
            .filter(
                TestRecipe.claim_test_id == test_id,
                TestRecipe.valid_to.is_(None),
                TestRecipe.recipe_kind == "data-recipe",
            )
            .order_by(TestRecipe.recipe_id)
            .all()
        )
        return [r.recipe_id for r in rows]

    def _environment_satisfies(
        self,
        *,
        recipe_env: ExecutionEnvironmentBody,
        available_env: ExecutionEnvironmentBody,
    ) -> bool:
        """Membership-based environment matching for v1.

        Returns ``True`` iff every assumption the recipe makes
        is honored by the available environment:

          1. ``org_kind`` / ``salesforce_edition`` — when set on
             recipe, must equal available's value. ``None`` on
             recipe means "any value acceptable."
          2. ``feature_assumptions`` — for each recipe entry:
             - ``required=True`` ⇒ available MUST contain a
               feature with matching ``feature_name``.
             - ``required=False`` ⇒ available MUST NOT contain a
               feature with matching ``feature_name`` (negative
               assumption — recipe assumes absence).
          3. ``auth_assumptions`` — every recipe ``auth_kind``
             MUST appear in available's auth_kinds.
          4. ``infrastructure_assumptions`` — every recipe
             ``infra_kind`` MUST appear in available's
             infra_kinds.

        Matching is by name / kind only. ``description`` /
        ``details`` are advisory at v1. Future evolution per
        SPEC §3 reservations may add capability-level matching.
        """
        # (1) org_kind
        if recipe_env.org_kind is not None:
            if recipe_env.org_kind != available_env.org_kind:
                return False

        # (1) salesforce_edition
        if recipe_env.salesforce_edition is not None:
            if (
                recipe_env.salesforce_edition
                != available_env.salesforce_edition
            ):
                return False

        # (2) feature_assumptions
        available_feature_names = {
            f.feature_name for f in available_env.feature_assumptions
        }
        for fa in recipe_env.feature_assumptions:
            present = fa.feature_name in available_feature_names
            if fa.required and not present:
                return False
            if (not fa.required) and present:
                # Negative assumption violated — recipe expected
                # the feature ABSENT, but it's present.
                return False

        # (3) auth_assumptions
        available_auth_kinds = {
            a.auth_kind for a in available_env.auth_assumptions
        }
        for aa in recipe_env.auth_assumptions:
            if aa.auth_kind not in available_auth_kinds:
                return False

        # (4) infrastructure_assumptions
        available_infra_kinds = {
            i.infra_kind for i in available_env.infrastructure_assumptions
        }
        for ia in recipe_env.infrastructure_assumptions:
            if ia.infra_kind not in available_infra_kinds:
                return False

        return True

    # ------------------------------------------------------------------
    # Boundary interfaces (Track D-δ)
    #
    # Per SPEC §8 + §9: these methods are the substrate's
    # external boundary surface — S4 reports run outcomes here
    # (§8.3), and the requirement-link methods exchange
    # external-system references with the rest of the platform
    # (§9). No provenance event is emitted from these methods
    # per the D-δ-9 decision; the boundary-table rows ARE the
    # audit record.
    # ------------------------------------------------------------------

    def report_run_outcome(
        self,
        session: Session,
        *,
        actor: ActorKind,
        recipe_id: UUID,
        last_run_id: UUID,
        last_run_at: datetime,
        last_run_outcome: Literal[
            "passed", "failed", "errored", "skipped",
        ],
        last_run_recipe_version_seq: int,
    ) -> RecipeRuntimeState:
        """S4 boundary callback per SPEC §8.3.

        Authority: ``actor='s4'`` only;
        :class:`AuthorityViolationError` otherwise.

        Idempotency on ``last_run_id``: same ``run_id`` reported
        twice returns the existing state unchanged
        (**first-write-wins**). A late correction with the same
        ``run_id`` but a different ``outcome`` does NOT
        overwrite — by design, the boundary is append-only on
        new run_ids. If a corrected outcome is needed, S4 must
        report it under a fresh ``run_id``.

        Upsert semantics: one row per ``recipe_id`` per SPEC
        §8.2. The accumulation rule:

          - ``last_pass_at`` ← ``last_run_at`` when
            ``outcome='passed'``; preserved otherwise.
          - ``last_failure_at`` ← ``last_run_at`` when
            ``outcome IN ('failed', 'errored')``; preserved
            otherwise.
          - ``last_run_id``, ``last_run_at``,
            ``last_run_outcome``,
            ``last_run_recipe_version_seq``: replaced with the
            new report's values.

        No provenance event emitted (per D-δ-9): runtime-state
        rows ARE their own audit trail via the snapshot
        accumulation columns.
        """
        check_runtime_state_write_authority(actor)

        existing = (
            session.query(TestRecipeRuntimeState)
            .filter(TestRecipeRuntimeState.recipe_id == recipe_id)
            .first()
        )

        # Idempotency: same run_id → no-op return.
        if existing is not None and existing.last_run_id == last_run_id:
            return self._hydrate_runtime_state_row(existing)

        # Accumulate pass/failure timestamps.
        if last_run_outcome == "passed":
            new_last_pass = last_run_at
        else:
            new_last_pass = (
                existing.last_pass_at if existing is not None else None
            )
        if last_run_outcome in ("failed", "errored"):
            new_last_failure = last_run_at
        else:
            new_last_failure = (
                existing.last_failure_at if existing is not None else None
            )

        if existing is None:
            session.add(TestRecipeRuntimeState(
                recipe_id=recipe_id,
                last_run_id=last_run_id,
                last_run_at=last_run_at,
                last_run_outcome=last_run_outcome,
                last_run_recipe_version_seq=last_run_recipe_version_seq,
                last_pass_at=new_last_pass,
                last_failure_at=new_last_failure,
            ))
            session.flush()
            return self.get_recipe_runtime_state(session, recipe_id)
        # Update existing.
        existing.last_run_id = last_run_id
        existing.last_run_at = last_run_at
        existing.last_run_outcome = last_run_outcome
        existing.last_run_recipe_version_seq = (
            last_run_recipe_version_seq
        )
        existing.last_pass_at = new_last_pass
        existing.last_failure_at = new_last_failure
        # updated_at has a server_default; let the column-level
        # default fire via NOW() expression on the next write,
        # or update it explicitly here so the column reflects
        # this write rather than the original INSERT time.
        session.execute(
            text(
                "UPDATE test_recipe_runtime_state SET updated_at = NOW() "
                "WHERE recipe_id = :r"
            ),
            {"r": str(recipe_id)},
        )
        session.flush()
        # Re-fetch so we capture the DB-side updated_at + the
        # ORM-side column updates in one consistent snapshot.
        session.refresh(existing)
        return self._hydrate_runtime_state_row(existing)

    def get_recipe_runtime_state(
        self,
        session: Session,
        recipe_id: UUID,
    ) -> Optional[RecipeRuntimeState]:
        """Raw runtime-state read per SPEC §8.

        Returns ``None`` if the recipe has never had a run
        reported. Use :meth:`get_test_runtime_status` for the
        composed test-level resolution; this method exposes
        the raw snapshot for consumers that need it (S6
        attribution, future flakiness detection per SPEC §8.6
        reservation).
        """
        row = (
            session.query(TestRecipeRuntimeState)
            .filter(TestRecipeRuntimeState.recipe_id == recipe_id)
            .first()
        )
        if row is None:
            return None
        return self._hydrate_runtime_state_row(row)

    def _hydrate_runtime_state_row(
        self, row: TestRecipeRuntimeState,
    ) -> RecipeRuntimeState:
        """Build a :class:`RecipeRuntimeState` from a SQLAlchemy
        :class:`TestRecipeRuntimeState` row."""
        return RecipeRuntimeState(
            recipe_id=row.recipe_id,
            last_run_id=row.last_run_id,
            last_run_at=row.last_run_at,
            last_run_outcome=row.last_run_outcome,
            last_run_recipe_version_seq=row.last_run_recipe_version_seq,
            last_pass_at=row.last_pass_at,
            last_failure_at=row.last_failure_at,
            updated_at=row.updated_at,
        )

    def append_claim_event(self, session, *, actor: str, test_id,
                           event_kind: str, event_data: dict) -> None:
        """Append ONE provenance event to a claim (D-454: the
        ``coverage_flag`` writer). Append-only, same-transaction with the
        caller's write; never touches claim rows. The event kind must be a
        member of the ``provenance_event_kind`` enum (the D-454 tenant
        migration added ``coverage_flag``)."""
        session.add(TestProvenance(
            id=_uuid_mod.uuid4(),
            claim_test_id=test_id,
            recipe_id=None,
            event_kind=event_kind,
            event_data=dict(event_data),
            event_actor=actor,
        ))

    def link_requirement(
        self,
        session: Session,
        *,
        actor: ActorKind,
        test_id: UUID,
        external_system: str,
        external_key: str,
        link_kind: Literal[
            "generated_from", "verifies", "related_to",
        ],
        external_version: Optional[str] = None,
    ) -> RequirementLinkResult:
        """Establish or update a requirement link per SPEC §9.

        Authority: any actor EXCEPT ``s4``. Typically S3 creates
        ``generated_from`` links during generation; humans
        create ``verifies`` / ``related_to`` during curation.

        Idempotency on the PK
        ``(test_id, external_system, external_key, link_kind)``:

          - **First call** → INSERT, ``was_noop=False``.
          - **Subsequent call**, same PK, SAME
            ``external_version`` → no-op, ``was_noop=True``,
            existing row returned.
          - **Subsequent call**, same PK, DIFFERENT
            ``external_version`` → UPDATE ``external_version``
            only; ``linked_by`` and ``linked_at`` preserved
            (original authorship is authoritative).
            ``was_noop=False``.

        Validation:
          - ``actor='s4'`` →
            :class:`AuthorityViolationError`.
          - ``external_system`` must be a registered enum value
            (only ``'jira'`` at v1) — ``ValueError`` otherwise.
          - ``test_id`` must reference an existing test (any
            version_seq) — :class:`OntologyViolationError`
            otherwise.

        No provenance event emitted (per D-δ-9): linkage state
        is its own audit record via the row's ``linked_by`` and
        ``linked_at`` columns.
        """
        # Authority: s4 is runtime-state-only.
        if actor == "s4":
            raise AuthorityViolationError(
                f"link_requirement is a substrate-2/external boundary "
                f"per SPEC §9; actor='s4' is the runtime-state-only "
                f"actor and not authorized. Got actor={actor!r}.",
            )
        # external_system validation.
        if external_system not in _VALID_EXTERNAL_SYSTEMS:
            raise ValueError(
                f"unknown external_system: {external_system!r}; "
                f"expected one of {sorted(_VALID_EXTERNAL_SYSTEMS)}"
            )
        # test_id existence (any version).
        exists = session.execute(
            text(
                "SELECT 1 FROM test_claims WHERE test_id = :t LIMIT 1"
            ),
            {"t": str(test_id)},
        ).first()
        if exists is None:
            raise OntologyViolationError(
                f"link_requirement references test_id={test_id!r} "
                f"but no claim row exists for that test_id",
                field_path="test_id",
                referent=str(test_id),
            )

        existing = (
            session.query(TestRequirementLink)
            .filter(
                TestRequirementLink.test_id == test_id,
                TestRequirementLink.external_system == external_system,
                TestRequirementLink.external_key == external_key,
                TestRequirementLink.link_kind == link_kind,
            )
            .first()
        )

        if existing is None:
            # First link → INSERT.
            row = TestRequirementLink(
                test_id=test_id,
                external_system=external_system,
                external_key=external_key,
                external_version=external_version,
                link_kind=link_kind,
                linked_by=actor,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._hydrate_requirement_link_row(
                row, was_noop=False,
            )

        # Existing row — distinguish no-op from version-update.
        if existing.external_version == external_version:
            return self._hydrate_requirement_link_row(
                existing, was_noop=True,
            )
        # external_version changed — UPDATE only that column;
        # linked_by and linked_at are preserved to capture
        # original authorship per the D-δ contract.
        existing.external_version = external_version
        session.flush()
        return self._hydrate_requirement_link_row(
            existing, was_noop=False,
        )

    def unlink_requirement(
        self,
        session: Session,
        *,
        actor: ActorKind,
        test_id: UUID,
        external_system: str,
        external_key: str,
        link_kind: Literal[
            "generated_from", "verifies", "related_to",
        ],
    ) -> Optional[RequirementLinkResult]:
        """Remove a requirement link.

        Authority: same as :meth:`link_requirement` (any actor
        except ``s4``).

        Idempotent on missing rows: returns ``None`` when no
        matching link exists (the desired state — absent — is
        already achieved). Returns the
        :class:`RequirementLinkResult` snapshot of the deleted
        row when a link was actually removed.

        No provenance event emitted (per D-δ-9).
        """
        if actor == "s4":
            raise AuthorityViolationError(
                f"unlink_requirement is a substrate-2/external "
                f"boundary per SPEC §9; actor='s4' is not "
                f"authorized. Got actor={actor!r}.",
            )

        existing = (
            session.query(TestRequirementLink)
            .filter(
                TestRequirementLink.test_id == test_id,
                TestRequirementLink.external_system == external_system,
                TestRequirementLink.external_key == external_key,
                TestRequirementLink.link_kind == link_kind,
            )
            .first()
        )
        if existing is None:
            return None
        snapshot = self._hydrate_requirement_link_row(
            existing, was_noop=False,
        )
        session.delete(existing)
        session.flush()
        return snapshot

    def _hydrate_requirement_link_row(
        self,
        row: TestRequirementLink,
        *,
        was_noop: bool,
    ) -> RequirementLinkResult:
        """Build a :class:`RequirementLinkResult` from a
        SQLAlchemy :class:`TestRequirementLink` row."""
        return RequirementLinkResult(
            test_id=row.test_id,
            external_system=row.external_system,
            external_key=row.external_key,
            link_kind=row.link_kind,
            external_version=row.external_version,
            linked_at=row.linked_at,
            linked_by=row.linked_by,
            was_noop=was_noop,
        )

    # ------------------------------------------------------------------
    # Status mutation + priority change (Track D-ε)
    #
    # In-place mutation operations per SPEC §6.6 + §7 + §10.2
    # group 1. These methods update the target row's ``status``
    # (or ``priority``) WITHOUT bumping ``version_seq`` — they
    # represent lifecycle and operational-metadata transitions,
    # not new semantic versions.
    #
    # Authority model:
    #   - promote/deprecate (status mutation) — HUMANS ONLY per
    #     D-ε-1's conservative default. Future evolution may
    #     extend autonomous approval / deprecation, but at v1
    #     the substrate insists on human authorship for
    #     governance signals.
    #   - change_recipe_priority — any actor EXCEPT ``s4``.
    #     Priority is operational metadata, not behavior-
    #     affecting, so the substrate accepts S3 / S8 priority
    #     adjustments alongside humans.
    #
    # Each method emits a provenance event from the §4.1
    # provenance_event_kind enum (claim_approved /
    # claim_deprecated / recipe_approved / recipe_deprecated /
    # recipe_priority_changed). No-op paths (already in target
    # state) emit no event and leave ``updated_at`` unchanged
    # — same precedent as :meth:`report_run_outcome`.
    # ------------------------------------------------------------------

    def promote_claim_to_approved(
        self,
        session: Session,
        *,
        actor: ActorKind,
        test_id: UUID,
        version_seq: int,
        event_context: Optional[dict] = None,
    ) -> ClaimRead:
        """Promote a specific claim version to ``status='approved'``.

        ``event_context`` (3A-3): optional attribution folded into the
        provenance ``event_data`` — e.g. ``{"user_id": 7,
        "claim_set_id": "…"}``. ``event_actor`` stays the D-ε-1 actor
        kind; the REAL attribution rides event_data. Default ``None``
        keeps every existing caller's event byte-identical.

        Per D-ε-1: humans only. The substrate's v1 conservative
        default refuses autonomous approval — S3's generations and
        S8's evolution proposals must surface through review
        rather than auto-approving.

        Status transitions:
          - ``draft`` → ``approved``
          - ``deprecated`` → ``approved`` (un-deprecation; the
            audit trail in :class:`TestProvenance` retains the
            full history)
          - ``approved`` → ``approved``: no-op (no provenance
            event; ``updated_at`` unchanged)

        Targets a specific ``(test_id, version_seq)`` rather than
        "the latest" to prevent races where a new version writes
        between caller intent and operation execution. Callers
        explicitly say which version they intend to approve.
        """
        if actor != "human":
            raise AuthorityViolationError(
                f"promote_claim_to_approved is humans-only per "
                f"D-ε-1 conservative default; got actor={actor!r}",
            )
        row = (
            session.query(TestClaim)
            .filter(
                TestClaim.test_id == test_id,
                TestClaim.version_seq == version_seq,
            )
            .first()
        )
        if row is None:
            raise ValueError(
                f"no claim row for (test_id={test_id!r}, "
                f"version_seq={version_seq!r})"
            )
        if row.status == "approved":
            return self._hydrate_claim_row(row)

        prior_status = row.status
        session.execute(
            text(
                "UPDATE test_claims "
                "SET status = 'approved', updated_at = NOW() "
                "WHERE test_id = :t AND version_seq = :v"
            ),
            {"t": str(test_id), "v": version_seq},
        )
        session.add(TestProvenance(
            id=_uuid_mod.uuid4(),
            claim_test_id=test_id,
            recipe_id=None,
            event_kind="claim_approved",
            event_data={
                "actor": actor,
                "version_seq": version_seq,
                "prior_status": prior_status,
                "new_status": "approved",
                **(event_context or {}),
            },
            event_actor=actor,
        ))
        session.flush()
        session.refresh(row)
        return self._hydrate_claim_row(row)

    def promote_recipe_to_approved(
        self,
        session: Session,
        *,
        actor: ActorKind,
        recipe_id: UUID,
        version_seq: int,
        event_context: Optional[dict] = None,
    ) -> RecipeRead:
        """Promote a specific recipe version to
        ``status='approved'``.

        Parallel of :meth:`promote_claim_to_approved` for
        recipes. Same humans-only authority per D-ε-1, same
        in-place mutation semantics, same no-op behavior when
        already approved. Same optional ``event_context``
        attribution (3A-3), default ``None`` byte-identical.

        Status transitions:
          - ``generated_unapproved`` → ``approved``
          - ``active`` → ``approved``
          - ``deprecated`` → ``approved`` (un-deprecation)
          - ``approved`` → ``approved``: no-op

        Provenance event: ``recipe_approved``.
        """
        if actor != "human":
            raise AuthorityViolationError(
                f"promote_recipe_to_approved is humans-only per "
                f"D-ε-1 conservative default; got actor={actor!r}",
            )
        row = (
            session.query(TestRecipe)
            .filter(
                TestRecipe.recipe_id == recipe_id,
                TestRecipe.version_seq == version_seq,
            )
            .first()
        )
        if row is None:
            raise ValueError(
                f"no recipe row for (recipe_id={recipe_id!r}, "
                f"version_seq={version_seq!r})"
            )
        if row.status == "approved":
            return self._hydrate_recipe_row(row)

        prior_status = row.status
        session.execute(
            text(
                "UPDATE test_recipes "
                "SET status = 'approved', updated_at = NOW() "
                "WHERE recipe_id = :r AND version_seq = :v"
            ),
            {"r": str(recipe_id), "v": version_seq},
        )
        session.add(TestProvenance(
            id=_uuid_mod.uuid4(),
            claim_test_id=None,
            recipe_id=recipe_id,
            event_kind="recipe_approved",
            event_data={
                "actor": actor,
                "version_seq": version_seq,
                "prior_status": prior_status,
                "new_status": "approved",
                **(event_context or {}),
            },
            event_actor=actor,
        ))
        session.flush()
        session.refresh(row)
        return self._hydrate_recipe_row(row)

    def deprecate_claim(
        self,
        session: Session,
        *,
        actor: ActorKind,
        test_id: UUID,
        version_seq: int,
        reason: str,
    ) -> ClaimRead:
        """Deprecate a specific claim version.

        Per D-ε-1: humans only. S8's evolution detection cannot
        autonomously deprecate at v1 — it must surface findings
        through other mechanisms; deprecation is a governance
        signal requiring explicit human authority.

        ``reason`` is REQUIRED (non-empty) per D-ε-5:
        deprecation is forward-explanatory; the reason is
        captured in :attr:`TestProvenance.event_data`, NOT as a
        column on :class:`TestClaim`. Future audit tooling
        reads provenance to surface deprecation rationale.

        Status transitions:
          - ``draft`` → ``deprecated``
          - ``approved`` → ``deprecated``
          - ``deprecated`` → ``deprecated``: no-op (no provenance
            event; ``updated_at`` unchanged)

        Provenance event: ``claim_deprecated``.
        """
        if actor != "human":
            raise AuthorityViolationError(
                f"deprecate_claim is humans-only per D-ε-1 "
                f"conservative default; got actor={actor!r}",
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                "deprecate_claim requires a non-empty 'reason' "
                "per D-ε-5: deprecation is forward-explanatory."
            )
        row = (
            session.query(TestClaim)
            .filter(
                TestClaim.test_id == test_id,
                TestClaim.version_seq == version_seq,
            )
            .first()
        )
        if row is None:
            raise ValueError(
                f"no claim row for (test_id={test_id!r}, "
                f"version_seq={version_seq!r})"
            )
        if row.status == "deprecated":
            return self._hydrate_claim_row(row)

        prior_status = row.status
        session.execute(
            text(
                "UPDATE test_claims "
                "SET status = 'deprecated', updated_at = NOW() "
                "WHERE test_id = :t AND version_seq = :v"
            ),
            {"t": str(test_id), "v": version_seq},
        )
        session.add(TestProvenance(
            id=_uuid_mod.uuid4(),
            claim_test_id=test_id,
            recipe_id=None,
            event_kind="claim_deprecated",
            event_data={
                "actor": actor,
                "version_seq": version_seq,
                "prior_status": prior_status,
                "new_status": "deprecated",
                "reason": reason,
            },
            event_actor=actor,
        ))
        session.flush()
        session.refresh(row)
        return self._hydrate_claim_row(row)

    def deprecate_recipe(
        self,
        session: Session,
        *,
        actor: ActorKind,
        recipe_id: UUID,
        version_seq: int,
        reason: str,
    ) -> RecipeRead:
        """Deprecate a specific recipe version.

        Parallel of :meth:`deprecate_claim` for recipes. Same
        humans-only authority, same required ``reason``, same
        no-op semantics. Provenance event:
        ``recipe_deprecated``.
        """
        if actor != "human":
            raise AuthorityViolationError(
                f"deprecate_recipe is humans-only per D-ε-1 "
                f"conservative default; got actor={actor!r}",
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                "deprecate_recipe requires a non-empty 'reason' "
                "per D-ε-5."
            )
        row = (
            session.query(TestRecipe)
            .filter(
                TestRecipe.recipe_id == recipe_id,
                TestRecipe.version_seq == version_seq,
            )
            .first()
        )
        if row is None:
            raise ValueError(
                f"no recipe row for (recipe_id={recipe_id!r}, "
                f"version_seq={version_seq!r})"
            )
        if row.status == "deprecated":
            return self._hydrate_recipe_row(row)

        prior_status = row.status
        session.execute(
            text(
                "UPDATE test_recipes "
                "SET status = 'deprecated', updated_at = NOW() "
                "WHERE recipe_id = :r AND version_seq = :v"
            ),
            {"r": str(recipe_id), "v": version_seq},
        )
        session.add(TestProvenance(
            id=_uuid_mod.uuid4(),
            claim_test_id=None,
            recipe_id=recipe_id,
            event_kind="recipe_deprecated",
            event_data={
                "actor": actor,
                "version_seq": version_seq,
                "prior_status": prior_status,
                "new_status": "deprecated",
                "reason": reason,
            },
            event_actor=actor,
        ))
        session.flush()
        session.refresh(row)
        return self._hydrate_recipe_row(row)

    def change_recipe_priority(
        self,
        session: Session,
        *,
        actor: ActorKind,
        recipe_id: UUID,
        version_seq: int,
        new_priority: int,
    ) -> RecipeRead:
        """Change the priority of a specific recipe version.

        Per D-ε-7: priority is operational metadata, not
        behavior-affecting. Any actor in
        ``{human, s3, s8}`` may call this; ``s4`` cannot
        (s4 is the runtime-state callback only — D-δ scope).

        Per D-ε-8: no ``version_seq`` bump. The recipe's
        priority column updates in-place; ``version_seq``
        tracks semantic supersession, and priority changes are
        operational, not semantic.

        ``new_priority`` accepts any integer (positive,
        negative, zero) — the column carries no sign
        constraint. Resolution operations
        (:meth:`select_recipe_for_execution`) sort by priority
        DESC, so larger values rank higher.

        No-op when ``new_priority == current priority``: no
        provenance event; ``updated_at`` unchanged.

        Provenance event: ``recipe_priority_changed``.
        """
        if actor == "s4":
            raise AuthorityViolationError(
                f"change_recipe_priority is not authorized for "
                f"actor='s4' (runtime-state-callback only); use "
                f"a human / s3 / s8 actor instead",
            )
        if actor not in ("human", "s3", "s8"):
            raise AuthorityViolationError(
                f"change_recipe_priority got unknown actor "
                f"{actor!r}; expected one of human / s3 / s8",
            )
        row = (
            session.query(TestRecipe)
            .filter(
                TestRecipe.recipe_id == recipe_id,
                TestRecipe.version_seq == version_seq,
            )
            .first()
        )
        if row is None:
            raise ValueError(
                f"no recipe row for (recipe_id={recipe_id!r}, "
                f"version_seq={version_seq!r})"
            )
        if row.priority == new_priority:
            return self._hydrate_recipe_row(row)

        prior_priority = row.priority
        session.execute(
            text(
                "UPDATE test_recipes "
                "SET priority = :p, updated_at = NOW() "
                "WHERE recipe_id = :r AND version_seq = :v"
            ),
            {"p": new_priority, "r": str(recipe_id), "v": version_seq},
        )
        session.add(TestProvenance(
            id=_uuid_mod.uuid4(),
            claim_test_id=None,
            recipe_id=recipe_id,
            event_kind="recipe_priority_changed",
            event_data={
                "actor": actor,
                "version_seq": version_seq,
                "prior_priority": prior_priority,
                "new_priority": new_priority,
            },
            event_actor=actor,
        ))
        session.flush()
        session.refresh(row)
        return self._hydrate_recipe_row(row)


# Registered external systems per the migration's enum. Update
# when the migration adds new values (e.g., GitHub issues, Linear).
_VALID_EXTERNAL_SYSTEMS = frozenset({"jira"})


def _verify_no_identity_bearing_refs(
    body: BodyBase, label: str,
) -> None:
    """Walk a operational-layer body's fields and raise
    :class:`OntologyViolationError` if any
    :class:`IdentityBearingRef` instance is encountered.

    The inverse of the claim-side defensive walk in
    :mod:`primeqa.test_representation.coverage`: claim bodies
    must contain only IdentityBearingRef; recipe / trigger /
    environment bodies must NOT contain IdentityBearingRef.
    Pydantic's field-type discipline structurally enforces this
    via :data:`OperationalRef` union resolution; this walk is
    defence in depth.
    """
    from pydantic import BaseModel

    def _walk(value, path):
        if value is None:
            return
        if isinstance(value, IdentityBearingRef):
            raise OntologyViolationError(
                f"IdentityBearingRef encountered in operational-layer "
                f"content at {path}: operational bodies use "
                f"OperationalRef (PinnedRef | LogicalRef) per D-058 §5",
                field_path=path,
                actual_entity_type=value.entity_type,
            )
        # Other ref types (plain PinnedRef, LogicalRef) are
        # the EXPECTED shape in operational bodies — don't
        # raise on them.
        if isinstance(value, BaseModel):
            for field_name in type(value).model_fields:
                if field_name == "body_schema_version":
                    continue
                _walk(
                    getattr(value, field_name),
                    f"{path}.{field_name}",
                )
            return
        if isinstance(value, list):
            for idx, item in enumerate(value):
                _walk(item, f"{path}[{idx}]")
            return
        if isinstance(value, dict):
            for k, v in value.items():
                _walk(v, f"{path}.{k}")
            return
        # Scalars carry no refs.

    _walk(body, label)

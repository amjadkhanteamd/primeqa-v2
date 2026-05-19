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

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from primeqa.test_representation.authority import ActorKind
from primeqa.test_representation.models.common import BodyBase


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

        Implementation in Track D-β.2.
        """
        raise NotImplementedError(
            "write_claim is implemented in Track D-β.2. "
            "The Coordinator class skeleton lives at Track D-β.1."
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
    ) -> WriteRecipeResult:
        """Write a new recipe version per SPEC §4.7.6.

        Recipe authority is a separate model from claim authority
        (recipes always require re-approval per SPEC §7.4); the
        recipe authority helper + the write flow land together in
        Track D-β.3.

        Args:
          session: Active SQLAlchemy session (caller-managed).
          actor: The mutation actor.
          recipe_id: ``None`` to create a new recipe; existing
            UUID to add a new version.
          claim_test_id: The claim this recipe realizes (logical
            FK to :class:`TestClaim.test_id` per A6 + D-060
            §4.7.6).
          trigger_kind: One of the 5 trigger-kinds per D-055.
          recipe_kind: One of the 5 recipe-kinds per D-054.
          causal_initiation: The causal-initiation body (trigger
            body shape per Track B-γ).
          observation_realization: The observation-realization
            body (recipe body shape per Track B-δ).
          execution_environment: The execution-environment body
            (per Track B-δ).

        Returns:
          A :class:`WriteRecipeResult` with the new version's
          metadata.

        Implementation in Track D-β.3.
        """
        raise NotImplementedError(
            "write_recipe is implemented in Track D-β.3. "
            "The Coordinator class skeleton lives at Track D-β.1."
        )

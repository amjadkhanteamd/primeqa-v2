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
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.test_representation.authority import (
    ActorKind,
    check_claim_write_authority,
    enforce_authority,
)
from primeqa.test_representation.canonicalization import canonicalize
from primeqa.test_representation.coverage import extract_coverage
from primeqa.test_representation.errors import (
    BodyCorruptionError,
    SchemaIncompatibilityError,
)
from primeqa.test_representation.identity_hash import (
    IDENTITY_HASH_VERSION,
    compute_identity_hash,
)
from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.registry import get_body_model
from primeqa.test_representation.models_db import (
    TestClaim,
    TestClaimCoverage,
    TestProvenance,
)


# ---------------------------------------------------------------------------
# Discriminator value taxonomies (mirror Track A's PG enums).
# ---------------------------------------------------------------------------

_VALID_ARCHETYPES = frozenset({
    "data_behavior", "configuration", "permission", "ui", "integration",
})

_VALID_CLAIM_KINDS = frozenset({
    # data-behavior (4)
    "value-claim", "state-transition-claim",
    "automation-effect-claim", "prohibition-claim",
    # configuration (3)
    "existence-claim", "property-claim", "metadata-relationship-claim",
    # permission (2)
    "capability-claim", "sharing-rule-claim",
    # ui (3)
    "element-state-claim", "navigation-claim", "layout-claim",
    # integration (4)
    "platform-event-claim", "outbound-message-claim",
    "callout-claim", "inbound-effect-claim",
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

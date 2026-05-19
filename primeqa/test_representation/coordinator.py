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
    check_recipe_write_authority,
    enforce_authority,
)
from primeqa.test_representation.canonicalization import canonicalize
from primeqa.test_representation.coverage import extract_coverage
from primeqa.test_representation.errors import (
    BodyCorruptionError,
    OntologyViolationError,
    SchemaIncompatibilityError,
)
from primeqa.test_representation.identity_hash import (
    IDENTITY_HASH_VERSION,
    compute_identity_hash,
)
from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
)
from primeqa.test_representation.models.registry import get_body_model
from primeqa.test_representation.models_db import (
    TestClaim,
    TestClaimCoverage,
    TestProvenance,
    TestRecipe,
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

_VALID_TRIGGER_KINDS = frozenset({
    "inbound-trigger",
    "data-mutation-trigger",
    "ui-trigger",
    "time-trigger",
    "configuration-trigger",
})

_VALID_RECIPE_KINDS = frozenset({
    "data-recipe",
    "metadata-recipe",
    "ui-recipe",
    "event-subscription-recipe",
    "callout-intercept-recipe",
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
        claim_version_seq: Optional[int] = None,
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

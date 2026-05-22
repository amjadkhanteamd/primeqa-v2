"""Emission authoring (D-097.5 / D-098 / D-099) — the substrate authors the
S2 claim + recipe bodies from a grounded candidate's S1 entities.

Guardrail 2 (D-097.5): the substrate owns *what is asserted true*. It authors
the claim body (the asserted relationship) and the recipe bodies (how the
relationship is re-verified) from the S1 entities that grounding resolved —
the LLM never authors entities or decides truth; it owns only linguistic
realization (``emit_outcome``).

Two plain dataclasses + one function:

  - :class:`GroundedEmission` — the grounding facts stashed by
    ``governance_core._resolve_configuration`` into the conversation
    ``state`` when an edge is admissibly grounded. Pure data (S1 entity
    facts); no S2 import, so governance_core stays light.
  - :class:`EmissionBundle` — the authored S2 bodies + the discriminator
    strings + the conditional-caveat decision. Carried on
    :class:`~primeqa.generation.governance.OutcomeVerdict` and couriered to
    the persister, which writes them in one Session (D-097.4 / D-099).
  - :func:`author_emission` — the ONLY site that constructs S2 body models
    for generation. Builds the metadata-relationship claim + an
    inspection-triggered metadata-read verification recipe.

The recipe shape realizes D-099: ``causal_initiation`` is the
``inspection-trigger`` (invariant-inspective initiation — no causal event);
``observation_realization`` is a ``metadata-recipe`` in ``metadata_read``
mode that reads the source entity's metadata and asserts the relationship
surfaces. Per D-099.3 this is an execution-time re-inspection contract (S4
re-verifies the edge when the recipe runs), not a frozen grounding snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from primeqa.generation.semantic_completeness import requires_caveat
from primeqa.test_representation.models.claims.configuration import (
    MetadataRelationshipClaimBody,
)
from primeqa.test_representation.models.conditions import SemanticConditionsBody
from primeqa.test_representation.models.environment import (
    AuthAssumption,
    ExecutionEnvironmentBody,
)
from primeqa.test_representation.models.primitives import AssertionPredicate
from primeqa.test_representation.models.recipes.metadata_recipe import (
    AssertStep,
    MetadataRecipeBody,
    ReadMetadataStep,
)
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
)
from primeqa.test_representation.models.triggers.inspection import (
    InspectionTriggerBody,
)


# ---------------------------------------------------------------------------
# Grounding facts (stashed by governance during config grounding)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Endpoint:
    """One resolved relationship endpoint (an S1 entity, by identity)."""

    entity_id: UUID
    entity_type: str
    external_id: str            # sf_api_name (human-readable cross-check)


@dataclass(frozen=True)
class GroundedEmission:
    """The grounding facts an admissibly-grounded config metadata-relationship
    candidate carries forward to emission. Authored-from, never LLM-supplied
    (D-097.5)."""

    archetype: str              # "configuration"
    claim_kind: str             # "metadata-relationship-claim"
    edge_type: str              # the verified Tier-1 edge (e.g. "APPLIES_TO")
    version_seq: int            # the pinned S1 version grounding ran against
    source: _Endpoint
    target: _Endpoint
    requirement_excerpt: str


# ---------------------------------------------------------------------------
# Authored bodies (couriered to the persister)
# ---------------------------------------------------------------------------

@dataclass
class EmissionBundle:
    """The substrate-authored S2 bodies for one draft, plus the discriminator
    strings ``write_claim`` / ``write_recipe`` need and the conditional-caveat
    decision (D-097.3). Refs do not exist yet — the persister assigns them
    post-write (D-099)."""

    archetype: str
    claim_kind: str
    # identity-bearing layers (claim)
    asserted_truth: MetadataRelationshipClaimBody
    semantic_conditions: SemanticConditionsBody
    # operational layers (recipe)
    trigger_kind: str
    recipe_kind: str
    causal_initiation: InspectionTriggerBody
    observation_realization: MetadataRecipeBody
    execution_environment: ExecutionEnvironmentBody
    # D-097.3 conditional plausibility caveat (from the single authority).
    # False for a Layer-1-complete config claim; the marker carries the rest.
    caveat_required: bool


# ---------------------------------------------------------------------------
# Authoring (Guardrail 2 — substrate authors semantic truth)
# ---------------------------------------------------------------------------

def author_emission(g: GroundedEmission) -> EmissionBundle:
    """Author the claim + recipe bodies for a grounded config metadata-
    relationship candidate. The single site that constructs S2 body models for
    generation (D-097.5)."""
    src_ref = IdentityBearingRef(
        entity_type=g.source.entity_type, entity_id=g.source.entity_id,
        version_seq=g.version_seq, external_id=g.source.external_id,
    )
    tgt_ref = IdentityBearingRef(
        entity_type=g.target.entity_type, entity_id=g.target.entity_id,
        version_seq=g.version_seq, external_id=g.target.external_id,
    )
    claim = MetadataRelationshipClaimBody(
        edge_type=g.edge_type, source=src_ref, target=tgt_ref,
    )
    # No preconditions: a metadata-relationship either holds in the org or it
    # does not — it applies unconditionally.
    conditions = SemanticConditionsBody(conditions=[])

    # Recipe (D-099): inspection-trigger + metadata_read read-and-assert. The
    # operational refs are logical (resolve-by-name) so S4 re-inspects the
    # *current* source entity at execution time (D-099.3); operational layers
    # must NOT carry IdentityBearingRef (write_recipe step 5).
    trigger = InspectionTriggerBody()
    recipe = MetadataRecipeBody(
        mode="metadata_read",
        api_choice="metadata_api",
        steps=[
            ReadMetadataStep(
                step_id="read-source",
                target_entity=LogicalRef(
                    entity_type=g.source.entity_type,
                    external_id=g.source.external_id,
                ),
                fields_to_capture=[g.edge_type],
            ),
            AssertStep(
                step_id="assert-edge",
                predicate=AssertionPredicate(
                    subject_ref="read-source", predicate="exists",
                ),
            ),
        ],
    )
    env = ExecutionEnvironmentBody(
        auth_assumptions=[AuthAssumption(
            auth_kind="metadata_api_user",
            details=(f"read {g.source.external_id} metadata to verify "
                     f"{g.edge_type} -> {g.target.external_id}"),
        )],
    )
    return EmissionBundle(
        archetype=g.archetype,
        claim_kind=g.claim_kind,
        asserted_truth=claim,
        semantic_conditions=conditions,
        trigger_kind="inspection-trigger",
        recipe_kind="metadata-recipe",
        causal_initiation=trigger,
        observation_realization=recipe,
        execution_environment=env,
        caveat_required=requires_caveat(g.claim_kind),
    )

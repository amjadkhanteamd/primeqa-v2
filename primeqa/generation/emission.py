"""Emission authoring (D-097.5 / D-098 / D-099 / D-101) — the substrate authors
the S2 claim + recipe bodies from a grounded candidate's S1 entities.

Guardrail 2 (D-097.5): the substrate owns *what is asserted true*. It authors
the claim body (the asserted relationship / prohibition) and the recipe bodies
(how it is re-verified) from the S1 entities that grounding resolved — the LLM
never authors entities or decides truth; it owns only linguistic realization
(``emit_outcome``).

Grounding facts come in per-shape dataclasses, stashed into the conversation
``state`` by governance_core when a candidate is admissibly grounded:

  - :class:`GroundedEmission` — config metadata-relationship (D-098): a verified
    Tier-1 edge between two endpoints. ``caveat_required=False`` (Layer-1-
    complete; reading S1 IS the verification).
  - :class:`GroundedNegative` — data_behavior prohibition negative (D-101 /
    D-107): a ValidationRule ``APPLIES_TO`` the subject grounds the rejection.
    When a grounding VR's formula parses AND a violating value derives with
    certainty, the negative is Layer-2-*verified* (``caveat_required=False``);
    otherwise it stays Layer-1-*plausible* (``caveat_required=True``). The
    verified-vs-caveated line IS the derivable/not-derivable line.

:func:`author_emission` dispatches on the grounded shape and returns an
:class:`EmissionBundle` the persister writes in one Session (D-097.4 / D-099),
carrying the registry caveat verdict (D-101.3) it stamps on the outcome.

Both shapes emit an *inspection* recipe (D-099): ``inspection-trigger`` (no
causal event) + a ``metadata_read`` read-and-assert over the grounding edge —
an execution-time re-inspection contract (D-099.3), not a frozen snapshot. The
behavioral negative (construct a violating mutation, observe the rejection) is
double-gated on the formula parser AND an expect-rejection recipe mode. The
parser landed (D-107): it now discharges the verified *marker* (LAYER_2 vs. the
caveat) by deriving a violating value with certainty. The behavioral *recipe*
(construct + observe) is still deferred (D-100.2) — under Option C the derived
payload is the verified-vs-caveated gate only and is NOT persisted, so the
prohibition claim's identity_hash is unchanged (no premature D-088 break).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from primeqa.generation.enums import AdmissibilityLayer, CaveatKind
from primeqa.generation.semantic_completeness import caveat_kind, requires_caveat
from primeqa.generation.verified_negative import VerifiedNegative, derive
from primeqa.semantic.formula import parse
from primeqa.test_representation.models.claims.configuration import (
    MetadataRelationshipClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.prohibition_claim import (
    ProhibitionClaimBody,
)
from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.conditions import SemanticConditionsBody
from primeqa.test_representation.models.environment import (
    AuthAssumption,
    ExecutionEnvironmentBody,
)
from primeqa.test_representation.models.primitives import (
    AssertionPredicate,
    RejectionExpectation,
    RejectionSignal,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)
from primeqa.test_representation.models.recipes.metadata_recipe import (
    AssertStep,
    MetadataRecipeBody,
    ReadMetadataStep,
)
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
)
from primeqa.test_representation.models.triggers.data_mutation import (
    DataMutationTriggerBody,
)
from primeqa.test_representation.models.triggers.inspection import (
    InspectionTriggerBody,
)

# Validation-rule rejections surface this generic API error code regardless of
# the formula (D-101.2): the honest Layer-1 floor — anything more specific
# without the parser is fabricated specificity.
_VR_REJECTION_ERROR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"

# ProhibitionClaimBody.operation closed enum; the substrate binds the intent
# hint against it, defaulting to a safe generic when unspecified (D-101.2).
_PROHIBITION_OPERATIONS = frozenset({
    "delete", "create_duplicate", "modify_field",
    "modify_record", "share", "transfer_ownership",
})
_DEFAULT_OPERATION = "modify_record"


# ---------------------------------------------------------------------------
# Emittable claim_kinds — the single source of truth (D-105.1)
# ---------------------------------------------------------------------------
# The (archetype, claim_kind) pairs the substrate can author + persist an
# emission for today. Resolution gates PROCEED_TO_EMIT to this set; a
# grounded-but-unbuilt kind refuses (emission-deferred) rather than crashing
# in finalize_outcome (D-105). This set grows as emission for more kinds is
# built (the runtime face of D-097.6's deferral). MUST stay in lockstep with
# what author_emission can dispatch (GroundedEmission -> config metadata-
# relationship; GroundedNegative -> data_behavior prohibition) — a drift-guard
# test binds the two.

EMITTABLE: frozenset = frozenset({
    ("configuration", "metadata-relationship-claim"),   # D-098 (GroundedEmission)
    ("data_behavior", "prohibition-claim"),              # D-101 (GroundedNegative)
})


def is_emittable(archetype: str, claim_kind: str) -> bool:
    """Whether the substrate can author + persist an emission for this
    (archetype, claim_kind) today (D-105.1). The authority both resolution (the
    PROCEED gate) and finalize_outcome (the backstop) consult."""
    return (archetype, claim_kind) in EMITTABLE


# ---------------------------------------------------------------------------
# Grounding facts (stashed by governance during grounding)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Endpoint:
    """One resolved S1 entity, by identity."""

    entity_id: UUID
    entity_type: str
    external_id: str            # sf_api_name (human-readable cross-check)


@dataclass(frozen=True)
class GroundedEmission:
    """Config metadata-relationship grounding (D-098): a verified Tier-1 edge
    between two endpoints. Authored-from, never LLM-supplied (D-097.5)."""

    archetype: str              # "configuration"
    claim_kind: str             # "metadata-relationship-claim"
    edge_type: str              # the verified Tier-1 edge (e.g. "APPLIES_TO")
    version_seq: int            # the pinned S1 version grounding ran against
    source: _Endpoint
    target: _Endpoint
    requirement_excerpt: str


@dataclass(frozen=True)
class GroundedNegative:
    """data_behavior prohibition-negative grounding (D-101): a ValidationRule
    ``APPLIES_TO`` the subject grounds the rejection at Layer-1-plausible. The
    operation is bound from the intent hint; the matched VR's edge is what the
    inspection recipe re-verifies."""

    archetype: str              # "data_behavior"
    claim_kind: str             # "prohibition-claim"
    operation_hint: Optional[str]   # raw intent hint; bound at authoring time
    version_seq: int
    subject: _Endpoint          # the Object the prohibited op would act on
    requirement_excerpt: str
    # Formula texts of the grounding ValidationRules (D-107). Authoring attempts
    # violating-value derivation over these to decide verified vs. caveated.
    # Empty (the default) -> no derivable formula -> caveated fallback unchanged.
    vr_formulas: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Authored bodies (couriered to the persister)
# ---------------------------------------------------------------------------

@dataclass
class EmissionBundle:
    """The substrate-authored S2 bodies for one draft, the discriminator strings
    ``write_claim`` / ``write_recipe`` need, and the registry caveat verdict
    (D-097.3 / D-101.3). Refs do not exist yet — the persister assigns them
    post-write (D-099)."""

    archetype: str
    claim_kind: str
    # identity-bearing layer (claim) — concrete type varies by claim_kind
    asserted_truth: BodyBase
    semantic_conditions: SemanticConditionsBody
    # operational layers (recipe)
    trigger_kind: str
    recipe_kind: str
    causal_initiation: InspectionTriggerBody
    observation_realization: MetadataRecipeBody
    execution_environment: ExecutionEnvironmentBody
    # Admissibility marker (D-097.3 / D-107): how deep grounding actually went —
    # LAYER_1 (constraint exists/active) or LAYER_2 (formula-verified: a violating
    # value derived with certainty). finalize_outcome stamps this verbatim.
    admissibility_layer: AdmissibilityLayer
    # Caveat posture (D-101.3): the registry verdict, stamped on the outcome.
    # Paired with the marker — LAYER_2 <=> caveat dropped (the D-107 invariant).
    caveat_required: bool
    caveat_kind: Optional[CaveatKind]


# ---------------------------------------------------------------------------
# Shared inspection recipe (D-099): inspection-trigger + metadata_read
# ---------------------------------------------------------------------------

def _inspection_recipe(
    *, read_entity_type: str, read_external_id: str,
    capture_field: str, env_detail: str,
) -> tuple[InspectionTriggerBody, MetadataRecipeBody, ExecutionEnvironmentBody]:
    """Build the (trigger, recipe, env) triple for a verification by inspection.
    Reads ``read_entity_type``/``read_external_id``'s metadata and asserts the
    grounding edge surfaces. Operational refs are logical (resolve-by-name) so
    S4 re-inspects current state (D-099.3); never identity-bearing (write_recipe
    step 5)."""
    trigger = InspectionTriggerBody()
    recipe = MetadataRecipeBody(
        mode="metadata_read",
        api_choice="metadata_api",
        steps=[
            ReadMetadataStep(
                step_id="read-subject",
                target_entity=LogicalRef(
                    entity_type=read_entity_type, external_id=read_external_id,
                ),
                fields_to_capture=[capture_field],
            ),
            AssertStep(
                step_id="assert-edge",
                predicate=AssertionPredicate(
                    subject_ref="read-subject", predicate="exists",
                ),
            ),
        ],
    )
    env = ExecutionEnvironmentBody(
        auth_assumptions=[AuthAssumption(
            auth_kind="metadata_api_user", details=env_detail,
        )],
    )
    return trigger, recipe, env


# ---------------------------------------------------------------------------
# Per-shape authoring (Guardrail 2 — substrate authors semantic truth)
# ---------------------------------------------------------------------------

def _author_config(g: GroundedEmission) -> EmissionBundle:
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
    # A metadata-relationship holds in the org or it does not — unconditional.
    conditions = SemanticConditionsBody(conditions=[])
    trigger, recipe, env = _inspection_recipe(
        read_entity_type=g.source.entity_type,
        read_external_id=g.source.external_id,
        capture_field=g.edge_type,
        env_detail=(f"read {g.source.external_id} metadata to verify "
                    f"{g.edge_type} -> {g.target.external_id}"),
    )
    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        # Config metadata-relationship is Layer-1-complete (D-079): reading S1 IS
        # the verification. No Layer 2 exists, so no caveat regardless of verified.
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=requires_caveat(g.claim_kind),
        caveat_kind=caveat_kind(g.claim_kind),
    )


def _derive_violation(formulas: tuple[str, ...]) -> Optional[VerifiedNegative]:
    """The verified-vs-caveated gate AND the violating-payload source (D-107 /
    D-110.3). Returns the first grounding VR formula whose error-condition
    *certainly* derives a violating field assignment (a
    :class:`VerifiedNegative` carrying ``violating_payload``), or ``None`` when
    no formula is derivable (the caveated fallback).

    Multiple VRs apply at-least-one semantics: a single derivable formula
    suffices, since any one VR firing produces the rejection (others can only
    add rejections, never suppress one). D-110.3 *uses* the payload (the
    behavioral create's field_values); it is carried in the **recipe**
    (operational), never the claim — so the Option-C claim-identity invariant
    holds (the claim body is byte-identical whether verified or caveated)."""
    for text in formulas:
        result = derive(parse(text))
        if isinstance(result, VerifiedNegative):
            return result
    return None


def _behavioral_recipe(
    *, subject_entity_type: str, subject_external_id: str,
    violating_payload: dict, env_detail: str,
) -> tuple[DataMutationTriggerBody, DataRecipeBody, ExecutionEnvironmentBody]:
    """Build the (trigger, recipe, env) triple for a **behavioral** negative
    (D-110.3): a create the org should reject. The ``CreateStep`` carries the
    parser-derived ``violating_payload`` as its ``field_values`` and an
    ``expect_rejection`` projecting the claim's ``RejectionSignal`` (the generic
    VR code; ``error_field`` dropped — operational bodies forbid it). The target
    is logical (resolve-by-name)."""
    target = LogicalRef(
        entity_type=subject_entity_type, external_id=subject_external_id)
    trigger = DataMutationTriggerBody(
        operation="create", target=target,
        identity_context="system", volume="single",
    )
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[CreateStep(
            step_id="create-violating",
            target_object=target,
            field_values=dict(violating_payload),
            expect_rejection=RejectionExpectation(
                error_code=_VR_REJECTION_ERROR_CODE),
        )],
    )
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user", details=env_detail,
    )])
    return trigger, recipe, env


def _author_negative(g: GroundedNegative) -> EmissionBundle:
    subject_ref = IdentityBearingRef(
        entity_type=g.subject.entity_type, entity_id=g.subject.entity_id,
        version_seq=g.version_seq, external_id=g.subject.external_id,
    )
    # Bind the operation from the intent hint against the closed enum, defaulting
    # to a safe generic when unspecified/invalid (D-101.2).
    operation = (g.operation_hint if g.operation_hint in _PROHIBITION_OPERATIONS
                 else _DEFAULT_OPERATION)
    # Verified-vs-caveated gate (D-107) + the violating-payload source (D-110.3):
    # does a grounding VR formula certainly derive a violating value? The payload
    # rides the RECIPE (operational), never the claim — so the claim body is
    # byte-identical whether verified or caveated (the Option-C identity_hash
    # invariant; verified below by a stability test).
    violation = _derive_violation(g.vr_formulas)
    verified = violation is not None
    claim = ProhibitionClaimBody(
        target=subject_ref,
        operation=operation,
        prohibition_mechanism="validation_rule",
        # Generic VR rejection code — derivable from the mechanism alone (D-101.2
        # honest floor). Even a verified negative keeps this generic signal: the
        # LAYER_2 marker certifies "a rejecting input exists," not the specific
        # error message/field. The specific field/value lives in the behavioral
        # recipe (D-110.3), not the claim.
        expected_rejection=RejectionSignal(error_code=_VR_REJECTION_ERROR_CODE),
    )
    # The triggering condition lives in the formula; whether or not it parsed, the
    # claim is unconditional at this layer (the marker/caveat carry the verdict).
    conditions = SemanticConditionsBody(conditions=[])

    # D-110.3 (S3-thin): a VERIFIED negative emits the BEHAVIORAL recipe — a
    # create carrying the parser-derived violating payload + expect_rejection
    # (behavioral subsumes structural: it tests the VR *enforces*). A CAVEATED
    # negative (no derivable formula) stays the INSPECTION re-verify (there is no
    # violation to construct). Replace, not augment (single-recipe; D-110.3).
    if verified:
        trigger, recipe, env = _behavioral_recipe(
            subject_entity_type=g.subject.entity_type,
            subject_external_id=g.subject.external_id,
            violating_payload=violation.violating_payload,
            env_detail=(f"create a {g.subject.external_id} record violating the "
                        f"grounding validation rule (expect rejection)"),
        )
        trigger_kind, recipe_kind = "data-mutation-trigger", "data-recipe"
    else:
        trigger, recipe, env = _inspection_recipe(
            read_entity_type=g.subject.entity_type,
            read_external_id=g.subject.external_id,
            capture_field="APPLIES_TO",
            env_detail=(f"read {g.subject.external_id} metadata to verify a "
                        f"validation rule applies (rejection plausibility)"),
        )
        trigger_kind, recipe_kind = "inspection-trigger", "metadata-recipe"

    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind=trigger_kind, recipe_kind=recipe_kind,
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        # Verified -> LAYER_2 + no caveat; otherwise LAYER_1 + the caveat (D-107).
        # The marker and the caveat move together: LAYER_2 <=> caveat dropped.
        admissibility_layer=(AdmissibilityLayer.LAYER_2 if verified
                             else AdmissibilityLayer.LAYER_1),
        caveat_required=requires_caveat(g.claim_kind, verified=verified),
        caveat_kind=caveat_kind(g.claim_kind, verified=verified),
    )


def author_emission(grounded: object) -> EmissionBundle:
    """Author the claim + recipe bodies for a grounded candidate. The single
    site that constructs S2 body models for generation (D-097.5); dispatches on
    the grounding shape."""
    if isinstance(grounded, GroundedEmission):
        return _author_config(grounded)
    if isinstance(grounded, GroundedNegative):
        return _author_negative(grounded)
    raise TypeError(f"author_emission: unsupported grounding {type(grounded).__name__}")

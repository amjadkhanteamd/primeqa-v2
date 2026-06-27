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
from typing import Any, Optional
from uuid import UUID

from primeqa.generation.enums import AdmissibilityLayer, CaveatKind
from primeqa.generation.semantic_completeness import caveat_kind, requires_caveat
from primeqa.generation.verified_negative import (
    VerifiedNegative,
    VerifiedUpdateNegative,
    derive,
    derive_update,
)
from primeqa.semantic.formula import parse
from primeqa.test_representation.models.claims.configuration import (
    ExistenceClaimBody,
    MetadataRelationshipClaimBody,
    PropertyClaimBody,
)
from primeqa.test_representation.models.claims.permission import (
    CapabilityClaimBody,
)
from primeqa.test_representation.models.claims.ui import (
    LayoutClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.prohibition_claim import (
    ProhibitionClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.automation_effect_claim import (
    AutomationEffectClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.state_transition_claim import (
    StateTransitionClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.value_claim import (
    ValueClaimBody,
)
from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.conditions import SemanticConditionsBody
from primeqa.test_representation.models.environment import (
    AuthAssumption,
    ExecutionEnvironmentBody,
)
from primeqa.test_representation.models.primitives import (
    AssertionPredicate,
    EventDescriptor,
    FieldChangeEffect,
    LiteralValue,
    NullValue,
    RejectionExpectation,
    RejectionSignal,
    StateDescriptor,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    AssertStep as DataAssertStep,
    CreateStep,
    DataRecipeBody,
    ReadStep,
    UpdateStep,
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
    ("configuration", "existence-claim"),                # D-122 (GroundedExistence)
    ("configuration", "property-claim"),                 # D-122 (GroundedProperty)
    ("permission", "capability-claim"),                  # D-123 (GroundedCapability)
    ("ui", "layout-claim"),                              # D-124 (GroundedLayout)
    ("data_behavior", "prohibition-claim"),              # D-101 (GroundedNegative)
    ("data_behavior", "value-claim"),                    # D-115 (GroundedPositive)
    ("data_behavior", "state-transition-claim"),         # D-210 (GroundedStateTransition)
    ("data_behavior", "automation-effect-claim"),        # D-210 (GroundedAutomationEffect)
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


@dataclass(frozen=True)
class GroundedPositive:
    """data_behavior value-claim positive grounding (D-115 slice 1): a Field
    ``BELONGS_TO`` an Object, with the requirement-sourced value the create sets
    and the read-back asserts.

    ``value`` is carried **verbatim** from the value-claim's ``expected_value`` —
    never derived or invented (contrast :class:`GroundedNegative`, whose violating
    value is *derived* via D-107). ``target_object`` is the field's parent
    (resolved via S1 ``BELONGS_TO`` at grounding time — the *held* governance
    stash; in this slice the fact is constructed directly). Authored-from, never
    LLM-supplied (D-097.5)."""

    archetype: str              # "data_behavior"
    claim_kind: str             # "value-claim"
    version_seq: int            # the pinned S1 version grounding ran against
    target_object: _Endpoint    # the Object to create on (the field's parent)
    field: _Endpoint            # the Field whose value is asserted
    value: Any                  # the requirement-sourced expected value (verbatim)
    requirement_excerpt: str


@dataclass(frozen=True)
class GroundedStateTransition:
    """data_behavior state-transition positive grounding (D-210.1): the subject
    Object + the NAMED to-state field (verified to exist via S1 ``BELONGS_TO``
    at grounding) + the requirement-sourced to-value (verbatim, like
    :class:`GroundedPositive`). v1 covers the CREATE-SCOPED transition only —
    the org sets the field when the subject is created; cross-object triggers
    defer at the stash gate. ``from_state`` is unknown in v1 (empty)."""

    archetype: str              # "data_behavior"
    claim_kind: str             # "state-transition-claim"
    version_seq: int
    subject: _Endpoint          # the Object whose state transitions
    field: _Endpoint            # the to-state Field (verified BELONGS_TO subject)
    to_value: Any               # the requirement-sourced to-state value (verbatim)
    requirement_excerpt: str
    # D-222: the OPTIONAL staged trigger — the field/value the create must
    # SET to provoke the transition (verified BELONGS_TO subject at the
    # stash gate; both None when the hints omit it or it doesn't verify).
    trigger_field: Optional[_Endpoint] = None
    trigger_value: Optional[Any] = None
    # D-227: the OPTIONAL cross-object trigger — the transition is provoked
    # by creating a RELATED record (trigger_object) carrying a lookup back
    # to the subject (trigger_lookup_field, verified BELONGS_TO the trigger
    # object at the stash gate). Both set together or both None.
    trigger_object: Optional[_Endpoint] = None
    trigger_lookup_field: Optional[_Endpoint] = None


@dataclass(frozen=True)
class GroundedAutomationEffect:
    """data_behavior automation-effect positive grounding (D-210.1): the
    TRIGGER object + the Flow that ``TRIGGERS_ON`` it (the real grounding
    dimension — the matched Flow IS the claim's automation ref) + the verified
    effect shape. Exactly one of three shapes (the stash gate enforces it):

      - **same-record**: ``effect_field`` on the SUBJECT (verified) +
        ``effect_value`` — the Flow stamps a field on the trigger record;
        ``effect_object``/``effect_lookup_field`` are None.
      - **cross-object (child-of-trigger)**: ``effect_object`` (verified
        Object) + ``effect_lookup_field`` (verified BELONGS_TO the effect
        object) — the Flow creates a correlated record;
        ``effect_field``/``effect_value`` optionally assert one of its
        fields, else existence is the assert.
      - **parent-stamp (D-227)**: ``effect_object`` + ``effect_via_lookup_field``
        (verified BELONGS_TO the SUBJECT — the trigger record's own lookup to
        the effect parent) + ``effect_field`` (REQUIRED, on the effect object);
        ``effect_value`` optional — value-less stamps assert ``not_null``
        (e.g. a $Flow.CurrentDate stamp has no stable literal).
    """

    archetype: str              # "data_behavior"
    claim_kind: str             # "automation-effect-claim"
    version_seq: int
    subject: _Endpoint          # the TRIGGER object (the Flow fires on it)
    automation: _Endpoint       # the Flow (matched via TRIGGERS_ON)
    requirement_excerpt: str
    effect_field: Optional[_Endpoint] = None     # same-record: on subject;
                                                 # cross-object: on effect_object
    effect_value: Any = None
    effect_object: Optional[_Endpoint] = None    # cross-object only
    effect_lookup_field: Optional[_Endpoint] = None  # cross-object correlate
    # D-227 parent-stamp: the SUBJECT's own lookup to the effect parent.
    effect_via_lookup_field: Optional[_Endpoint] = None


@dataclass(frozen=True)
class GroundedExistence:
    """configuration existence-claim grounding (D-122): an S1 entity verified to
    exist (Layer-1-complete — the non-empty ``get_entities`` result IS the
    verification, D-079). Authored-from, never LLM-supplied (D-097.5)."""

    archetype: str              # "configuration"
    claim_kind: str             # "existence-claim"
    version_seq: int            # the pinned S1 version grounding ran against
    subject: _Endpoint          # the entity asserted to exist
    requirement_excerpt: str


@dataclass(frozen=True)
class GroundedProperty:
    """configuration property-claim grounding (D-122): an S1-modeled detail
    property of an entity, read verbatim from ``get_entity_details`` (Layer-1-
    complete, D-079). ``expected_value`` is the value READ from S1 — never the
    requirement's assertion on faith; a mismatch refuses at grounding rather than
    emit a false claim (invent-nothing, Guardrail 2)."""

    archetype: str              # "configuration"
    claim_kind: str             # "property-claim"
    version_seq: int
    subject: _Endpoint          # the entity whose property is asserted
    property_name: str          # the S1 detail column (e.g. "is_required")
    expected_value: Any         # the value read from the S1 detail row (raw scalar)
    requirement_excerpt: str


@dataclass(frozen=True)
class GroundedCapability:
    """permission capability-claim grounding (D-123): a Profile/PermissionSet
    grants the asserted capability on an Object/Field — the ``GRANTS_*_ACCESS``
    edge verified via ``get_related`` (the grant is *configured*). Layer-1-
    complete (D-079); authored-from, never LLM-supplied (D-097.5)."""

    archetype: str              # "permission"
    claim_kind: str             # "capability-claim"
    version_seq: int
    granting_subject: _Endpoint  # the Profile or PermissionSet
    target: _Endpoint            # the Object or Field
    granted_capability: str      # "read" / "edit"
    grant_type: str              # "object" | "field"
    requirement_excerpt: str


@dataclass
class GroundedLayout:
    """ui layout-claim grounding (D-124): a Field is placed on a PageLayout — the
    ``INCLUDES_FIELD`` edge verified via ``get_related`` (the placement is
    *configured*). Layer-1-complete (D-079); authored-from, never LLM-supplied
    (D-097.5)."""

    archetype: str              # "ui"
    claim_kind: str             # "layout-claim"
    version_seq: int
    layout: _Endpoint            # the PageLayout
    field: _Endpoint             # the Field placed on it
    requirement_excerpt: str


# ---------------------------------------------------------------------------
# Authored bodies (couriered to the persister)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecondaryRecipe:
    """One ADDITIONAL operational realization of the same claim (D-228) — a
    weaker/alternative verification the selection layer can fall back to
    (priority DESC; the primary keeps the column default 0, so a fallback
    carries a NEGATIVE priority). Same write_recipe discriminator strings +
    bodies as the bundle's primary."""

    trigger_kind: str
    recipe_kind: str
    causal_initiation: object
    observation_realization: object
    execution_environment: ExecutionEnvironmentBody
    priority: int


@dataclass
class EmissionBundle:
    """The substrate-authored S2 bodies for one draft, the discriminator strings
    ``write_claim`` / ``write_recipe`` need, and the registry caveat verdict
    (D-097.3 / D-101.3). Refs do not exist yet — the persister assigns them
    post-write (D-099). D-228: ``secondary_recipes`` carries N additional
    realizations of the SAME claim (the replaceability invariant's write side);
    the claim's admissibility reflects the STRONGEST realization."""

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
    # D-228: additional realizations (fallback depths / alternative shapes).
    secondary_recipes: tuple = ()
    # D-288 (4f.2-prep): the claim's evaluation strategy, claim-DERIVED at authoring.
    # None today (every authoring path leaves it unset → write_claim persists NULL →
    # the router/decision read None → single, byte-identical). The future bva-authoring
    # helper (4f.2b, after the §4a claim-shape is settled in 4f.2a) stamps 'bva' here;
    # persistence reads it through to write_claim. The wire is dormant until then.
    strategy_kind: Optional[str] = None


# ---------------------------------------------------------------------------
# Shared inspection recipe (D-099): inspection-trigger + metadata_read
# ---------------------------------------------------------------------------

def _inspection_recipe(
    *, read_entity_type: str, read_external_id: str,
    capture_field: str, env_detail: str,
    assert_predicate: str = "exists", assert_value: Any = None,
    edge_target: Optional[LogicalRef] = None,
    edge_qualifier: Optional[str] = None,
) -> tuple[InspectionTriggerBody, MetadataRecipeBody, ExecutionEnvironmentBody]:
    """Build the (trigger, recipe, env) triple for a verification by inspection.
    Reads ``read_entity_type``/``read_external_id``'s metadata and asserts the
    grounding edge surfaces. Operational refs are logical (resolve-by-name) so
    S4 re-inspects current state (D-099.3); never identity-bearing (write_recipe
    step 5). D-224: ``edge_target``/``edge_qualifier`` carry the captured edge's
    far endpoint + capability so the realization is self-contained."""
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
                edge_target=edge_target,
                edge_qualifier=edge_qualifier,
            ),
            AssertStep(
                step_id="assert-edge",
                predicate=AssertionPredicate(
                    subject_ref="read-subject", predicate=assert_predicate,
                    value=assert_value,
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


def _author_existence(g: GroundedExistence) -> EmissionBundle:
    """Author a config existence-claim (D-122): an inspection recipe that reads
    the subject's metadata and asserts it surfaces. Layer-1-complete, no caveat —
    a non-empty read IS the verification (D-079)."""
    subject_ref = IdentityBearingRef(
        entity_type=g.subject.entity_type, entity_id=g.subject.entity_id,
        version_seq=g.version_seq, external_id=g.subject.external_id,
    )
    claim = ExistenceClaimBody(subject=subject_ref)
    conditions = SemanticConditionsBody(conditions=[])
    trigger, recipe, env = _inspection_recipe(
        read_entity_type=g.subject.entity_type,
        read_external_id=g.subject.external_id,
        capture_field="sf_api_name",
        env_detail=f"read {g.subject.external_id} metadata to verify it exists",
    )
    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=requires_caveat(g.claim_kind),
        caveat_kind=caveat_kind(g.claim_kind),
    )


def _author_property(g: GroundedProperty) -> EmissionBundle:
    """Author a config property-claim (D-122): an inspection recipe that reads the
    subject's ``property_name`` and asserts it equals the S1-read value. Layer-1-
    complete, no caveat (D-079). The value was read from S1 at grounding, never
    invented — a NULL property asserts ``is_null``, else ``equals``."""
    subject_ref = IdentityBearingRef(
        entity_type=g.subject.entity_type, entity_id=g.subject.entity_id,
        version_seq=g.version_seq, external_id=g.subject.external_id,
    )
    expected = NullValue() if g.expected_value is None else LiteralValue(value=g.expected_value)
    claim = PropertyClaimBody(
        subject=subject_ref, property_name=g.property_name, expected_value=expected,
    )
    conditions = SemanticConditionsBody(conditions=[])
    if g.expected_value is None:
        assert_predicate, assert_value = "is_null", None
    else:
        assert_predicate, assert_value = "equals", g.expected_value
    trigger, recipe, env = _inspection_recipe(
        read_entity_type=g.subject.entity_type,
        read_external_id=g.subject.external_id,
        capture_field=g.property_name,
        env_detail=(f"read {g.subject.external_id}.{g.property_name} to verify "
                    f"it equals {g.expected_value!r}"),
        assert_predicate=assert_predicate, assert_value=assert_value,
    )
    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=requires_caveat(g.claim_kind),
        caveat_kind=caveat_kind(g.claim_kind),
    )


def _author_capability(g: GroundedCapability) -> EmissionBundle:
    """Author a permission capability-claim (D-123): an inspection recipe that
    reads the grantee's metadata and asserts the grant edge surfaces. Layer-1-
    complete, no caveat (D-079) — the configured grant IS the verification."""
    granter_ref = IdentityBearingRef(
        entity_type=g.granting_subject.entity_type, entity_id=g.granting_subject.entity_id,
        version_seq=g.version_seq, external_id=g.granting_subject.external_id,
    )
    target_ref = IdentityBearingRef(
        entity_type=g.target.entity_type, entity_id=g.target.entity_id,
        version_seq=g.version_seq, external_id=g.target.external_id,
    )
    claim = CapabilityClaimBody(
        granting_subject=granter_ref, target=target_ref,
        granted_capability=g.granted_capability, grant_type=g.grant_type,
    )
    conditions = SemanticConditionsBody(conditions=[])
    edge_type = "GRANTS_OBJECT_ACCESS" if g.grant_type == "object" else "GRANTS_FIELD_ACCESS"
    # D-224: the read carries the FULL scope (grantee + target + capability) and
    # asserts equals-true over the mapped permission flag — NOT exists: a
    # permissions row exists even when every flag is false.
    trigger, recipe, env = _inspection_recipe(
        read_entity_type=g.granting_subject.entity_type,
        read_external_id=g.granting_subject.external_id,
        capture_field=edge_type,
        env_detail=(f"read {g.granting_subject.external_id} grants to verify "
                    f"{g.granted_capability} on {g.target.external_id}"),
        assert_predicate="equals", assert_value=True,
        edge_target=LogicalRef(entity_type=g.target.entity_type,
                               external_id=g.target.external_id),
        edge_qualifier=g.granted_capability,
    )
    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=requires_caveat(g.claim_kind),
        caveat_kind=caveat_kind(g.claim_kind),
    )


def _author_layout(g: GroundedLayout) -> EmissionBundle:
    """Author a ui layout-claim (D-124): an inspection recipe that reads the
    layout's metadata and asserts the ``INCLUDES_FIELD`` placement surfaces.
    Layer-1-complete, no caveat (D-079) — the configured placement IS the
    verification. A **metadata-recipe, NOT a ui-recipe**: placement is a metadata
    fact, not a live UI interaction (the runtime render/enable question is
    ``element-state-claim``, Tier-3-deferred)."""
    layout_ref = IdentityBearingRef(
        entity_type=g.layout.entity_type, entity_id=g.layout.entity_id,
        version_seq=g.version_seq, external_id=g.layout.external_id,
    )
    field_ref = IdentityBearingRef(
        entity_type=g.field.entity_type, entity_id=g.field.entity_id,
        version_seq=g.version_seq, external_id=g.field.external_id,
    )
    claim = LayoutClaimBody(layout=layout_ref, field=field_ref)
    conditions = SemanticConditionsBody(conditions=[])
    # D-224: the read carries the placed Field as edge_target (membership is
    # presence — the exists assert is faithful here, unlike capability flags).
    trigger, recipe, env = _inspection_recipe(
        read_entity_type=g.layout.entity_type,
        read_external_id=g.layout.external_id,
        capture_field="INCLUDES_FIELD",
        env_detail=(f"read {g.layout.external_id} layout to verify "
                    f"{g.field.external_id} is placed on it"),
        edge_target=LogicalRef(entity_type=g.field.entity_type,
                               external_id=g.field.external_id),
    )
    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=requires_caveat(g.claim_kind),
        caveat_kind=caveat_kind(g.claim_kind),
    )


def _derive_update_violation(
    formulas: tuple[str, ...],
) -> Optional[VerifiedUpdateNegative]:
    """The update-shape gate (D-203): the first grounding VR formula derivable
    in BOTH directions — a non-violating setup state AND violating changes.
    Only comparisons qualify (NOT-ISPICKVAL / NOT-ISBLANK have no certain
    non-violating assignment); ``None`` → the caller's graded fallback
    (create-rejected when ``_derive_violation`` still succeeds, else caveated)."""
    for text in formulas:
        result = derive_update(parse(text))
        if isinstance(result, VerifiedUpdateNegative):
            return result
    return None


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


def _update_rejected_recipe(
    *, subject_entity_type: str, subject_external_id: str,
    setup_payload: dict, violating_changes: dict, env_detail: str,
) -> tuple[DataMutationTriggerBody, DataRecipeBody, ExecutionEnvironmentBody]:
    """Build the (trigger, recipe, env) triple for an **update-rejected**
    negative (D-203): a setup ``CreateStep`` carrying the derived non-violating
    state, then an ``UpdateStep`` carrying the violating changes +
    ``expect_rejection``. Field names are **object-qualified**
    (``{Object}.{field}`` — the positive vertical's convention) so S4's world
    construction treats them as the semantic fields: bare formula names would
    dodge the padding-exclusion and could be silently overwritten in the
    ``_sf_fields`` merge."""
    target = LogicalRef(
        entity_type=subject_entity_type, external_id=subject_external_id)

    def _qualified(payload: dict) -> dict:
        return {f"{subject_external_id}.{f}": v for f, v in payload.items()}

    trigger = DataMutationTriggerBody(
        operation="update", target=target,
        identity_context="system", volume="single",
    )
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[
            CreateStep(
                step_id="create-setup",
                target_object=target,
                field_values=_qualified(setup_payload),
            ),
            UpdateStep(
                step_id="update-violating",
                target=target,
                field_changes=_qualified(violating_changes),
                expect_rejection=RejectionExpectation(
                    error_code=_VR_REJECTION_ERROR_CODE),
            ),
        ],
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
    # Graded operation dispatch (D-203) over the verified-vs-caveated gate
    # (D-107). The derived payloads ride the RECIPE (operational), never the
    # claim — the claim body is byte-identical across ALL recipe shapes (the
    # Option-C identity_hash invariant; verified by a stability test).
    #
    #   modify_record / modify_field → try the UPDATE shape (setup +
    #     violating changes, both derivable); when only the violation derives,
    #     fall back to TODAY'S create-rejected (no regression — a state-only VR
    #     fires on insert too); when neither, caveated.
    #   create_duplicate → create-rejected (as today).
    #   delete / share / transfer_ownership → caveated inspection ALWAYS:
    #     VRs never fire on delete (and shares/transfers are not VR-rejectable
    #     creates) — a create-rejected recipe here would test the wrong
    #     operation (the pre-D-203 semantic blur, closed).
    update_pair = None
    violation = None
    if operation in ("modify_record", "modify_field"):
        update_pair = _derive_update_violation(g.vr_formulas)
        if update_pair is None:
            violation = _derive_violation(g.vr_formulas)
    elif operation == "create_duplicate":
        violation = _derive_violation(g.vr_formulas)
    verified = update_pair is not None or violation is not None
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

    # D-110.3 (S3-thin) + D-203: a VERIFIED negative emits the BEHAVIORAL
    # recipe — the 2-step update-rejected shape when both directions derived,
    # else the create-rejected (behavioral subsumes structural: it tests the VR
    # *enforces*). A CAVEATED negative (no derivable formula, or a
    # non-VR-testable operation) stays the INSPECTION re-verify (there is no
    # violation to construct). Replace, not augment (single-recipe; D-110.3).
    if update_pair is not None:
        trigger, recipe, env = _update_rejected_recipe(
            subject_entity_type=g.subject.entity_type,
            subject_external_id=g.subject.external_id,
            setup_payload=update_pair.setup_payload,
            violating_changes=update_pair.violating_changes,
            env_detail=(f"create a valid {g.subject.external_id} record, then "
                        f"update it into violation of the grounding validation "
                        f"rule (expect rejection)"),
        )
        trigger_kind, recipe_kind = "data-mutation-trigger", "data-recipe"
    elif verified:
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

    # D-228: a VERIFIED negative ALSO carries the caveated inspection
    # re-verify as a fallback SECONDARY (priority -10) — depth diversity per
    # environment: an env without data-API capability still verifies
    # plausibility. The claim's Layer-2 marker reflects the STRONGEST
    # realization; the secondary is a weaker realization of the same truth.
    secondaries = ()
    if verified:
        s_trigger, s_recipe, s_env = _inspection_recipe(
            read_entity_type=g.subject.entity_type,
            read_external_id=g.subject.external_id,
            capture_field="APPLIES_TO",
            env_detail=(f"fallback: read {g.subject.external_id} metadata to "
                        f"verify a validation rule applies (rejection "
                        f"plausibility — the behavioral recipe is primary)"),
        )
        secondaries = (SecondaryRecipe(
            trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
            causal_initiation=s_trigger, observation_realization=s_recipe,
            execution_environment=s_env, priority=-10),)

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
        secondary_recipes=secondaries,
    )


def _author_positive(g: GroundedPositive) -> EmissionBundle:
    """Author the positive create-and-verify bundle for a value-claim (D-115
    slice 1): a value-claim asserting ``field == V``, plus a **data** recipe that
    creates a record with the semantic field set to V, reads it back, and asserts
    the observed value equals V.

    The value V is carried **verbatim** from the grounding (the requirement-sourced
    ``expected_value``); the substrate never derives or invents it. The
    ``CreateStep`` carries **only the semantic field** — not a complete valid
    payload; S4 fills the operational required-field padding at execution (the k16
    boundary: S4 resolves operational validity, never the value under test)."""
    field_api = g.field.external_id
    object_api = g.target_object.external_id

    field_ref = IdentityBearingRef(
        entity_type=g.field.entity_type, entity_id=g.field.entity_id,
        version_seq=g.version_seq, external_id=field_api,
    )
    claim = ValueClaimBody(
        subject=field_ref, expected_value=LiteralValue(value=g.value),
    )
    # Directly-set value-claim is unconditional at this layer (no when-condition).
    conditions = SemanticConditionsBody(conditions=[])

    target = LogicalRef(entity_type=g.target_object.entity_type,
                        external_id=object_api)
    trigger = DataMutationTriggerBody(
        operation="create", target=target,
        identity_context="system", volume="single",
    )
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[
            CreateStep(
                step_id="create-record",
                target_object=target,
                # The SEMANTIC field only (k16) — S4 pads required fields at run.
                field_values={field_api: g.value},
            ),
            ReadStep(
                step_id="read-created",
                target=target,
                # Read the just-created record back. '$create-record.id' is the
                # cross-step reference to the create step's record Id; S4 resolves
                # the substitution at execution (the read-resolution mechanism is
                # side B's to define).
                soql=(f"SELECT {field_api} FROM {object_api} "
                      f"WHERE Id = '$create-record.id'"),
                fields_to_capture=[field_api],
            ),
            DataAssertStep(
                step_id="assert-value",
                predicate=AssertionPredicate(
                    subject_ref=f"read-created.{field_api}",
                    predicate="equals", value=g.value,
                ),
            ),
        ],
    )
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user",
        details=(f"create a {object_api} record with {field_api}={g.value!r}, "
                 f"read it back, assert the value persisted"),
    )])

    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        # value-claim is a Layer-1 positive (directly-set state; no Layer-2 marker).
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=requires_caveat(g.claim_kind),
        caveat_kind=caveat_kind(g.claim_kind),
    )


def _observe_steps(object_api: str, field_api: str, expected: Any,
                   *, create_fields: Optional[dict] = None) -> list:
    """The shared observe-the-org shape (D-210.1): create the subject WITHOUT
    the asserted field (the AUTOMATION must set it — contrast _author_positive,
    which sets the field directly), read it back, assert the org-produced
    value."""
    target = LogicalRef(entity_type="Object", external_id=object_api)
    return [
        CreateStep(
            step_id="create-record",
            target_object=target,
            # padding-only create (k16): the asserted field is deliberately
            # ABSENT — the org's automation is what must produce it.
            field_values=dict(create_fields or {}),
        ),
        ReadStep(
            step_id="read-created",
            target=target,
            soql=(f"SELECT {field_api} FROM {object_api} "
                  f"WHERE Id = '$create-record.id'"),
            fields_to_capture=[field_api],
        ),
        DataAssertStep(
            step_id="assert-value",
            predicate=AssertionPredicate(
                subject_ref=f"read-created.{field_api}",
                predicate="equals", value=expected,
            ),
        ),
    ]


def _author_state_transition(g: GroundedStateTransition) -> EmissionBundle:
    """Author the create-scoped state-transition bundle (D-210.1): the claim
    asserts the subject reaches ``to_state`` when created; the recipe creates
    the subject WITHOUT the to-state field, reads it back, and asserts the org
    set it. ``from_state`` is empty in v1 (unknown pre-state — the create IS
    the event). Caveated Layer-1: no Flow-formula derivation exists, so the
    engine cannot pre-verify the org will produce the transition (D-210 §4)."""
    field_api = g.field.external_id
    object_api = g.subject.external_id

    subject_ref = IdentityBearingRef(
        entity_type=g.subject.entity_type, entity_id=g.subject.entity_id,
        version_seq=g.version_seq, external_id=object_api)
    field_ref = IdentityBearingRef(
        entity_type=g.field.entity_type, entity_id=g.field.entity_id,
        version_seq=g.version_seq, external_id=field_api)
    # D-222: the staged trigger pair (when grounded) rides from_state — the
    # D-210.1 "unknown pre-state" reservation now holds the STAGED pre-state
    # — and the create sets it so the org's automation actually fires.
    subject_fields = [field_ref]
    from_values = {}
    create_fields = {}
    if g.trigger_field is not None:
        trigger_api = g.trigger_field.external_id
        subject_fields.append(IdentityBearingRef(
            entity_type=g.trigger_field.entity_type,
            entity_id=g.trigger_field.entity_id,
            version_seq=g.version_seq, external_id=trigger_api))
        from_values[trigger_api] = LiteralValue(value=g.trigger_value)
        create_fields[trigger_api] = g.trigger_value
    # D-227: the cross-object trigger — the transition is provoked by creating
    # a RELATED record, not the subject itself. The event description names
    # the trigger object (EventDescriptor stays prose; precise_trigger is
    # reserved, B-γ).
    event_desc = g.requirement_excerpt
    if g.trigger_object is not None:
        event_desc = (f"creating a {g.trigger_object.external_id} linked to "
                      f"the subject — {g.requirement_excerpt}")
    claim = StateTransitionClaimBody(
        subject=subject_ref,
        subject_fields=subject_fields,
        from_state=StateDescriptor(field_values=from_values),
        to_state=StateDescriptor(field_values={field_api: LiteralValue(value=g.to_value)}),
        triggering_event=EventDescriptor(
            trigger_kind="data-mutation-trigger",
            description=event_desc),
    )
    conditions = SemanticConditionsBody(conditions=[])

    target = LogicalRef(entity_type="Object", external_id=object_api)
    if g.trigger_object is not None:
        # D-227 cross-object shape: create the subject (padding ± the D-222
        # staged pair), create the TRIGGER record with its verified lookup
        # back to the subject, read the SUBJECT back, assert the to-state.
        # Runs on the D-205 N-create chain — the executor resolves
        # '$create-subject.id' in the trigger's field_values and the read.
        trigger_api = g.trigger_object.external_id
        lookup_api = g.trigger_lookup_field.external_id
        steps = [
            CreateStep(step_id="create-subject", target_object=target,
                       field_values=dict(create_fields)),
            CreateStep(
                step_id="create-trigger",
                target_object=LogicalRef(entity_type="Object",
                                         external_id=trigger_api),
                field_values={lookup_api: "$create-subject.id"}),
            ReadStep(
                step_id="read-subject", target=target,
                soql=(f"SELECT {field_api} FROM {object_api} "
                      f"WHERE Id = '$create-subject.id'"),
                fields_to_capture=[field_api]),
            DataAssertStep(
                step_id="assert-value",
                predicate=AssertionPredicate(
                    subject_ref=f"read-subject.{field_api}",
                    predicate="equals", value=g.to_value)),
        ]
        trigger_op_target = LogicalRef(entity_type="Object",
                                       external_id=trigger_api)
        details = (f"create a {object_api} record, create a {trigger_api} "
                   f"linked to it, read the {object_api} back, assert the "
                   f"org set {field_api}={g.to_value!r}")
    else:
        steps = _observe_steps(object_api, field_api, g.to_value,
                               create_fields=create_fields)
        trigger_op_target = target
        details = (f"create a {object_api} record (padding only), read it "
                   f"back, assert the org set {field_api}={g.to_value!r}")
    trigger = DataMutationTriggerBody(
        operation="create", target=trigger_op_target,
        identity_context="system", volume="single")
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=steps)
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user", details=details)])

    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=requires_caveat(g.claim_kind),
        caveat_kind=caveat_kind(g.claim_kind),
    )


def _author_automation_effect(g: GroundedAutomationEffect) -> EmissionBundle:
    """Author the automation-effect bundle (D-210.1). Same-record: the Flow
    stamps a field on the trigger record — observe-the-org shape on the
    subject. Cross-object: the Flow creates a correlated record — create the
    trigger, query the effect object via the VERIFIED lookup field, assert
    existence (or the asserted field). Caveated Layer-1 (D-210 §4)."""
    object_api = g.subject.external_id
    automation_ref = IdentityBearingRef(
        entity_type=g.automation.entity_type, entity_id=g.automation.entity_id,
        version_seq=g.version_seq, external_id=g.automation.external_id)
    event = EventDescriptor(trigger_kind="data-mutation-trigger",
                            description=g.requirement_excerpt)
    target = LogicalRef(entity_type="Object", external_id=object_api)

    if g.effect_object is None:
        # same-record: the Flow sets effect_field on the subject itself
        field_api = g.effect_field.external_id
        field_ref = IdentityBearingRef(
            entity_type=g.effect_field.entity_type, entity_id=g.effect_field.entity_id,
            version_seq=g.version_seq, external_id=field_api)
        claim = AutomationEffectClaimBody(
            automation=automation_ref, automation_primitive="flow",
            triggering_action=event,
            expected_effect=FieldChangeEffect(changes=StateDescriptor(
                field_values={field_api: LiteralValue(value=g.effect_value)})),
            affected_fields=[field_ref],
        )
        steps = _observe_steps(object_api, field_api, g.effect_value)
        details = (f"create a {object_api} record, read it back, assert the "
                   f"Flow set {field_api}={g.effect_value!r}")
    elif g.effect_via_lookup_field is not None:
        # D-227 parent-stamp: the Flow stamps a record the TRIGGER record
        # points to via its own lookup. Create the parent FIRST (so its id is
        # known — no relationship-traversal read), create the trigger with the
        # lookup set, read the PARENT back. Value-less stamps assert not_null
        # (e.g. $Flow.CurrentDate has no stable literal). Runs on the D-205
        # N-create chain.
        effect_api = g.effect_object.external_id
        via_api = g.effect_via_lookup_field.external_id
        field_api = g.effect_field.external_id
        field_bare = field_api.split(".", 1)[-1]
        field_ref = IdentityBearingRef(
            entity_type=g.effect_field.entity_type,
            entity_id=g.effect_field.entity_id,
            version_seq=g.version_seq, external_id=field_api)
        if g.effect_value is not None:
            effect = FieldChangeEffect(changes=StateDescriptor(
                field_values={field_api: LiteralValue(value=g.effect_value)}))
            assert_pred = AssertionPredicate(
                subject_ref=f"read-effect.{field_bare}",
                predicate="equals", value=g.effect_value)
            stamp_desc = f"{field_bare}={g.effect_value!r}"
        else:
            effect = FieldChangeEffect(changes=StateDescriptor(field_values={}))
            assert_pred = AssertionPredicate(
                subject_ref=f"read-effect.{field_bare}",
                predicate="not_null")
            stamp_desc = f"{field_bare} (some value — the stamp has no stable literal)"
        claim = AutomationEffectClaimBody(
            automation=automation_ref, automation_primitive="flow",
            triggering_action=event, expected_effect=effect,
            affected_fields=[field_ref],
        )
        steps = [
            CreateStep(step_id="create-parent",
                       target_object=LogicalRef(entity_type="Object",
                                                external_id=effect_api),
                       field_values={}),
            CreateStep(step_id="create-record", target_object=target,
                       field_values={via_api: "$create-parent.id"}),
            ReadStep(
                step_id="read-effect",
                target=LogicalRef(entity_type="Object", external_id=effect_api),
                soql=(f"SELECT {field_bare} FROM {effect_api} "
                      f"WHERE Id = '$create-parent.id'"),
                fields_to_capture=[field_bare]),
            DataAssertStep(step_id="assert-effect", predicate=assert_pred),
        ]
        details = (f"create a {effect_api} parent, create a {object_api} "
                   f"linked to it, read the parent back, assert the Flow "
                   f"stamped {stamp_desc}")
    else:
        # cross-object: the Flow creates a correlated effect record
        effect_api = g.effect_object.external_id
        lookup_api = g.effect_lookup_field.external_id
        # the lookup is bare-keyed in SOQL on the effect object
        lookup_bare = lookup_api.split(".", 1)[-1]
        if g.effect_field is not None:
            field_api = g.effect_field.external_id
            field_bare = field_api.split(".", 1)[-1]
            field_ref = IdentityBearingRef(
                entity_type=g.effect_field.entity_type,
                entity_id=g.effect_field.entity_id,
                version_seq=g.version_seq, external_id=field_api)
            effect = FieldChangeEffect(changes=StateDescriptor(
                field_values={field_api: LiteralValue(value=g.effect_value)}))
            affected = [field_ref]
            select = f"SELECT Id, {field_bare} FROM {effect_api}"
            assert_pred = AssertionPredicate(
                subject_ref=f"read-effect.{field_bare}",
                predicate="equals", value=g.effect_value)
            details = (f"create a {object_api} record, query {effect_api} via "
                       f"{lookup_bare}, assert the Flow-created record carries "
                       f"{field_bare}={g.effect_value!r}")
        else:
            effect = FieldChangeEffect(changes=StateDescriptor(field_values={}))
            affected = []
            select = f"SELECT Id FROM {effect_api}"
            assert_pred = AssertionPredicate(
                subject_ref="read-effect.Id", predicate="exists", value=None)
            details = (f"create a {object_api} record, query {effect_api} via "
                       f"{lookup_bare}, assert the Flow created a correlated record")
        claim = AutomationEffectClaimBody(
            automation=automation_ref, automation_primitive="flow",
            triggering_action=event, expected_effect=effect,
            affected_fields=affected,
        )
        steps = [
            CreateStep(step_id="create-record", target_object=target,
                       field_values={}),
            ReadStep(
                step_id="read-effect",
                target=LogicalRef(entity_type="Object", external_id=effect_api),
                soql=f"{select} WHERE {lookup_bare} = '$create-record.id'",
                fields_to_capture=(["Id"] if g.effect_field is None
                                   else ["Id", field_bare]),
            ),
            DataAssertStep(step_id="assert-effect", predicate=assert_pred),
        ]

    conditions = SemanticConditionsBody(conditions=[])
    trigger = DataMutationTriggerBody(
        operation="create", target=target,
        identity_context="system", volume="single")
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api", steps=steps)
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user", details=details)])

    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=requires_caveat(g.claim_kind),
        caveat_kind=caveat_kind(g.claim_kind),
    )


def author_emission(grounded: object) -> EmissionBundle:
    """Author the claim + recipe bodies for a grounded candidate. The single
    site that constructs S2 body models for generation (D-097.5); dispatches on
    the grounding shape."""
    if isinstance(grounded, GroundedEmission):
        return _author_config(grounded)
    if isinstance(grounded, GroundedExistence):
        return _author_existence(grounded)
    if isinstance(grounded, GroundedProperty):
        return _author_property(grounded)
    if isinstance(grounded, GroundedCapability):
        return _author_capability(grounded)
    if isinstance(grounded, GroundedLayout):
        return _author_layout(grounded)
    if isinstance(grounded, GroundedNegative):
        return _author_negative(grounded)
    if isinstance(grounded, GroundedPositive):
        return _author_positive(grounded)
    if isinstance(grounded, GroundedStateTransition):
        return _author_state_transition(grounded)
    if isinstance(grounded, GroundedAutomationEffect):
        return _author_automation_effect(grounded)
    raise TypeError(f"author_emission: unsupported grounding {type(grounded).__name__}")

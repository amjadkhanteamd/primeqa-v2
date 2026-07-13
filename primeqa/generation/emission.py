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

import html
import re
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Union
from uuid import UUID

from primeqa.generation.enums import AdmissibilityLayer, CaveatKind
from primeqa.generation.fixture import (
    FixtureUnsat, ROLE_CONTEXT, ROLE_TARGET_ACTIVATION, ROLE_TARGET_WITNESS,
    complete_accept_fixture,
)
from primeqa.generation.decision_branch import satisfy_decision_branches
from primeqa.generation.transition import (
    derive_prior_state_control, has_transition_semantics, satisfy_transition,
    satisfy_transition_acceptance, satisfy_temporal_boundary,
)
from primeqa.generation.typed_value import transport_payload as _to_transport_payload
from primeqa.generation.semantic_completeness import caveat_kind, requires_caveat
from primeqa.generation.verified_negative import (
    VerifiedNegative,
    VerifiedUpdateNegative,
    derive,
    derive_boundary_set,
    derive_context_control,
    derive_update,
)
from primeqa.semantic.formula import parse
from primeqa.test_representation.temporal import relative_date_offset
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
    ProhibitionClaimArcBody,
    ProhibitionClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.acceptance_claim import (
    AcceptanceClaimArcBody,
    AcceptanceClaimBody,
    AcceptanceClaimUpdateBody,
)
from primeqa.test_representation.models.claims.data_behavior.automation_effect_claim import (
    AutomationAbsenceClaimBody,
    AutomationEffectClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.state_transition_claim import (
    StateTransitionClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.value_claim import (
    ValueClaimBody,
)
from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.conditions import (
    Condition,
    ConditionV2,
    SemanticConditionsBody,
    SemanticConditionsBodyV2,
)
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
    SideEffect,
    StateDescriptor,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    ApprovalActionStep,
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
    ("data_behavior", "prohibition-claim"),              # D-101
    ("data_behavior", "acceptance-claim"),               # D-305 (GroundedNegative)
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
class _GroundedCondition:
    """A grounded business-state clause for a prohibition's identity-bearing
    semantic_conditions (D-293, Option A). ``field`` is the S1-resolved subject of
    the clause (governance fills the ``_Endpoint``); ``predicate``/``value`` follow
    the S2 ``Condition`` taxonomy (value-free predicates carry ``value=None``).
    D-330: a cross-field comparison clause (``exceeds``) carries the right-hand
    field as ``compared_to`` (S1-resolved) and no ``value``."""

    field: _Endpoint
    predicate: str
    value: Any = None
    compared_to: Optional[_Endpoint] = None


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
    # D-293: grounded business-state clauses (Option A — LLM-proposed, governance-
    # verified). Authored into the claim's semantic_conditions so distinct
    # conditions yield distinct identity_hashes (no AC1/2/4 collapse). Empty (the
    # default) -> conditions=[], byte-identical to pre-D-293 (DORMANT until the
    # intent contract + grounding arm it in a later slice).
    conditions: tuple[_GroundedCondition, ...] = ()
    # D-294: read-only S1 field metadata for violation-derivation breadth, keyed
    # by BARE field api name (as the formula parser / recipe speak). Each value:
    # {field_type, length, is_calculated, picklist_values|None}. Governance fills
    # it at grounding; `derive`/`derive_update` consume it to synthesize the
    # violating value for non-numeric shapes (cross-field / NOT-ISBLANK /
    # NOT-ISPICKVAL / bare-boolean). Empty (the default) -> derivation refuses
    # exactly as today (the certainty bar). DORMANT until a later slice arms the
    # derive branches; threaded-but-ignored here.
    field_metadata: dict = field(default_factory=dict)
    # D-297 (lever 5): {VR formula_text -> the VR's user-facing error message},
    # read from S1 at grounding (governance `_grounding_vr_messages`). DORMANT here —
    # emission does not read it yet; slice 5.2 looks up the DERIVED source formula's
    # message and projects it into RejectionExpectation.error_message_pattern so the
    # S4 grade confirms WHICH rule fired. Empty (the default) -> no pattern ->
    # byte-identical. Not persisted (transient), not an identity_hash input.
    vr_messages: dict = field(default_factory=dict)
    # D-333: the approval-action arc. Non-empty -> the ARC author (v2 body;
    # staged-conditions create -> actions -> the explicit rejected update);
    # () (the default) -> every pre-D-333 prohibition byte-identical.
    approval_actions: tuple = ()
    # D-333: the explicit update the org must REJECT after the arc —
    # ``(_Endpoint, value)``. REQUIRED when approval_actions is non-empty
    # (governance refuses otherwise); ignored on the non-arc path.
    attempted_change: Optional[tuple] = None


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
    # Completion E2 (update_records evidence): the PRE-STAGED correlated
    # rows the producing flow will update — {"count", "template"
    # ((field, value)...), "distractor" (field, value), "updated_value"}.
    # The recipe creates the subject in the NOT-entry state, stages count
    # matching children + ONE distractor, updates the subject INTO the
    # entry state, then reads WHERE correlate AND field=updated_value and
    # asserts count_equals(count) — over- and under-update both fail.
    premise_children: Optional[dict] = None
    # Completion E3 (roll-up evidence): the SUBJECT-as-parent aggregate —
    # a flow triggered on a CHILD object aggregates the sibling set and
    # stamps the result on the subject. {"child_object", "lookup" (bare),
    # "count", "staged" ((field, value)... per child row), "fn"
    # ("Count"|"Sum"), "expected"}. The recipe creates the subject, stages
    # ``count`` children under it + ONE child under a SECOND parent (the
    # correlation distractor), reads the subject back and asserts
    # effect_field == expected — a mis-correlated rollup (the distractor
    # leaking in) and a dead rollup both fail the same assert.
    rollup_spec: Optional[dict] = None
    # Completion (FL06 slice — premise-conditioned same-record effect): the
    # SIBLING row whose existence makes the flow's arm fire — {"staged"
    # ((bare_field, value)...), "correlation" (sibling_field,
    # subject_field, witness)}. The recipe creates the sibling FIRST, then
    # the subject sharing the correlation witness, reads the subject back
    # and asserts the flag — without the sibling the arm provably cannot
    # fire, so a green means the premise query discriminated.
    premise_sibling: Optional[dict] = None
    # D-299: the OPTIONAL entry-condition trigger — the (field, value) pairs the
    # create must SET so the Flow's entry gate actually fires (the risk-rating
    # flow gates on StageName='Credit Assessment' AND the KYC/Credit-Score fields
    # its Credit_Assessment_Prerequisites VR forces present). Each _Endpoint is
    # a Field verified BELONGS_TO the SUBJECT at the stash gate; requirement-
    # sourced values (verbatim). Empty () → today's shallow observe-the-org shape
    # exactly (the create sets nothing, so the flow never fires — observability,
    # not correctness). Multi-field because one entry gate is not enough.
    trigger_fields: tuple = ()  # tuple[tuple[_Endpoint, Any], ...]
    # D-304: the Salesforce mechanism (the claim body's sub-discriminator).
    # "flow" = the D-210 default (a Flow TRIGGERS_ON the subject); "formula" =
    # the automation ref is a CALCULATED FIELD and trigger_fields carry the
    # formula's inputs. Default keeps every pre-D-304 construction byte-identical.
    automation_primitive: str = "flow"
    # D-306: the OPTIONAL update-observe phase — the (field, value) pairs an
    # UPDATE stages AFTER the create, so the org RE-computes the effect (the
    # recalculates-on-change half of automations/formulas: create at the
    # initial trigger_fields state → change these → observe the new effect
    # value). Same idiom as trigger_fields (BELONGS_TO-verified, k16 exclude —
    # the observed field can never be in EITHER trigger set; the stash gate
    # refuses it on non-same-record shapes). Empty () → the create-scoped
    # shape, byte-identical.
    update_trigger_fields: tuple = ()  # tuple[tuple[_Endpoint, Any], ...]
    # FL02 slice (IR v2): the TRANSFORM shape — the Flow REWRITES the effect
    # field from a staged raw input (before-save, so the transformed value is
    # what validation rules and the read-back observe). When
    # ``transform_chain`` is non-empty the author stages
    # ``transform_staged_value`` ON the effect field itself (the deliberate,
    # documented k16 exception: staged != expected, so the test can never be
    # self-fulfilling — if the flow does not run, read-back returns the raw
    # value and the assert fails honestly) and asserts ``effect_value`` (the
    # substrate-computed post-transform canonical). Empty chain → every
    # pre-FL02 construction byte-identical.
    transform_chain: tuple = ()            # application order, e.g. ("TRIM","UPPER")
    transform_staged_value: Any = None     # the raw witness staged on effect_field
    transform_source_field: Optional[str] = None   # bare field the chain reads
    # D-307: the ABSENCE case — "under this staged state the automation
    # correctly produces NO correlated record" (TC-038/039: Medium/Low band →
    # no follow-up Task). Cross-object shape ONLY (effect_object +
    # effect_lookup_field, no effect_field — the stash gate refuses every
    # other combination: silently authoring presence would invert the claim's
    # meaning). Authors the v2 body + a not_exists assert. False → presence,
    # byte-identical.
    expected_absence: bool = False


@dataclass(frozen=True)
class GroundedAcceptance:
    """data_behavior acceptance-claim grounding (D-305): the subject Object +
    the GROUNDED business-state clauses (each field verified BELONGS_TO the
    subject + writable at the stash gate; equals/is_null predicates only —
    the stageable set). The clauses are IDENTITY-BEARING (they ride
    semantic_conditions): an acceptance case is DEFINED by its values, so
    just-below and at-threshold are distinct claims."""

    archetype: str              # "data_behavior"
    claim_kind: str             # "acceptance-claim"
    version_seq: int
    subject: _Endpoint          # the Object being created
    requirement_excerpt: str
    conditions: tuple = ()      # grounded clauses (field ep, predicate, value)
    # D-306: the OPTIONAL update phase — grounded EQUALS clauses the recipe
    # stages on a positive UpdateStep AFTER the initial-state create (the
    # stage-progress case: "given KYC + score, progressing to Credit
    # Assessment succeeds"). Non-empty → the v2 update body: the destination
    # rides the BODY's update_state (identity-bearing, phase- and
    # direction-distinct by construction — D-306.1) while `conditions`
    # carries ONLY the initial state on the conditions layer. Empty () → the
    # D-305 create shape verbatim.
    update_conditions: tuple = ()
    # D-333: the approval-action arc — non-empty (with a non-empty
    # update_conditions, governance-enforced) authors the v3 arc body: the
    # actions run against the created record BEFORE the accepted update.
    # () (the default) -> every pre-D-333 acceptance byte-identical.
    approval_actions: tuple = ()


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


# D-300.1: boundary accept probes sit strictly BELOW the primary (single mode
# deterministically prefers the reject primary — no recipe_id tie-break can
# select an accept probe as a prohibition's single realization, the wrong-green
# vector) and strictly ABOVE the -10 inspection fallback.
_BOUNDARY_PROBE_PRIORITY = -1


@dataclass(frozen=True)
class BoundaryRecipe:
    """One member of a D-300 bva boundary set — a PROBE the run-all batch
    strict-ANDs into the claim's Verified verdict (contrast
    :class:`SecondaryRecipe`, a weaker FALLBACK realization that must never
    enter a boundary verdict). Same write_recipe fields; priority is
    :data:`_BOUNDARY_PROBE_PRIORITY` by construction (D-300.1). The edge label
    ("just-inside") rides the env detail text — diagnostics for humans; the
    verdict never consumes it."""

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
    # v1, or v2 when a cross-field clause rides the conditions (D-330)
    semantic_conditions: Union[SemanticConditionsBody, SemanticConditionsBodyV2]
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
    # the router/decision read None → single, byte-identical). The D-300 S3 author
    # stamps 'bva' here (flag-gated, §4a settled narrow in D-300); persistence reads
    # it through to write_claim.
    strategy_kind: Optional[str] = None
    # D-300: the bva boundary PROBES of this claim — each its own recipe row
    # under the same claim (Option-C: recipes are outside the identity hash).
    # A dedicated slot, NEVER secondary_recipes (the D-228 inspection fallback
    # must not enter a boundary strict-AND). Empty () = no boundary set (every
    # claim today); populated ONLY alongside strategy_kind='bva' (S3).
    boundary_recipes: tuple = ()


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
    formulas: tuple[str, ...], field_metadata: Optional[dict] = None,
) -> tuple[Optional[VerifiedUpdateNegative], Optional[str]]:
    """The update-shape gate (D-203): the first grounding VR formula derivable
    in BOTH directions — a non-violating setup state AND violating changes.
    Only comparisons qualify (NOT-ISPICKVAL / NOT-ISBLANK have no certain
    non-violating assignment); ``(None, None)`` → the caller's graded fallback
    (create-rejected when ``_derive_violation`` still succeeds, else caveated).
    ``field_metadata`` (D-294) is threaded to ``derive_update`` for the
    metadata-driven non-numeric shapes; absent -> today's behaviour. **D-297:**
    also returns the SOURCE formula text (the VR the recipe actually violates) so
    the caller can bind that VR's error message (never an arbitrary aligned one)."""
    for text in formulas:
        result = derive_update(parse(text), field_metadata)
        if isinstance(result, VerifiedUpdateNegative):
            return result, text
    return None, None


def _derive_violation(
    formulas: tuple[str, ...], field_metadata: Optional[dict] = None,
) -> tuple[Optional[VerifiedNegative], Optional[str]]:
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
    holds (the claim body is byte-identical whether verified or caveated).

    **D-297:** also returns the SOURCE formula text (the VR whose violation the
    recipe carries) so the caller binds THAT VR's error message, not an aligned
    one."""
    for text in formulas:
        result = derive(parse(text), field_metadata)
        if isinstance(result, VerifiedNegative):
            return result, text
    return None, None


class BehaviourIncomplete(RuntimeError):
    """D-293: a prohibition reached authoring with no derivable behavioural
    reject recipe. Governance's :func:`prohibition_recipe_derivable` gate must
    refuse such intents BEFORE emission (refuse, never degrade); this guards the
    invariant so emission can never fall back to the caveated inspection."""


def _derive_prohibition_recipe(
    operation_hint: Optional[str], vr_formulas: tuple[str, ...],
    field_metadata: Optional[dict] = None,
    sibling_vr_items=None,
) -> tuple[str, Optional[VerifiedUpdateNegative], Optional[VerifiedNegative], Optional[str]]:
    """Bind the prohibition operation and derive its behavioural reject recipe —
    the SINGLE source of truth shared by :func:`_author_negative` (which needs the
    derived payloads) and :func:`prohibition_recipe_derivable` (the D-293 gate,
    which needs only the yes/no). Returns ``(operation, update_pair, violation,
    source_formula)``; the behaviour instance is complete iff ``update_pair`` or
    ``violation`` is set. ``source_formula`` (D-297) is the VR-formula text the
    recipe actually violates (for message-pattern binding), or ``None``.

      modify_record / modify_field → UPDATE shape (setup + violating changes);
        when only the violation derives, create-rejected (a state-only VR fires on
        insert too); when neither, nothing (incomplete).
      create_duplicate → create-rejected.
      delete / share / transfer_ownership → nothing: VRs never fire on delete, and
        shares/transfers are not VR-rejectable creates (the pre-D-203 blur, closed).
    """
    operation = (operation_hint if operation_hint in _PROHIBITION_OPERATIONS
                 else _DEFAULT_OPERATION)
    update_pair: Optional[VerifiedUpdateNegative] = None
    violation: Optional[VerifiedNegative] = None
    source: Optional[str] = None
    if operation in ("modify_record", "modify_field"):
        # VR10 arc: a TRANSITION-shaped formula (org-state functions) realizes
        # as setup + transition-update via the explicit transition IR — the
        # single-formula derive/derive_update paths keep refusing org-state
        # (their epistemics are single-phase), so this is strictly additive.
        # The witness violates EXACTLY ONE approval branch, satisfies the rest,
        # and (when sibling items are threaded) silences sibling controls with
        # provenance-classified fills.
        for text in vr_formulas:
            if not has_transition_semantics(text):
                continue
            w = satisfy_transition(
                text, field_metadata,
                # the TARGET is not its own sibling — the witness fires it
                sibling_items=[(n, t) for n, t in (sibling_vr_items or ())
                               if t != text])
            if w is not None:
                update_pair = VerifiedUpdateNegative(
                    setup_payload=w.setup, violating_changes=w.changes,
                    provenance=w.provenance,
                    entry_changes=(w.entry_changes or None))
                source = text
                break
        if update_pair is None:
            # VR06 arc: the TEMPORAL-BOUNDARY shape (a gate + the blank/past
            # date disjunction). The primary IS the blank arm — the date is
            # staged by ABSENCE (the ISBLANK branch); the remaining three arms
            # (past-reject + the adjacent today/tomorrow accepts) ride as
            # probes carrying replay-stable RelativeDate values.
            for text in vr_formulas:
                exp = satisfy_temporal_boundary(
                    text, field_metadata,
                    sibling_items=sibling_vr_items)
                if exp is not None:
                    update_pair = VerifiedUpdateNegative(
                        setup_payload=exp.setup_base,
                        violating_changes=exp.changes,
                        provenance=exp.provenance,
                        temporal=exp)
                    source = text
                    break
        if update_pair is None:
            # VR03 arc: DecisionBranchCoverage — the bounded
            # AND(gates, OR(branches)) decision shape. The primary IS the
            # first isolated firing arm (create-rejected); the second firing
            # arm + the gate necessity controls ride as probes.
            for text in vr_formulas:
                dexp = satisfy_decision_branches(
                    text, field_metadata, sibling_items=sibling_vr_items)
                if dexp is not None:
                    violation = VerifiedNegative(
                        violating_payload=dexp.arms[0].payload,
                        decision=dexp)
                    source = text
                    break
        if update_pair is None and violation is None:
            update_pair, source = _derive_update_violation(
                vr_formulas, field_metadata)
        if update_pair is None and violation is None:
            violation, source = _derive_violation(vr_formulas, field_metadata)
    elif operation == "create_duplicate":
        violation, source = _derive_violation(vr_formulas, field_metadata)
    return operation, update_pair, violation, source


def prohibition_recipe_derivable(
    operation_hint: Optional[str], vr_formulas: tuple[str, ...],
    field_metadata: Optional[dict] = None,
    sibling_vr_items=None,
) -> bool:
    """D-293 completeness predicate (the governance gate): does this prohibition
    yield a BEHAVIOURAL reject recipe? ``False`` → incomplete behaviour instance →
    governance refuses (no degrade to the caveated inspection). ``field_metadata``
    (D-294) widens what derives; absent -> today's behaviour. ``sibling_vr_items``
    (VR10 arc) arms the transition path's sibling isolation — threading it here
    keeps the gate's yes/no consistent with what authoring will actually produce
    (an un-isolatable transition negative refuses at governance, never crashes at
    emission)."""
    _, update_pair, violation, _ = _derive_prohibition_recipe(
        operation_hint, vr_formulas, field_metadata,
        sibling_vr_items=sibling_vr_items)
    return update_pair is not None or violation is not None


def _behavioral_recipe(
    *, subject_entity_type: str, subject_external_id: str,
    violating_payload: dict, env_detail: str, error_message: Optional[str] = None,
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
                error_code=_VR_REJECTION_ERROR_CODE,
                error_message_pattern=(
                    re.escape(error_message) if error_message else None)),
        )],
    )
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user", details=env_detail,
    )])
    return trigger, recipe, env


def _update_rejected_recipe(
    *, subject_entity_type: str, subject_external_id: str,
    setup_payload: dict, violating_changes: dict, env_detail: str,
    error_message: Optional[str] = None,
    entry_changes: Optional[dict] = None,
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
    steps = [
        CreateStep(
            step_id="create-setup",
            target_object=target,
            field_values=_qualified(setup_payload),
        ),
    ]
    if entry_changes:
        # VR05 arc: the prior state is GATED — establish it through the org's
        # own transition (expected to SUCCEED, D-306) before the mutation
        # under test; a direct create into the state would bypass the gate.
        steps.append(UpdateStep(
            step_id="update-entry",
            target=target,
            field_changes=_qualified(entry_changes),
            expect_acceptance=True,
        ))
    steps.append(UpdateStep(
        step_id="update-violating",
        target=target,
        field_changes=_qualified(violating_changes),
        expect_rejection=RejectionExpectation(
            error_code=_VR_REJECTION_ERROR_CODE,
            error_message_pattern=(
                re.escape(error_message) if error_message else None)),
    ))
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=steps,
    )
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user", details=env_detail,
    )])
    return trigger, recipe, env


def _boundary_accept_recipe(
    *, subject_entity_type: str, subject_external_id: str,
    payload: dict, edge: str, boundary_field: Optional[str] = None,
    fixture_provenance: Optional[dict] = None,
) -> tuple[DataMutationTriggerBody, DataRecipeBody, ExecutionEnvironmentBody]:
    """Build the (trigger, recipe, env) triple for a D-300 **just-inside accept
    probe**: create the subject AT the boundary-adjacent non-firing value and
    expect the create to SUCCEED (no VR fires), then read the field back and
    assert OUR OWN payload value round-trips (the shipped positive-vertical
    shape — never a fabricated org value; D-115). Payload keys are qualified
    ``{Object}.{field}`` (the positive vertical's convention) so S4's world
    padding treats them as semantic fields and can never silently overwrite the
    boundary value — a padding overwrite would be a wrong-green. The ``edge``
    label rides the env detail (human diagnostics; the verdict never reads it)."""
    target = LogicalRef(
        entity_type=subject_entity_type, external_id=subject_external_id)
    if boundary_field is not None:
        # D-328 gated member: the payload also stages the gate fields (e.g.
        # VR08's RecordTypeId) — the boundary field is named, not inferred.
        field_bare, value = boundary_field, payload[boundary_field]
    else:
        (field_bare, value), = payload.items()   # bare single-threshold member
    field_qualified = f"{subject_external_id}.{field_bare}"

    def _qualify(f: str) -> str:
        # The positive vertical's qualified convention (S4 bare-ifies at the API
        # boundary, world.py; qualification marks the field as authoring-staged
        # so padding can never silently overwrite it — a wrong-green vector).
        return f"{subject_external_id}.{f.rsplit('.', 1)[-1]}"

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
                # the boundary value + the D-328 gates that make the accept
                # meaningful (k16: S4 pads only what authoring did NOT stage)
                field_values={_qualify(f): v for f, v in payload.items()},
            ),
            ReadStep(
                step_id="read-created",
                target=target,
                soql=(f"SELECT {field_qualified} FROM {subject_external_id} "
                      f"WHERE Id = '$create-record.id'"),
                fields_to_capture=[field_qualified],
            ),
            DataAssertStep(
                step_id="assert-value",
                predicate=AssertionPredicate(
                    subject_ref=f"read-created.{field_qualified}",
                    predicate="equals", value=value,
                ),
            ),
        ],
    )
    if edge == "context-control":
        detail = (f"context-differential control ({edge}): create a "
                  f"{subject_external_id} record with {field_bare}={value!r} — "
                  f"the SAME above-boundary value the in-context arm rejects — "
                  f"under the ALTERNATIVE record classification, and expect the "
                  f"create to SUCCEED: the accept↔reject delta at one varied "
                  f"context dimension proves the rule is context-scoped")
    else:
        detail = (f"bva boundary probe ({edge}): create a "
                  f"{subject_external_id} record with {field_bare}={value!r} — "
                  f"the boundary-adjacent NON-firing value — and expect the "
                  f"create to SUCCEED (no validation rule fires)")
    if fixture_provenance:
        # The durable record of WHY each field is staged (AK Option-1 scope):
        # target witness / target activation / context / sibling isolation.
        parts = [f"{f}={payload.get(f)!r} [{role}: {str(src)[:60]}]"
                 for f, (role, src) in sorted(fixture_provenance.items())]
        detail += "; fixture provenance: " + "; ".join(parts)
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user", details=detail,
    )])
    return trigger, recipe, env


def author_boundary_recipes(
    *, subject_entity_type: str, subject_external_id: str, members: tuple,
) -> tuple:
    """Map a D-300 boundary set (:class:`BoundaryMember` tuple from
    ``derive_boundary_set``) to :class:`BoundaryRecipe` carriers. Only the
    NON-firing (accept) members author here — the firing member IS the bundle's
    primary reject recipe (its payload equals ``derive()``'s, the unit-pinned
    invariant), so authoring it again would double-execute the same probe.
    Empty in → empty out (the dormant default). DORMANT until D-300 S3 calls
    this from the flag-gated ``_author_negative`` path."""
    out = []
    for m in members:
        if m.expect_reject:
            continue                        # the firing member is the primary
        trigger, recipe, env = _boundary_accept_recipe(
            subject_entity_type=subject_entity_type,
            subject_external_id=subject_external_id,
            payload=m.payload, edge=m.edge,
            boundary_field=getattr(m, "boundary_field", None),
            fixture_provenance=getattr(m, "fixture_provenance", None),
        )
        out.append(BoundaryRecipe(
            trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
            causal_initiation=trigger, observation_realization=recipe,
            execution_environment=env, priority=_BOUNDARY_PROBE_PRIORITY,
        ))
    return tuple(out)


def _transition_accept_probe(
    *, subject_entity_type: str, subject_external_id: str,
    witness, field_metadata: Optional[dict],
    differential_note: Optional[str] = None,
) -> BoundaryRecipe:
    """The T3 positive as a probe on the transition-prohibition claim: create
    the witness's setup (every violation branch falsified, gates + siblings
    satisfied), apply the transition update expecting SUCCESS (D-306
    ``expect_acceptance`` — a business rejection grades failed), then read the
    record back and assert the to-state persisted (AK: require update success
    AND read-back of the transitioned value). Fixture provenance rides the env
    detail. Priority −1 — a data probe the bva batch strict-ANDs with the
    primary reject (D-300.1)."""
    target = LogicalRef(
        entity_type=subject_entity_type, external_id=subject_external_id)

    def _q(f: str) -> str:
        return f"{subject_external_id}.{f.rsplit('.', 1)[-1]}"

    setup = _to_transport_payload(witness.setup, field_metadata)
    changes = _to_transport_payload(witness.changes, field_metadata)
    steps = [
        CreateStep(
            step_id="create-setup", target_object=target,
            field_values={_q(f): v for f, v in setup.items()},
        ),
        UpdateStep(
            step_id="update-transition", target=target,
            field_changes={_q(f): v for f, v in changes.items()},
            expect_acceptance=True,
        ),
    ]
    asserts = []
    for f, v in sorted(changes.items()):
        fq = _q(f)
        steps.append(ReadStep(
            step_id=f"read-{f.rsplit('.', 1)[-1].lower()}", target=target,
            soql=(f"SELECT {fq} FROM {subject_external_id} "
                  f"WHERE Id = '$create-setup.id'"),
            fields_to_capture=[fq],
        ))
        steps.append(DataAssertStep(
            step_id=f"assert-{f.rsplit('.', 1)[-1].lower()}",
            predicate=AssertionPredicate(
                subject_ref=f"read-{f.rsplit('.', 1)[-1].lower()}.{fq}",
                predicate="equals", value=v),
        ))
        asserts.append(f"{fq}={v!r}")
    trigger = DataMutationTriggerBody(
        operation="update", target=target,
        identity_context="system", volume="single",
    )
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api", steps=steps,
    )
    detail = (f"transition accept probe (the inverse experiment): create a "
              f"{subject_external_id} record with every violation branch "
              f"falsified and all sibling controls satisfied, apply the "
              f"transition update and expect the org to ACCEPT it, then read "
              f"back and assert {', '.join(asserts)} persisted")
    if differential_note:
        detail += f"; {differential_note}"
    if witness.provenance:
        parts = [f"{f} [{role}: {str(src)[:60]}]"
                 for f, (role, src) in sorted(witness.provenance.items())]
        detail += "; fixture provenance: " + "; ".join(parts)
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user", details=detail,
    )])
    return BoundaryRecipe(
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env, priority=_BOUNDARY_PROBE_PRIORITY,
    )


def _author_acceptance(g: GroundedAcceptance) -> EmissionBundle:
    """Author the acceptance bundle (D-305): the claim asserts the org ACCEPTS
    creating the subject under the grounded business state; the recipe stages
    that state on ONE multi-field create (``expect_acceptance=True`` — the
    create IS the assertion), reads the record back by id, and asserts it
    exists. Object-qualified keys (the positive vertical's convention). Every
    ``equals`` clause stages its value; ``is_null`` clauses stay ABSENT (the
    case is partly defined by what is NOT set — the negative-scope shape).

    D-306 (``update_conditions`` non-empty → ``operation="update"``): the org
    ACCEPTS the CHANGE — the create stages the initial state, a positive
    UpdateStep stages the update clauses and carries the ``expect_acceptance``
    semantics instead (the stage-progress case, TC-020)."""
    subject_ref = IdentityBearingRef(
        entity_type=g.subject.entity_type, entity_id=g.subject.entity_id,
        version_seq=g.version_seq, external_id=g.subject.external_id,
    )
    object_api = g.subject.external_id
    operation = "update" if g.update_conditions else "create"
    if g.update_conditions:
        # D-306.1 (adversarial-review B1): the phase split and the change
        # DIRECTION must be identity-bearing, and the conditions layer cannot
        # carry them (a commutative AND-composed SET — sorting erases both;
        # progress/regress and split-shift claims collided). The destination
        # rides the v2 BODY (update_state); semantic_conditions carries ONLY
        # the initial state (a satisfiable conjunction again).
        non_equals = [c.predicate for c in g.update_conditions
                      if c.predicate != "equals"]
        if non_equals:
            # Construction-proof (the stash gate refuses upstream): a
            # non-equals clause would enter identity yet never be staged.
            raise ValueError(
                f"update_conditions must be equals-only; got {non_equals}")
        if getattr(g, "approval_actions", ()):
            # D-333: the approval-arc acceptance — the actions are IDENTITY
            # (the v3 body): "accepted AFTER approval" and the plain
            # "accepted" must hash apart.
            claim = AcceptanceClaimArcBody(
                target=subject_ref, operation="update",
                update_state=StateDescriptor(field_values={
                    c.field.external_id: LiteralValue(value=c.value)
                    for c in g.update_conditions}),
                approval_actions=list(g.approval_actions))
        else:
            claim = AcceptanceClaimUpdateBody(
                target=subject_ref, operation="update",
                update_state=StateDescriptor(field_values={
                    c.field.external_id: LiteralValue(value=c.value)
                    for c in g.update_conditions}))
    else:
        claim = AcceptanceClaimBody(target=subject_ref, operation="create")
    conditions = _conditions_body(tuple(g.conditions), g.version_seq)

    staged = {c.field.external_id: c.value for c in g.conditions
              if c.predicate == "equals"}
    target = LogicalRef(entity_type=g.subject.entity_type,
                        external_id=object_api)
    trigger = DataMutationTriggerBody(
        operation=operation, target=target,
        identity_context="system", volume="single",
    )
    if g.update_conditions:
        # D-306 update-acceptance: the initial-state create stages `conditions`
        # (standard D-115.2 grading — a rejection of the PREMISE state surfaces
        # by field attribution); the UPDATE stages the change and carries the
        # expect_acceptance semantics (any business rejection of it = the
        # finding: the org refused a change the requirement says must succeed).
        update_staged = {c.field.external_id: c.value
                         for c in g.update_conditions if c.predicate == "equals"}
        # D-333: the approval actions run BETWEEN the initial-state create and
        # the accepted update (the arc realizes the approval state the change
        # depends on). () -> byte-identical to the D-306 shape.
        arc_steps = [
            ApprovalActionStep(step_id=f"approval-{i}", action=a)
            for i, a in enumerate(getattr(g, "approval_actions", ()))]
        steps = [
            CreateStep(
                step_id="create-record",
                target_object=target,
                field_values=staged,
            ),
            *arc_steps,
            UpdateStep(
                step_id="update-record",
                target=target,
                field_changes=update_staged,
                expect_acceptance=True,
            ),
            ReadStep(
                step_id="read-created",
                target=target,
                soql=(f"SELECT Id FROM {object_api} "
                      f"WHERE Id = '$create-record.id'"),
                fields_to_capture=["Id"],
            ),
            DataAssertStep(
                step_id="assert-exists",
                predicate=AssertionPredicate(
                    subject_ref="read-created.Id", predicate="exists"),
            ),
        ]
        arc_detail = (
            f"run the approval action(s) "
            f"[{' then '.join(getattr(g, 'approval_actions', ()))}], "
            if arc_steps else "")
        details = (f"create a {object_api} record staging the initial state "
                   f"({', '.join(sorted(staged)) or 'no staged fields'}), "
                   f"{arc_detail}"
                   f"update it to ({', '.join(sorted(update_staged))}) and "
                   f"expect the org to ACCEPT the change (no validation rule "
                   f"fires)")
    else:
        steps = [
            CreateStep(
                step_id="create-record",
                target_object=target,
                field_values=staged,
                expect_acceptance=True,
            ),
            ReadStep(
                step_id="read-created",
                target=target,
                soql=(f"SELECT Id FROM {object_api} "
                      f"WHERE Id = '$create-record.id'"),
                fields_to_capture=["Id"],
            ),
            DataAssertStep(
                step_id="assert-exists",
                predicate=AssertionPredicate(
                    subject_ref="read-created.Id", predicate="exists"),
            ),
        ]
        details = (f"create a {object_api} record staging the asserted business "
                   f"state ({', '.join(sorted(staged)) or 'no staged fields'}) "
                   f"and expect the org to ACCEPT it (no validation rule fires)")
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=steps,
    )
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user", details=details,
    )])
    return EmissionBundle(
        archetype=g.archetype, claim_kind=g.claim_kind,
        asserted_truth=claim, semantic_conditions=conditions,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env,
        # Layer-1: acceptance cannot be pre-verified from metadata — the RUN
        # is the verification (semantic_completeness: has_layer_2=False).
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=requires_caveat(g.claim_kind),
        caveat_kind=caveat_kind(g.claim_kind),
    )


def _conditions_body(grounded: tuple, version_seq: int):
    """Build the identity-bearing conditions body from grounded business-state
    clauses (D-293). Each clause becomes one condition over the S1-resolved
    field. Empty input -> ``conditions=[]`` — byte-identical to the pre-D-293
    condition-free prohibition (the dormant default).

    D-330: a clause set containing a CROSS-FIELD comparison (``compared_to``)
    authors the v2 body (``SemanticConditionsBodyV2``); an all-v1 clause set
    keeps authoring v1 byte-identically (existing claims never re-key)."""
    def _ref(ep):
        return IdentityBearingRef(
            entity_type=ep.entity_type, entity_id=ep.entity_id,
            version_seq=version_seq, external_id=ep.external_id)

    # getattr keeps hand-built grounded conditions working (the D-288 idiom).
    if any(getattr(c, "compared_to", None) is not None for c in grounded):
        return SemanticConditionsBodyV2(conditions=[
            ConditionV2(
                subject=_ref(c.field), predicate=c.predicate, value=c.value,
                compared_to=(_ref(c.compared_to)
                             if getattr(c, "compared_to", None) is not None
                             else None))
            for c in grounded
        ])
    return SemanticConditionsBody(conditions=[
        Condition(
            subject=_ref(c.field), predicate=c.predicate, value=c.value)
        for c in grounded
    ])


def _author_arc_negative(g: GroundedNegative) -> EmissionBundle:
    """Author the D-333 approval-arc prohibition: after ``approval_actions``
    run against the subject record, the org REJECTS the explicit
    ``attempted_change``. Complete BY CONSTRUCTION — the rejection premise
    (the record's approval state) is realized by the org's own actions,
    never staged and never VR-derived, so the D-293 derivability machinery
    does not apply. The recipe stages the claim's equals conditions on the
    setup create (the D-305 acceptance staging discipline — governance
    already picklist-bound them, D-332), runs the actions, then attempts
    the update expecting the business rejection. Layer-1 + the honest
    caveat: the deeper pre-verification layer genuinely wasn't run — the
    RUN is the verification (the acceptance posture, D-305)."""
    subject_ref = IdentityBearingRef(
        entity_type=g.subject.entity_type, entity_id=g.subject.entity_id,
        version_seq=g.version_seq, external_id=g.subject.external_id,
    )
    change_ep, change_value = g.attempted_change
    operation = (g.operation_hint
                 if g.operation_hint in ("modify_field", "modify_record")
                 else "modify_record")
    # D-297: bind the aligned VR's synced message when the grounding narrowed
    # to exactly one formula — the arc's rejection should name ITS rule.
    _raw = (g.vr_messages.get(g.vr_formulas[0])
            if len(g.vr_formulas) == 1 else None)
    error_message = html.unescape(_raw) if _raw else None

    claim = ProhibitionClaimArcBody(
        target=subject_ref,
        operation=operation,
        prohibition_mechanism="validation_rule",
        expected_rejection=RejectionSignal(error_code=_VR_REJECTION_ERROR_CODE),
        approval_actions=list(g.approval_actions),
        attempted_change={change_ep.external_id: str(change_value)},
    )
    conditions = _conditions_body(g.conditions, g.version_seq)

    target = LogicalRef(entity_type=g.subject.entity_type,
                        external_id=g.subject.external_id)
    staged = {c.field.external_id: c.value for c in g.conditions
              if c.predicate == "equals"}
    trigger = DataMutationTriggerBody(
        operation="update", target=target,
        identity_context="system", volume="single",
    )
    steps = [CreateStep(step_id="create-setup", target_object=target,
                        field_values=staged)]
    steps += [ApprovalActionStep(step_id=f"approval-{i}", action=a)
              for i, a in enumerate(g.approval_actions)]
    steps.append(UpdateStep(
        step_id="update-blocked", target=target,
        field_changes={change_ep.external_id: change_value},
        expect_rejection=RejectionExpectation(
            error_code=_VR_REJECTION_ERROR_CODE,
            error_message_pattern=(
                re.escape(error_message) if error_message else None)),
    ))
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api", steps=steps,
    )
    arc_words = " then ".join(g.approval_actions)
    env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
        auth_kind="data_api_user",
        details=(f"create a {g.subject.external_id} staging the asserted "
                 f"business state, run the approval action(s) [{arc_words}], "
                 f"then attempt {change_ep.external_id}="
                 f"{change_value!r} and expect the org to REJECT it"),
    )])
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


def _author_negative(g: GroundedNegative, *,
                     enable_bva_boundaries: bool = False) -> EmissionBundle:
    # D-333: the approval-action arc has its own author — dispatch BEFORE the
    # VR-derivation path (the arc's completeness is constructional).
    if g.approval_actions:
        return _author_arc_negative(g)
    subject_ref = IdentityBearingRef(
        entity_type=g.subject.entity_type, entity_id=g.subject.entity_id,
        version_seq=g.version_seq, external_id=g.subject.external_id,
    )
    # Graded operation dispatch (D-203) over the verified-vs-caveated gate
    # (D-107) — bind the operation + derive the behavioural reject recipe via the
    # shared predicate (the single source of truth with the D-293 governance
    # gate). The derived payloads ride the RECIPE (operational), never the claim;
    # the violating VALUE is recipe-rewrite-stable (D-110.3 Option-C), while the
    # business STATE rides semantic_conditions (D-293, identity-bearing).
    operation, update_pair, violation, source_formula = _derive_prohibition_recipe(
        g.operation_hint, g.vr_formulas, g.field_metadata,
        sibling_vr_items=[(msg or text, text)
                          for text, msg in (g.vr_messages or {}).items()])
    verified = update_pair is not None or violation is not None
    # D-297 (lever 5): bind the DERIVED VR's synced error message (from the grounding
    # map, keyed by the VR the recipe actually violates — never an arbitrary aligned
    # VR, so a multi-VR condition-free passthrough binds the right message or none)
    # so the S4 grade confirms WHICH rule fired. No message / unknown source -> None
    # -> the recipe's error_message_pattern stays None -> byte-identical. The message
    # is S1-synced from Salesforce (ground-or-refuse), never LLM-authored.
    # D-297.1: HTML-unescape the synced message before it is escaped into the
    # pattern — the Tooling API stores VR messages with HTML entities (e.g.
    # "&#8377;" for the rupee sign), but the org's runtime rejection renders them;
    # normalizing to the rendered form (S4 _matches unescapes the runtime side too)
    # keeps a CORRECT rejection from grading `failed` on an encoding mismatch.
    _raw_message = g.vr_messages.get(source_formula) if source_formula else None
    error_message = html.unescape(_raw_message) if _raw_message else None
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
    # D-293: the business STATE is identity-bearing. When governance grounded a
    # rejection condition (Option A), author it into semantic_conditions so this
    # prohibition is a DISTINCT claim (no AC1/2/4 collapse); the violating VALUE
    # stays operational (the recipe), preserving D-110.3's recipe-rewrite identity
    # stability. Absent -> conditions=[], byte-identical to pre-D-293 (DORMANT
    # until the intent contract + grounding populate g.conditions).
    conditions = _conditions_body(g.conditions, g.version_seq)

    # D-110.3 (S3-thin) + D-203: a VERIFIED negative emits the BEHAVIORAL
    # recipe — the 2-step update-rejected shape when both directions derived,
    # else the create-rejected (behavioral subsumes structural: it tests the VR
    # *enforces*). A CAVEATED negative (no derivable formula, or a
    # non-VR-testable operation) stays the INSPECTION re-verify (there is no
    # violation to construct). Replace, not augment (single-recipe; D-110.3).
    if update_pair is not None:
        _detail = (f"create a valid {g.subject.external_id} record, then "
                   f"update it into violation of the grounding validation "
                   f"rule (expect rejection)")
        _prov = getattr(update_pair, "provenance", None)
        if _prov:
            # The transition witness's durable record of WHY each field is
            # staged (AK Option-1 roles), mirroring the boundary probes.
            parts = [f"{f} [{role}: {str(src)[:60]}]"
                     for f, (role, src) in sorted(_prov.items())]
            _detail += "; fixture provenance: " + "; ".join(parts)
        trigger, recipe, env = _update_rejected_recipe(
            subject_entity_type=g.subject.entity_type,
            subject_external_id=g.subject.external_id,
            setup_payload=_to_transport_payload(
                update_pair.setup_payload, g.field_metadata),
            violating_changes=_to_transport_payload(
                update_pair.violating_changes, g.field_metadata),
            env_detail=_detail,
            error_message=error_message,
            entry_changes=_to_transport_payload(
                getattr(update_pair, "entry_changes", None), g.field_metadata),
        )
        trigger_kind, recipe_kind = "data-mutation-trigger", "data-recipe"
    elif verified:
        trigger, recipe, env = _behavioral_recipe(
            subject_entity_type=g.subject.entity_type,
            subject_external_id=g.subject.external_id,
            violating_payload=_to_transport_payload(
                violation.violating_payload, g.field_metadata),
            env_detail=(f"create a {g.subject.external_id} record violating the "
                        f"grounding validation rule (expect rejection)"),
            error_message=error_message,
        )
        trigger_kind, recipe_kind = "data-mutation-trigger", "data-recipe"
    else:
        # D-293 decision-2 (refuse, never silently degrade). A prohibition with no
        # derivable behavioural reject recipe (non-numeric VR; or delete / share /
        # transfer_ownership, which VRs never reject) is an INCOMPLETE behaviour
        # instance. Governance's prohibition_recipe_derivable() gate REFUSES such
        # intents before stash, so this branch is unreachable on the normal path;
        # the guard makes the invariant explicit — emission no longer degrades to
        # the pre-D-293 caveated metadata inspection (which masked the AC1/2/4
        # collapse and degraded AC3 to a bare existence check).
        raise BehaviourIncomplete(
            f"prohibition on {g.subject.external_id}: no derivable behavioural "
            f"reject recipe (operation={operation}); the D-293 completeness gate "
            f"must refuse this intent before emission")

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

    # D-300 (flag-gated): a verified SINGLE-THRESHOLD-NUMERIC prohibition
    # additionally authors its boundary probe set — the just-inside ACCEPT
    # probe rides boundary_recipes and the claim is stamped strategy_kind='bva'
    # (routes run-all; the firing probe IS the primary above, unchanged).
    # The stamp is conditional on a NON-EMPTY authored set: a bva claim with
    # no boundary probe would strict-AND over the primary alone — single with
    # extra steps, never minted. derive_boundary_set refuses compounds /
    # non-threshold shapes, so arming is exactly as narrow as D-300 scopes.
    # Flag OFF (the default) -> byte-identical bundle.
    boundary = ()
    strategy = None
    # T3 positive (VR10 arc): when the primary is a TRANSITION pair, author the
    # INVERSE experiment as an accept probe on the SAME claim — every violation
    # branch falsified, gates + siblings satisfied, the update (the transition
    # into the gated state) expected to SUCCEED, with a read-back asserting the
    # to-state persisted. Witness None (underivable / UNSAT) → no probe (the
    # negative stands alone) — an acceptance is never emitted on a fixture the
    # rule might still reject.
    if (enable_bva_boundaries and violation is not None
            and getattr(violation, "decision", None) is not None):
        # VR03 arc: DecisionBranchCoverage probes — the primary is the first
        # isolated firing arm; here ride the remaining firing arm(s) (each
        # requiring target attribution) + the gate NECESSITY controls (each
        # must be ACCEPTED, read back on its varied dimension).
        dexp = violation.decision
        _raw = (g.vr_messages or {}).get(source_formula)
        _msg = html.unescape(_raw) if _raw else None
        probes = []
        for arm in dexp.arms[1:]:
            if arm.expect_reject:
                t_, r_, e_ = _behavioral_recipe(
                    subject_entity_type=g.subject.entity_type,
                    subject_external_id=g.subject.external_id,
                    violating_payload=_to_transport_payload(
                        arm.payload, g.field_metadata),
                    env_detail=(
                        f"decision-branch firing arm ({arm.label}): the "
                        f"{arm.varied_field} branch fires with every other "
                        f"branch held false — expect rejection attributed to "
                        f"the rule under test"),
                    error_message=_msg,
                )
                probes.append(BoundaryRecipe(
                    trigger_kind="data-mutation-trigger",
                    recipe_kind="data-recipe",
                    causal_initiation=t_, observation_realization=r_,
                    execution_environment=e_,
                    priority=_BOUNDARY_PROBE_PRIORITY))
            else:
                t_, r_, e_ = _boundary_accept_recipe(
                    subject_entity_type=g.subject.entity_type,
                    subject_external_id=g.subject.external_id,
                    payload=_to_transport_payload(
                        arm.payload, g.field_metadata),
                    edge=arm.label,
                    boundary_field=arm.varied_field,
                    fixture_provenance=arm.provenance,
                )
                probes.append(BoundaryRecipe(
                    trigger_kind="data-mutation-trigger",
                    recipe_kind="data-recipe",
                    causal_initiation=t_, observation_realization=r_,
                    execution_environment=e_,
                    priority=_BOUNDARY_PROBE_PRIORITY))
        if probes:
            boundary = tuple(probes)
            strategy = "bva"
    if (enable_bva_boundaries
            and getattr(update_pair, "temporal", None) is not None):
        # VR06 arc: the temporal experiment's probe arms — the primary is the
        # blank-reject arm; here ride run_date−1 (reject, the < TODAY()
        # branch), run_date (accept — the adjacent boundary), run_date+1
        # (accept). Each accept reads back the transitioned state.
        exp = update_pair.temporal
        _raw = (g.vr_messages or {}).get(source_formula)
        _msg = html.unescape(_raw) if _raw else None
        probes = []
        for label, dv, expect_reject in exp.arms:
            if dv is None:
                continue                    # the blank arm IS the primary
            arm_setup = {**exp.setup_base, exp.date_field: dv}
            arm_prov = {**exp.provenance,
                        exp.date_field: (ROLE_TARGET_WITNESS,
                                         f"temporal arm: {label}")}
            if expect_reject:
                t_, r_, e_ = _update_rejected_recipe(
                    subject_entity_type=g.subject.entity_type,
                    subject_external_id=g.subject.external_id,
                    setup_payload=_to_transport_payload(
                        arm_setup, g.field_metadata),
                    violating_changes=_to_transport_payload(
                        exp.changes, g.field_metadata),
                    env_detail=(
                        f"temporal boundary arm ({label}): the {exp.date_field} "
                        f"is a replay-stable RelativeDate materialised at the "
                        f"S4 boundary; the transition must be REJECTED "
                        f"(the < TODAY() branch)"),
                    error_message=_msg,
                )
                probes.append(BoundaryRecipe(
                    trigger_kind="data-mutation-trigger",
                    recipe_kind="data-recipe",
                    causal_initiation=t_, observation_realization=r_,
                    execution_environment=e_,
                    priority=_BOUNDARY_PROBE_PRIORITY))
            else:
                from primeqa.generation.transition import TransitionWitness
                probes.append(_transition_accept_probe(
                    subject_entity_type=g.subject.entity_type,
                    subject_external_id=g.subject.external_id,
                    witness=TransitionWitness(
                        setup=arm_setup, changes=dict(exp.changes),
                        provenance=arm_prov),
                    field_metadata=g.field_metadata,
                    differential_note=(
                        f"temporal boundary arm ({label}): the same transition "
                        f"the past-date arm rejects must be ACCEPTED — the "
                        f"adjacent boundary proof"),
                ))
        if probes:
            boundary = tuple(probes)
            strategy = "bva"
    if (enable_bva_boundaries and update_pair is not None
            and getattr(update_pair, "provenance", None)
            and source_formula and has_transition_semantics(source_formula)):
        _sibs = [(msg or text, text)
                 for text, msg in (g.vr_messages or {}).items()]
        if getattr(update_pair, "entry_changes", None):
            # VR05 arc: the PRIOR_STATE ContextDifferential control arm — the
            # SAME setup and the SAME mutation with the entry transition
            # OMITTED (the one varied dimension is the prior-state context;
            # the accept↔reject delta is attributable to transition history
            # alone). None → no control (never fabricated on a fixture some
            # control might reject).
            _ctrl = derive_prior_state_control(
                source_formula, update_pair.setup_payload,
                update_pair.violating_changes, g.field_metadata, _sibs)
            if _ctrl is not None:
                boundary = (_transition_accept_probe(
                    subject_entity_type=g.subject.entity_type,
                    subject_external_id=g.subject.external_id,
                    witness=_ctrl, field_metadata=g.field_metadata,
                    differential_note=(
                        "prior-state context varied: the entry transition is "
                        "OMITTED (the differential's control arm) — the same "
                        "mutation the entered arm rejects must be ACCEPTED"),),)
                strategy = "bva"
        else:
            _accept = satisfy_transition_acceptance(
                source_formula, g.field_metadata, sibling_items=_sibs)
            if _accept is not None:
                boundary = (_transition_accept_probe(
                    subject_entity_type=g.subject.entity_type,
                    subject_external_id=g.subject.external_id,
                    witness=_accept, field_metadata=g.field_metadata),)
                strategy = "bva"
    if (enable_bva_boundaries and verified and source_formula
            and not boundary):
        # (the transition / temporal / decision experiments above own their
        # probe sets; the D-300 boundary machinery must not overwrite them)
        members = derive_boundary_set(parse(source_formula), g.field_metadata)
        if members:
            # Deterministic accept-fixture completion (AK Option-1 scope; the
            # D-347 satisfaction operation's first production consumer): the
            # accept probe's staged values may ARM a SIBLING control (live
            # catch: Enterprise + Discount 25% armed VR02's approval-reason
            # requiredness — the probe was setup-rejected by a rule that is
            # not under test). Complete the fixture on FREE dimensions only —
            # every staged field is PROTECTED (the boundary witness + its
            # gates/context are preserved exactly); a sibling silenceable only
            # on a protected dimension is UNSAT → author NO probe (the claim
            # stays single) rather than modify what is under test. Runs in the
            # FORMULA domain, before transport. Provenance classifies every
            # payload value (target witness / activation / context / sibling
            # isolation) and rides the probe's env detail.
            vr_items = [(msg or text, text)
                        for text, msg in (g.vr_messages or {}).items()] or [
                (t, t) for t in g.vr_formulas]
            # ContextDifferential (RECORD dimension, Amendment B §4): when the
            # firing member stages a RecordTypeId (the claim is record-context-
            # gated) and the org exposes a real alternative record type, derive
            # the CONTROL arm by reference — the same above-boundary value under
            # the alternative classification, expected ACCEPTED. The treatment
            # arm IS the firing member (already the primary's proof), so the
            # composition dedup is by construction. No RecordTypeId / no
            # alternative → None → the plain BoundaryPair (2-member fallback).
            firing = next((m for m in members if m.expect_reject), None)
            if firing is not None:
                control = derive_context_control(firing, g.field_metadata)
                if control is not None:
                    members = members + (control,)
            completed = []
            for m in members:
                if m.expect_reject:
                    completed.append(m)
                    continue
                comp = complete_accept_fixture(
                    m.payload, set(m.payload), vr_items, g.field_metadata)
                if isinstance(comp, FixtureUnsat):
                    if m.edge == "context-control":
                        continue    # control UNSAT → drop the arm (2-member fallback)
                    completed = None            # UNSAT → no probe, claim single
                    break
                varied = m.edge == "context-control"
                prov = {
                    f: ((ROLE_CONTEXT,
                         "record type (varied: the differential's control arm)"
                         if varied else "record type")
                        if f == "RecordTypeId"
                        else (ROLE_TARGET_WITNESS, source_formula)
                        if f == m.boundary_field
                        else (ROLE_TARGET_ACTIVATION, source_formula))
                    for f in m.payload}
                prov.update(comp.provenance)
                completed.append(replace(
                    m, payload={**m.payload, **comp.fills},
                    fixture_provenance=prov))
            members = tuple(completed) if completed is not None else ()
        if members:
            # P1: boundary payloads are FORMULA-domain (derive's domain) — map
            # them through the D-346 typed value boundary like every other
            # emitted payload (a percent just-inside 0.25 ships as 25.00; the
            # pre-P1 integer thresholds pass through 1:1, byte-identical).
            members = tuple(
                replace(m, payload=_to_transport_payload(
                    m.payload, g.field_metadata))
                for m in members)
            boundary = author_boundary_recipes(
                subject_entity_type=g.subject.entity_type,
                subject_external_id=g.subject.external_id,
                members=members)
        if boundary:
            strategy = "bva"

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
        strategy_kind=strategy,
        boundary_recipes=boundary,
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


def _fmt_expected(v) -> str:
    """Human/identity-facing rendering of an expected value: a symbolic
    RelativeDate narrates as its MEANING (``<the run date + 5 days>``) —
    stable and replay-safe, never a raw wire-dict repr; everything else is
    ``repr`` (the existing idiom)."""
    off = relative_date_offset(v)
    if off is None:
        return repr(v)
    sign = "+" if off >= 0 else "-"
    plural = "s" if abs(off) != 1 else ""
    return f"<the run date {sign} {abs(off)} day{plural}>"


def _observe_steps(object_api: str, field_api: str, expected: Any,
                   *, create_fields: Optional[dict] = None,
                   update_fields: Optional[dict] = None) -> list:
    """The shared observe-the-org shape (D-210.1): create the subject WITHOUT
    the asserted field (the AUTOMATION must set it — contrast _author_positive,
    which sets the field directly), read it back, assert the org-produced
    value.

    ``update_fields`` (D-306, update-then-observe): a positive UpdateStep
    between the create and the read — create the INITIAL state, CHANGE it, and
    the read observes the org's RE-computed effect. NEITHER step carries an
    expectation flag (D-306.1, overturning the D-306 entry's
    expect_acceptance-on-the-create choice): this create is PREMISE staging
    for a recalc claim, not the assertion, so a rejection grades by the
    standard D-115.2 attribution — a staged trigger field named → ``failed``
    (the premise state is refused, a real finding); padding-provoked or
    unattributed → ``errored`` (S4's own operational gap). The adversarial
    review showed the flag converted padding-provoked VR rejections into
    false business findings on unattended runs. A rejected trigger UPDATE is
    likewise staging failure → ``errored``. ``None``/empty → the create-scoped
    shape, byte-identical."""
    target = LogicalRef(entity_type="Object", external_id=object_api)
    steps = [
        CreateStep(
            step_id="create-record",
            target_object=target,
            # padding-only create (k16): the asserted field is deliberately
            # ABSENT — the org's automation is what must produce it.
            field_values=dict(create_fields or {}),
        ),
    ]
    if update_fields:
        steps.append(UpdateStep(
            step_id="update-record",
            target=target,
            field_changes=dict(update_fields),
        ))
    return steps + [
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
    # D-327: the description narrates the trigger from STRUCTURE only — the
    # requirement excerpt is provenance (it rides the intent/llm_calls), not
    # identity. Same-meaning intents with different excerpt slices must hash
    # together so persistence dedup (SPEC §7.7) collapses them.
    if g.trigger_object is not None:
        event_desc = (f"creating a {g.trigger_object.external_id} linked to "
                      f"the subject")
    elif g.trigger_field is not None:
        event_desc = (f"a data change setting "
                      f"{g.trigger_field.external_id}={g.trigger_value!r}")
    else:
        event_desc = f"a data change on {object_api}"
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
    # D-327: structural narration only — the requirement excerpt is
    # provenance, not identity (see _author_state_transition).
    event = EventDescriptor(trigger_kind="data-mutation-trigger",
                            description=f"creating a {object_api}")
    target = LogicalRef(entity_type="Object", external_id=object_api)
    trigger_op = "create"

    if g.rollup_spec:
        # Completion E3: the ROLL-UP shape — a flow triggered on a CHILD
        # object aggregates the sibling set (Count / Sum) and stamps the
        # result on the subject-as-parent. Chain: create the subject, stage
        # `count` children under it (each carrying the substrate-derived
        # per-row values), create a SECOND parent with ONE child under it
        # (the correlation distractor — a rollup that ignored its own
        # correlation would sweep it in and the expected value would be
        # off), read the subject back, assert effect_field == expected.
        # Every value substrate-derived from the producing op + aggregate
        # (deterministic identity).
        rs = g.rollup_spec
        child_api = rs["child_object"]
        lookup_key = (f"{child_api}.{rs['lookup']}"
                      if "." not in rs["lookup"] else rs["lookup"])
        field_api = g.effect_field.external_id
        field_bare = field_api.split(".", 1)[-1]
        n = rs["count"]
        child_vals = {f"{child_api}.{f}" if "." not in f else f: v
                      for f, v in rs["staged"]}
        child_target = LogicalRef(entity_type="Object", external_id=child_api)
        steps = [CreateStep(step_id="create-record", target_object=target,
                            field_values={})]
        for i in range(n):
            steps.append(CreateStep(
                step_id=f"create-child-{i+1}", target_object=child_target,
                field_values={**child_vals, lookup_key: "$create-record.id"}))
        steps.append(CreateStep(step_id="create-parent-2",
                                target_object=target, field_values={}))
        steps.append(CreateStep(
            step_id="create-distractor", target_object=child_target,
            field_values={**child_vals, lookup_key: "$create-parent-2.id"}))
        steps.append(ReadStep(
            step_id="read-effect", target=target,
            soql=(f"SELECT Id, {field_bare} FROM {object_api} "
                  f"WHERE Id = '$create-record.id'"),
            fields_to_capture=["Id", field_bare]))
        steps.append(DataAssertStep(
            step_id="assert-effect",
            predicate=AssertionPredicate(
                subject_ref=f"read-effect.{field_bare}",
                predicate="equals", value=rs["expected"])))
        staged_desc = ", ".join(f"{f}={v!r}" for f, v in rs["staged"]) \
            or "no staged fields"
        claim = AutomationEffectClaimBody(
            automation=automation_ref,
            automation_primitive=g.automation_primitive,
            triggering_action=EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description=(f"creating {n} {child_api} children of a "
                             f"{object_api} (each with {staged_desc}; +1 "
                             f"child under a second {object_api})")),
            expected_effect=FieldChangeEffect(changes=StateDescriptor(
                field_values={field_api: LiteralValue(
                    value=rs["expected"])})),
            affected_fields=[IdentityBearingRef(
                entity_type=g.effect_field.entity_type,
                entity_id=g.effect_field.entity_id,
                version_seq=g.version_seq, external_id=field_api)],
        )
        details = (f"create a {object_api}, stage {n} {child_api} children "
                   f"under it ({staged_desc}) + 1 child under a second "
                   f"{object_api} (the correlation distractor), read the "
                   f"first {object_api} back, assert the {rs['fn']} rollup "
                   f"set {field_bare}={rs['expected']!r}")
        conditions = SemanticConditionsBody(conditions=[])
        trigger = DataMutationTriggerBody(
            operation="create", target=child_target,
            identity_context="system", volume="single")
        recipe = DataRecipeBody(
            api_choice="rest", identity_context="system",
            execution_mechanism="direct_api", steps=steps)
        env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
            auth_kind="data_api_user", details=details)])
        return EmissionBundle(
            archetype=g.archetype, claim_kind=g.claim_kind,
            asserted_truth=claim, semantic_conditions=conditions,
            trigger_kind="data-mutation-trigger",
            recipe_kind="data-recipe",
            causal_initiation=trigger, observation_realization=recipe,
            execution_environment=env,
            admissibility_layer=AdmissibilityLayer.LAYER_1,
            caveat_required=requires_caveat(g.claim_kind),
            caveat_kind=caveat_kind(g.claim_kind),
        )

    # D-306: the update-observe phase is SAME-RECORD only (the recompute is
    # observed on the record whose state changed). The stash gate refuses the
    # cross-object/parent-stamp combinations upstream; construction-proof here.
    if g.update_trigger_fields and g.effect_object is not None \
            and g.effect_via_lookup_field is not None:
        # parent-stamp + update transition remains unbuilt (no consumer);
        # the PLAIN cross-object update transition is the E1 shape below.
        raise ValueError(
            "update_trigger_fields on a parent-stamp automation effect — "
            "the update-observe transition is not built for that shape")
    if g.transform_chain and (g.effect_object is not None
                              or g.update_trigger_fields):
        raise ValueError(
            "transform provenance on a cross-object/parent-stamp/update-phase "
            "automation effect — the transform shape is create-scoped "
            "same-record only (FL02 slice v1)")

    if g.effect_object is None:
        # same-record: the Flow sets effect_field on the subject itself
        field_api = g.effect_field.external_id
        field_ref = IdentityBearingRef(
            entity_type=g.effect_field.entity_type, entity_id=g.effect_field.entity_id,
            version_seq=g.version_seq, external_id=field_api)
        # D-299: the create SETS the grounded entry-condition fields so the
        # Flow's entry gate actually fires — a padding-only create can never
        # provoke the effect (observability, not correctness). Keys are
        # object-qualified external_ids (S4 bare-ifies them via world._sf_fields,
        # exactly as for the D-222 state-transition trigger). The value is
        # carried raw (the recipe side; the claim wraps in LiteralValue). Empty
        # trigger_fields () -> today's padding-only shape, byte-identical.
        create_fields = {ep.external_id: val for ep, val in g.trigger_fields}
        if g.premise_sibling:
            # Completion (FL06 slice): the premise-conditioned shape — the
            # sibling is created FIRST (its own save cannot fire the arm:
            # no match exists yet), then the subject sharing the
            # correlation witness; the read observes the conditional flag.
            # All values substrate-derived (deterministic identity).
            ps = g.premise_sibling
            field_bare = field_api.split(".", 1)[-1]
            sib_vals = {f"{object_api}.{f}" if "." not in f else f: v
                        for f, v in ps["staged"]}
            gate = ", ".join(f"{k}={v!r}" for k, v in create_fields.items())
            sib_desc = ", ".join(f"{f}={v!r}" for f, v in ps["staged"])
            steps = [
                CreateStep(step_id="create-sibling", target_object=target,
                           field_values=sib_vals),
                CreateStep(step_id="create-record", target_object=target,
                           field_values=dict(create_fields)),
                ReadStep(step_id="read-effect", target=target,
                         soql=(f"SELECT Id, {field_bare} FROM {object_api} "
                               f"WHERE Id = '$create-record.id'"),
                         fields_to_capture=["Id", field_bare]),
                DataAssertStep(step_id="assert-effect",
                               predicate=AssertionPredicate(
                                   subject_ref=f"read-effect.{field_bare}",
                                   predicate="equals",
                                   value=g.effect_value)),
            ]
            claim = AutomationEffectClaimBody(
                automation=automation_ref,
                automation_primitive=g.automation_primitive,
                triggering_action=EventDescriptor(
                    trigger_kind="data-mutation-trigger",
                    description=(f"creating a {object_api} with {gate} "
                                 f"when a matching sibling exists "
                                 f"({sib_desc})")),
                expected_effect=FieldChangeEffect(changes=StateDescriptor(
                    field_values={field_api: LiteralValue(
                        value=g.effect_value)})),
                affected_fields=[field_ref],
            )
            details = (f"create a {object_api} sibling ({sib_desc}), create "
                       f"the {object_api} under test with {gate}, read it "
                       f"back, assert the premise-conditioned arm set "
                       f"{field_bare}={g.effect_value!r} (without the "
                       f"sibling the arm provably cannot fire)")
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
                trigger_kind="data-mutation-trigger",
                recipe_kind="data-recipe",
                causal_initiation=trigger, observation_realization=recipe,
                execution_environment=env,
                admissibility_layer=AdmissibilityLayer.LAYER_1,
                caveat_required=requires_caveat(g.claim_kind),
                caveat_kind=caveat_kind(g.claim_kind),
            )
        # FL02 slice: the TRANSFORM shape stages the RAW witness ON the effect
        # field itself — the deliberate, documented exception to the k16
        # padding-only rule. Staged != expected by construction (the stash
        # gate synthesized and verified the pair), so the test can never be
        # self-fulfilling: if the flow does not run, the read-back returns
        # the raw value and the assert fails honestly.
        if g.transform_chain:
            create_fields = dict(create_fields)
            create_fields[field_api] = g.transform_staged_value
        # D-306: the update-observe phase — the CHANGED state an UpdateStep
        # stages after the create, so the read observes the RE-computed effect.
        update_fields = {ep.external_id: val
                         for ep, val in g.update_trigger_fields}
        sr_event = event
        gate = ", ".join(f"{k}={v!r}" for k, v in create_fields.items())
        upd_gate = ", ".join(f"{k}={v!r}" for k, v in update_fields.items())
        if update_fields:
            # The update IS the causal event — the description narrates the
            # phase (create-scoped vs update-scoped claims hash apart on it).
            # D-327: staged clause only — the excerpt is provenance.
            trigger_op = "update"
            initial = f" with {gate}" if create_fields else ""
            sr_event = EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description=(f"creating a {object_api}{initial} then updating "
                             f"{upd_gate}"))
        elif g.transform_chain:
            # identity-bearing narration (D-327 idiom): the staged raw input
            # + the normalize framing hash this claim apart from a literal
            # stamp of the same field/value.
            sr_event = EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description=(f"creating a {object_api} with {gate} — raw "
                             f"input the org normalizes on save"))
        elif create_fields:
            sr_event = EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description=f"creating a {object_api} with {gate}")
        claim = AutomationEffectClaimBody(
            automation=automation_ref,
            automation_primitive=g.automation_primitive,
            triggering_action=sr_event,
            expected_effect=FieldChangeEffect(changes=StateDescriptor(
                field_values={field_api: LiteralValue(value=g.effect_value)})),
            affected_fields=[field_ref],
        )
        steps = _observe_steps(object_api, field_api, g.effect_value,
                               create_fields=create_fields,
                               update_fields=update_fields)
        if update_fields:
            initial = f" with {gate} (the initial state)" if create_fields else ""
            details = (f"create a {object_api} record{initial}, update "
                       f"{upd_gate} (the change), read it back, assert the "
                       f"org re-computed "
                       f"{field_api}={_fmt_expected(g.effect_value)}")
        elif g.transform_chain:
            chain = "(".join(reversed(g.transform_chain)) \
                + "(value" + ")" * len(g.transform_chain)
            details = (f"create a {object_api} record staging the raw value "
                       f"{g.transform_staged_value!r} on {field_api}, read it "
                       f"back, assert the Flow normalized it to "
                       f"{field_api}={g.effect_value!r} (= {chain})")
        elif create_fields:
            details = (f"create a {object_api} record with {gate} (the Flow's "
                       f"entry condition), read it back, assert the Flow set "
                       f"{field_api}={g.effect_value!r}")
        else:
            details = (f"create a {object_api} record, read it back, assert the "
                       f"Flow set {field_api}={g.effect_value!r}")
    elif g.effect_via_lookup_field is not None:
        # D-227 parent-stamp: the Flow stamps a record the TRIGGER record
        # points to via its own lookup. Create the parent FIRST (so its id is
        # known — no relationship-traversal read), create the trigger with the
        # lookup set, read the PARENT back. Value-less stamps assert not_null
        # (e.g. $Flow.CurrentDate has no stable literal). Runs on the D-205
        # N-create chain. D-307: the staged entry gate rides the TRIGGER
        # create (never the parent) and narrates into the identity-bearing
        # description — the same D-299 idiom as the same-record branch.
        effect_api = g.effect_object.external_id
        via_api = g.effect_via_lookup_field.external_id
        field_api = g.effect_field.external_id
        field_bare = field_api.split(".", 1)[-1]
        create_fields = {ep.external_id: val for ep, val in g.trigger_fields}
        gate = ", ".join(f"{k}={v!r}" for k, v in create_fields.items())
        ps_event = event
        gated = ""
        if create_fields:
            gated = f" with {gate}"
            ps_event = EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description=f"creating a {object_api} with {gate}")
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
            automation=automation_ref,
            automation_primitive=g.automation_primitive,
            triggering_action=ps_event, expected_effect=effect,
            affected_fields=[field_ref],
        )
        steps = [
            CreateStep(step_id="create-parent",
                       target_object=LogicalRef(entity_type="Object",
                                                external_id=effect_api),
                       field_values={}),
            CreateStep(step_id="create-record", target_object=target,
                       field_values={**create_fields,
                                     via_api: "$create-parent.id"}),
            ReadStep(
                step_id="read-effect",
                target=LogicalRef(entity_type="Object", external_id=effect_api),
                soql=(f"SELECT {field_bare} FROM {effect_api} "
                      f"WHERE Id = '$create-parent.id'"),
                fields_to_capture=[field_bare]),
            DataAssertStep(step_id="assert-effect", predicate=assert_pred),
        ]
        details = (f"create a {effect_api} parent, create a {object_api}"
                   f"{gated} linked to it, read the parent back, assert the "
                   f"Flow stamped {stamp_desc}")
    else:
        # cross-object: the Flow creates a correlated effect record. D-307:
        # the staged entry gate rides the trigger create + the identity-
        # bearing description (the L7e live recon: a padding-only create
        # never provokes the correlated record — the flow's gate never fires).
        effect_api = g.effect_object.external_id
        lookup_api = g.effect_lookup_field.external_id
        # the lookup is bare-keyed in SOQL on the effect object
        lookup_bare = lookup_api.split(".", 1)[-1]
        create_fields = {ep.external_id: val for ep, val in g.trigger_fields}
        gate = ", ".join(f"{k}={v!r}" for k, v in create_fields.items())
        # E1: the cross-object UPDATE transition — the subject is created in
        # the NOT-meeting state and updated INTO the producer's entry state;
        # the read then observes the correlated effect. Identity-bearing
        # narration (a transitioned claim never collides with a create-only
        # one).
        xo_update_fields = {ep.external_id: val
                            for ep, val in g.update_trigger_fields}
        xo_upd_gate = ", ".join(f"{k}={v!r}"
                                for k, v in xo_update_fields.items())
        xo_event = event
        gated = ""
        if xo_update_fields:
            gated = (f" with {gate}" if create_fields else "") \
                + f", then updating {xo_upd_gate}"
            xo_event = EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description=(f"creating a {object_api}"
                             + (f" with {gate}" if create_fields else "")
                             + f" then updating {xo_upd_gate}"))
        elif create_fields:
            gated = f" with {gate}"
            xo_event = EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description=f"creating a {object_api} with {gate}")
        if g.premise_children:
            # Completion E2: the SET-UPDATE shape — the flow updates a
            # correlated set of PRE-EXISTING rows. Chain: create the subject
            # in the NOT-entry state; stage `count` MATCHING children (the
            # op's own filter template, correlated via the lookup) plus ONE
            # DISTRACTOR child (template value flipped to a third state);
            # update the subject INTO the entry state (the flow fires);
            # read WHERE correlate AND field=updated and assert the EXACT
            # count — a missed matching row (under-update) or a swept-in
            # distractor (the flow ignoring its own filter, over-update)
            # both fail the same assert. All values substrate-derived from
            # the producing op (deterministic identity).
            pc = g.premise_children
            field_api = g.effect_field.external_id
            field_bare = field_api.split(".", 1)[-1]
            n = pc["count"]
            tmpl = dict(pc["template"])
            child_base = {f"{effect_api}.{f}" if "." not in f else f: v
                          for f, v in tmpl.items()}
            child_base[lookup_api] = "$create-record.id"
            steps = [CreateStep(step_id="create-record",
                                target_object=target,
                                field_values=dict(create_fields))]
            for i in range(n):
                steps.append(CreateStep(
                    step_id=f"create-child-{i+1}",
                    target_object=LogicalRef(entity_type="Object",
                                             external_id=effect_api),
                    field_values=dict(child_base)))
            if pc.get("distractor"):
                dfld, dval = pc["distractor"]
                dvals = dict(child_base)
                dvals[f"{effect_api}.{dfld}"
                      if "." not in dfld else dfld] = dval
                steps.append(CreateStep(
                    step_id="create-distractor",
                    target_object=LogicalRef(entity_type="Object",
                                             external_id=effect_api),
                    field_values=dvals))
            steps.append(UpdateStep(
                step_id="update-record", target=target,
                field_changes=dict(xo_update_fields)))
            steps.append(ReadStep(
                step_id="read-effect",
                target=LogicalRef(entity_type="Object",
                                  external_id=effect_api),
                soql=(f"SELECT Id, {field_bare} FROM {effect_api} "
                      f"WHERE {lookup_bare} = '$create-record.id' "
                      f"AND {field_bare} = '{pc['updated_value']}'"),
                fields_to_capture=["Id", field_bare]))
            steps.append(DataAssertStep(
                step_id="assert-effect",
                predicate=AssertionPredicate(
                    subject_ref="read-effect.Id",
                    predicate="count_equals", value=n)))
            effect = FieldChangeEffect(changes=StateDescriptor(
                field_values={field_api: LiteralValue(
                    value=pc["updated_value"])}))
            claim = AutomationEffectClaimBody(
                automation=automation_ref,
                automation_primitive=g.automation_primitive,
                triggering_action=EventDescriptor(
                    trigger_kind="data-mutation-trigger",
                    description=(
                        f"creating a {object_api}{gated} with {n} matching "
                        f"{effect_api} children (+1 non-matching)")),
                expected_effect=effect,
                affected_fields=[IdentityBearingRef(
                    entity_type=g.effect_field.entity_type,
                    entity_id=g.effect_field.entity_id,
                    version_seq=g.version_seq, external_id=field_api)],
            )
            details = (f"create a {object_api}{gated}, stage {n} matching "
                       f"{effect_api} children + 1 distractor, update the "
                       f"{object_api} into the entry state, assert EXACTLY "
                       f"{n} children carry "
                       f"{field_bare}={pc['updated_value']!r} (the "
                       f"distractor must stay untouched)")
            conditions = SemanticConditionsBody(conditions=[])
            trigger = DataMutationTriggerBody(
                operation="update", target=target,
                identity_context="system", volume="single")
            recipe = DataRecipeBody(
                api_choice="rest", identity_context="system",
                execution_mechanism="direct_api", steps=steps)
            env = ExecutionEnvironmentBody(auth_assumptions=[AuthAssumption(
                auth_kind="data_api_user", details=details)])
            return EmissionBundle(
                archetype=g.archetype, claim_kind=g.claim_kind,
                asserted_truth=claim, semantic_conditions=conditions,
                trigger_kind="data-mutation-trigger",
                recipe_kind="data-recipe",
                causal_initiation=trigger, observation_realization=recipe,
                execution_environment=env,
                admissibility_layer=AdmissibilityLayer.LAYER_1,
                caveat_required=requires_caveat(g.claim_kind),
                caveat_kind=caveat_kind(g.claim_kind),
            )
        if g.expected_absence:
            # D-307: the ABSENCE mirror — same staged create, same correlate
            # read, the assert INVERTS (no row may exist). The v2 body IS the
            # identity discriminator (a distinct key-set — absence and
            # presence claims can never collide). The stash gate guarantees
            # effect_field is None here (construction-proofed below).
            if g.effect_field is not None:
                raise ValueError(
                    "expected_absence with effect_field — v1 absence asserts "
                    "NO correlated record at all (D-307); the stash gate "
                    "refuses field-conditional absence upstream")
            select = f"SELECT Id FROM {effect_api}"
            assert_pred = AssertionPredicate(
                subject_ref="read-effect.Id", predicate="not_exists",
                value=None)
            details = (f"create a {object_api} record{gated}, query "
                       f"{effect_api} via {lookup_bare}, assert the Flow "
                       f"correctly created NO correlated record")
            claim = AutomationAbsenceClaimBody(
                automation=automation_ref,
                automation_primitive=g.automation_primitive,
                triggering_action=xo_event,
            )
        elif g.effect_field is not None:
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
            details = (f"create a {object_api} record{gated}, query "
                       f"{effect_api} via {lookup_bare}, assert the "
                       f"Flow-created record carries "
                       f"{field_bare}={g.effect_value!r}")
        else:
            # No effect field: the effect IS the correlated record's creation
            # — a SideEffect ("outside the target record's own field values"),
            # never an empty field_change (which stated no effect at all: the
            # D-308 approval claims all landed here and titled/read as "An
            # automation updates the record"). The description is built from
            # grounded facts only — it is identity-bearing (feeds the
            # canonical hash), so it must stay deterministic per emission.
            if g.automation_primitive == "approval_process":
                effect = SideEffect(description=(
                    f"the record is submitted for approval — a {effect_api} "
                    f"approval request is created"))
            else:
                effect = SideEffect(description=(
                    f"a correlated {effect_api} record is created "
                    f"(linked via {lookup_bare})"))
            affected = []
            select = f"SELECT Id FROM {effect_api}"
            assert_pred = AssertionPredicate(
                subject_ref="read-effect.Id", predicate="exists", value=None)
            details = (f"create a {object_api} record{gated}, query "
                       f"{effect_api} via {lookup_bare}, assert the Flow "
                       f"created a correlated record")
        if not g.expected_absence:
            claim = AutomationEffectClaimBody(
                automation=automation_ref,
                automation_primitive=g.automation_primitive,
                triggering_action=xo_event, expected_effect=effect,
                affected_fields=affected,
            )
        steps = [
            CreateStep(step_id="create-record", target_object=target,
                       field_values=dict(create_fields)),
        ]
        if xo_update_fields:
            steps.append(UpdateStep(
                step_id="update-record", target=target,
                field_changes=dict(xo_update_fields)))
        steps += [
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
        operation=trigger_op, target=target,
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


def author_emission(grounded: object, *,
                    enable_bva_boundaries: bool = False) -> EmissionBundle:
    """Author the claim + recipe bodies for a grounded candidate. The single
    site that constructs S2 body models for generation (D-097.5); dispatches on
    the grounding shape. ``enable_bva_boundaries`` (D-300, the tenant flag via
    the request's operational context) is consumed by the prohibition author
    only; default OFF = byte-identical."""
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
    if isinstance(grounded, GroundedAcceptance):
        return _author_acceptance(grounded)
    if isinstance(grounded, GroundedNegative):
        return _author_negative(grounded,
                                enable_bva_boundaries=enable_bva_boundaries)
    if isinstance(grounded, GroundedPositive):
        return _author_positive(grounded)
    if isinstance(grounded, GroundedStateTransition):
        return _author_state_transition(grounded)
    if isinstance(grounded, GroundedAutomationEffect):
        return _author_automation_effect(grounded)
    raise TypeError(f"author_emission: unsupported grounding {type(grounded).__name__}")

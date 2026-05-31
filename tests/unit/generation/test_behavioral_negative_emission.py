"""Unit tests for the S3 behavioral-negative emission (D-110.3, S3-thin) — pure,
offline, constructed GroundedNegatives.

A **verified** negative (a VR formula the D-107 parser derives) now emits the
**behavioral** data-recipe — a create carrying the parser's violating payload +
`expect_rejection` — instead of the inspection re-verify. A **caveated** negative
(no derivable formula) stays inspection. The claim's `identity_hash` is **stable**
across both (the violating payload lives in the recipe, not the claim — the
Option-C invariant).
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.emission import (
    GroundedNegative,
    _Endpoint,
    author_emission,
)
from primeqa.test_representation.identity_hash import compute_identity_hash
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)
from primeqa.test_representation.models.recipes.metadata_recipe import (
    MetadataRecipeBody,
)
from primeqa.test_representation.models.triggers.data_mutation import (
    DataMutationTriggerBody,
)
from primeqa.test_representation.models.triggers.inspection import (
    InspectionTriggerBody,
)

_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


def _grounded(*, formulas, external_id="Lead"):
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id=external_id),
        requirement_excerpt="Users must not save a Lead without a reason.",
        vr_formulas=formulas)


# A derivable formula → VerifiedNegative; a non-derivable one → caveated.
_DERIVABLE = ("ISBLANK(Reason__c)",)          # -> {Reason__c: None}
_DERIVABLE_CMP = ("Amount__c = 0",)            # -> {Amount__c: 0}
_NOT_DERIVABLE = ("ISCHANGED(Status__c)",)     # org-state -> NotDerivable -> caveated
_NONE = ()                                     # no formula -> caveated


# ---------------------------------------------------------------------------
# Verified → behavioral
# ---------------------------------------------------------------------------

def test_verified_negative_emits_behavioral_recipe():
    bundle = author_emission(_grounded(formulas=_DERIVABLE))

    assert bundle.trigger_kind == "data-mutation-trigger"
    assert bundle.recipe_kind == "data-recipe"
    assert isinstance(bundle.causal_initiation, DataMutationTriggerBody)
    assert bundle.causal_initiation.operation == "create"
    assert isinstance(bundle.observation_realization, DataRecipeBody)

    steps = bundle.observation_realization.steps
    assert len(steps) == 1 and isinstance(steps[0], CreateStep)
    create = steps[0]
    # the parser's violating payload IS the create's field_values.
    assert create.field_values == {"Reason__c": None}
    assert create.target_object.external_id == "Lead"


def test_violating_payload_from_a_comparison_formula():
    bundle = author_emission(_grounded(formulas=_DERIVABLE_CMP))
    assert bundle.recipe_kind == "data-recipe"
    assert bundle.observation_realization.steps[0].field_values == {"Amount__c": 0}


def test_behavioral_recipe_carries_expect_rejection_projection():
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    expect = bundle.observation_realization.steps[0].expect_rejection
    assert expect is not None
    # the projection (D-110.1): the generic VR code, no error_field.
    assert expect.error_code == _VR_CODE
    assert not hasattr(expect, "error_field")
    # the claim still carries the identity-bearing RejectionSignal (same code).
    assert bundle.asserted_truth.expected_rejection.error_code == _VR_CODE


def test_verified_marker_and_caveat_dropped():
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    # LAYER_2 + caveat dropped (D-107 invariant — unchanged by D-110.3).
    assert bundle.admissibility_layer.value == "layer_2"
    assert bundle.caveat_required is False


# ---------------------------------------------------------------------------
# Caveated → inspection (unchanged)
# ---------------------------------------------------------------------------

def test_non_derivable_formula_stays_inspection():
    bundle = author_emission(_grounded(formulas=_NOT_DERIVABLE))
    assert bundle.trigger_kind == "inspection-trigger"
    assert bundle.recipe_kind == "metadata-recipe"
    assert isinstance(bundle.causal_initiation, InspectionTriggerBody)
    assert isinstance(bundle.observation_realization, MetadataRecipeBody)
    assert bundle.caveat_required is True


def test_no_formula_stays_inspection():
    bundle = author_emission(_grounded(formulas=_NONE))
    assert bundle.recipe_kind == "metadata-recipe"
    assert bundle.caveat_required is True


# ---------------------------------------------------------------------------
# ⚑ The claim identity is STABLE across verified/caveated (Option-C invariant)
# ---------------------------------------------------------------------------

def test_claim_identity_hash_stable_across_verified_and_caveated():
    # Same subject; the ONLY difference is whether the VR formula derives. The
    # behavioral payload lives in the recipe — the CLAIM body must be identical.
    eid = uuid4()
    g_verified = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_Endpoint(entity_id=eid, entity_type="Object", external_id="Lead"),
        requirement_excerpt="x", vr_formulas=_DERIVABLE)
    g_caveated = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_Endpoint(entity_id=eid, entity_type="Object", external_id="Lead"),
        requirement_excerpt="x", vr_formulas=_NOT_DERIVABLE)

    b_v = author_emission(g_verified)
    b_c = author_emission(g_caveated)

    # the recipes differ (behavioral vs inspection)...
    assert b_v.recipe_kind == "data-recipe"
    assert b_c.recipe_kind == "metadata-recipe"
    # ...but the CLAIM bodies are byte-identical -> identity_hash stable.
    assert b_v.asserted_truth.model_dump() == b_c.asserted_truth.model_dump()
    h_v = compute_identity_hash(
        b_v.archetype, b_v.claim_kind, b_v.asserted_truth, b_v.semantic_conditions)
    h_c = compute_identity_hash(
        b_c.archetype, b_c.claim_kind, b_c.asserted_truth, b_c.semantic_conditions)
    assert h_v == h_c


# ---------------------------------------------------------------------------
# The emitted behavioral recipe is well-formed (S2-acceptable shape)
# ---------------------------------------------------------------------------

def test_behavioral_recipe_round_trips_through_registry():
    # Proves the emitted recipe is a valid DataRecipeBody that decodes back
    # (what S2's write_recipe / read path do).
    from primeqa.test_representation.models.registry import get_body_model
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    dumped = bundle.observation_realization.model_dump(mode="json")
    cls = get_body_model(dumped["kind"], dumped["body_schema_version"])
    assert cls is DataRecipeBody
    restored = cls.model_validate(dumped)
    assert restored.steps[0].expect_rejection.error_code == _VR_CODE

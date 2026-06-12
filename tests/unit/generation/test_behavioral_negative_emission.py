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


def test_comparison_formula_with_modify_hint_emits_update_rejected():
    # D-203: a comparison derives BOTH directions, so a modify_* prohibition
    # upgrades to the 2-step update-rejected shape — setup create in the
    # non-violating state, update into violation. Field names come out
    # object-QUALIFIED (the positive vertical's convention; Correction B).
    bundle = author_emission(_grounded(formulas=_DERIVABLE_CMP))
    assert bundle.recipe_kind == "data-recipe"
    assert bundle.causal_initiation.operation == "update"
    setup, mutation = bundle.observation_realization.steps
    assert isinstance(setup, CreateStep) and setup.expect_rejection is None
    assert setup.step_id == "create-setup"
    assert setup.field_values == {"Lead.Amount__c": 1}       # satisfy(False): <> 0
    assert mutation.kind == "update"
    assert mutation.field_changes == {"Lead.Amount__c": 0}   # satisfy(True): = 0
    assert mutation.expect_rejection.error_code == _VR_CODE


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


# ---------------------------------------------------------------------------
# D-203 — graded operation dispatch
# ---------------------------------------------------------------------------

def _grounded_op(operation_hint, formulas):
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint=operation_hint, version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="Lead"),
        requirement_excerpt="x", vr_formulas=formulas)


def test_isblank_with_modify_hint_falls_back_to_create_rejected():
    # Graded fallback: ISBLANK derives only the violating direction (no certain
    # non-blank setup), so modify_record drops back to TODAY'S create-rejected —
    # no regression for the existing corpus.
    bundle = author_emission(_grounded_op("modify_record", _DERIVABLE))
    assert bundle.recipe_kind == "data-recipe"
    assert bundle.causal_initiation.operation == "create"
    assert len(bundle.observation_realization.steps) == 1
    assert bundle.observation_realization.steps[0].field_values == {"Reason__c": None}


def test_create_duplicate_hint_keeps_create_rejected():
    bundle = author_emission(_grounded_op("create_duplicate", _DERIVABLE_CMP))
    assert bundle.causal_initiation.operation == "create"
    steps = bundle.observation_realization.steps
    assert len(steps) == 1 and steps[0].field_values == {"Amount__c": 0}


def test_delete_hint_stays_caveated_even_when_derivable():
    # The semantic-blur fix: VRs never fire on delete — a create-rejected
    # recipe would test the wrong operation, so a delete prohibition routes to
    # the caveated inspection regardless of formula derivability.
    bundle = author_emission(_grounded_op("delete", _DERIVABLE_CMP))
    assert bundle.recipe_kind == "metadata-recipe"
    assert bundle.trigger_kind == "inspection-trigger"
    assert bundle.caveat_required is True


def test_update_shape_is_layer_2_no_caveat():
    bundle = author_emission(_grounded_op("modify_record", _DERIVABLE_CMP))
    assert bundle.admissibility_layer.value == "layer_2"
    assert bundle.caveat_required is False


def test_claim_identity_stable_across_all_three_recipe_shapes():
    # The Option-C invariant extended to D-203: update-rejected vs
    # create-rejected vs caveated-inspection — the CLAIM body never varies.
    eid = uuid4()

    def _g(formulas):
        return GroundedNegative(
            archetype="data_behavior", claim_kind="prohibition-claim",
            operation_hint="modify_record", version_seq=7,
            subject=_Endpoint(entity_id=eid, entity_type="Object",
                              external_id="Lead"),
            requirement_excerpt="x", vr_formulas=formulas)

    b_update = author_emission(_g(_DERIVABLE_CMP))     # 2-step update shape
    b_create = author_emission(_g(_DERIVABLE))         # create-rejected fallback
    b_caveat = author_emission(_g(_NOT_DERIVABLE))     # inspection

    assert b_update.causal_initiation.operation == "update"
    assert b_create.causal_initiation.operation == "create"
    assert b_caveat.recipe_kind == "metadata-recipe"
    assert (b_update.asserted_truth.model_dump()
            == b_create.asserted_truth.model_dump()
            == b_caveat.asserted_truth.model_dump())
    hashes = {
        compute_identity_hash(b.archetype, b.claim_kind,
                              b.asserted_truth, b.semantic_conditions)
        for b in (b_update, b_create, b_caveat)
    }
    assert len(hashes) == 1


def test_update_recipe_round_trips_through_registry():
    from primeqa.test_representation.models.registry import get_body_model
    bundle = author_emission(_grounded_op("modify_record", _DERIVABLE_CMP))
    dumped = bundle.observation_realization.model_dump(mode="json")
    restored = get_body_model(
        dumped["kind"], dumped["body_schema_version"]).model_validate(dumped)
    assert restored.steps[1].kind == "update"
    assert restored.steps[1].expect_rejection.error_code == _VR_CODE


# ---------------------------------------------------------------------------
# D-228 — multi-recipe authoring: a verified negative ALSO carries the
# caveated inspection re-verify as a fallback secondary (priority -10)
# ---------------------------------------------------------------------------

def test_verified_create_rejected_carries_inspection_secondary():
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    assert bundle.recipe_kind == "data-recipe"          # primary is behavioral
    assert len(bundle.secondary_recipes) == 1
    sec = bundle.secondary_recipes[0]
    assert sec.trigger_kind == "inspection-trigger"
    assert sec.recipe_kind == "metadata-recipe"
    assert sec.priority == -10
    assert isinstance(sec.causal_initiation, InspectionTriggerBody)
    assert isinstance(sec.observation_realization, MetadataRecipeBody)
    # the fallback assumes metadata-API auth only — that's what makes it
    # selectable on an env where the behavioral primary cannot run.
    assert [a.auth_kind for a in sec.execution_environment.auth_assumptions] \
        == ["metadata_api_user"]


def test_verified_update_rejected_carries_inspection_secondary():
    bundle = author_emission(_grounded_op("modify_record", _DERIVABLE_CMP))
    assert bundle.causal_initiation.operation == "update"
    assert len(bundle.secondary_recipes) == 1
    assert bundle.secondary_recipes[0].recipe_kind == "metadata-recipe"


def test_caveated_negative_has_no_secondaries():
    # The caveated primary IS the inspection — a secondary would duplicate it.
    bundle = author_emission(_grounded(formulas=_NOT_DERIVABLE))
    assert bundle.recipe_kind == "metadata-recipe"
    assert bundle.secondary_recipes == ()


def test_secondary_round_trips_through_registry():
    from primeqa.test_representation.models.registry import get_body_model
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    sec = bundle.secondary_recipes[0]
    dumped = sec.observation_realization.model_dump(mode="json")
    restored = get_body_model(
        dumped["kind"], dumped["body_schema_version"]).model_validate(dumped)
    assert restored.steps[0].fields_to_capture == ["APPLIES_TO"]


def test_secondary_does_not_perturb_claim_identity():
    # Option-C invariant extended to D-228: secondaries are operational layers;
    # the claim body + identity_hash are byte-identical with or without them.
    eid = uuid4()

    def _g(formulas):
        return GroundedNegative(
            archetype="data_behavior", claim_kind="prohibition-claim",
            operation_hint="modify_record", version_seq=7,
            subject=_Endpoint(entity_id=eid, entity_type="Object",
                              external_id="Lead"),
            requirement_excerpt="x", vr_formulas=formulas)

    b_with = author_emission(_g(_DERIVABLE))         # 1 secondary
    b_without = author_emission(_g(_NOT_DERIVABLE))  # 0 secondaries
    assert len(b_with.secondary_recipes) == 1
    assert b_without.secondary_recipes == ()
    h_with = compute_identity_hash(
        b_with.archetype, b_with.claim_kind,
        b_with.asserted_truth, b_with.semantic_conditions)
    h_without = compute_identity_hash(
        b_without.archetype, b_without.claim_kind,
        b_without.asserted_truth, b_without.semantic_conditions)
    assert h_with == h_without

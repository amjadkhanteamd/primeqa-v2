"""Unit tests for positive value-claim emission-authoring (D-115 slice 1 side A).

`author_emission(GroundedPositive(...))` authors a value-claim + a positive
create-and-verify **data** recipe (Create `field=V` no rejection -> Read -> Assert
`equals` `field==V`). The grounding fact is constructed directly here, mirroring how
the negative emission is tested (`author_emission(GroundedNegative(...))`).

The governance grounding stash that would build a `GroundedPositive` from a real
intent is **held** (the synthesis->intent contract decision), so `value-claim` stays
out of `EMITTABLE` and a real grounded value-claim keeps deferring `EMISSION_DEFERRED`
at the resolve gate — the author-capability exists but is unreachable from a real
requirement until the stash lands.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.emission import (
    EMITTABLE,
    GroundedPositive,
    _Endpoint,
    author_emission,
)
from primeqa.generation.enums import AdmissibilityLayer
from primeqa.test_representation.models.claims.data_behavior.value_claim import (
    ValueClaimBody,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    AssertStep,
    CreateStep,
    DataRecipeBody,
    ReadStep,
)


def _grounded(field="Industry", obj="Account", value="Technology"):
    return GroundedPositive(
        archetype="data_behavior", claim_kind="value-claim", version_seq=7,
        target_object=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id=obj),
        field=_Endpoint(entity_id=uuid4(), entity_type="Field", external_id=field),
        value=value, requirement_excerpt=f"{obj}.{field} should be {value}.")


def test_emits_positive_create_and_verify_recipe():
    b = author_emission(_grounded())
    # claim: a value-claim
    assert isinstance(b.asserted_truth, ValueClaimBody)
    assert b.asserted_truth.kind == "value-claim"
    # recipe: a data recipe -> create -> read -> assert
    assert b.recipe_kind == "data-recipe"
    assert b.trigger_kind == "data-mutation-trigger"
    recipe = b.observation_realization
    assert isinstance(recipe, DataRecipeBody)
    create, read, assertion = recipe.steps
    assert isinstance(create, CreateStep)
    assert isinstance(read, ReadStep)
    assert isinstance(assertion, AssertStep)
    # the create is POSITIVE — no expect_rejection (contrast the behavioral negative)
    assert create.expect_rejection is None
    # the read captures the field; the assert is an equals over it
    assert read.fields_to_capture == ["Industry"]
    assert assertion.predicate.predicate == "equals"


def test_create_step_carries_the_semantic_field_only():
    # k16: the CreateStep sets ONLY the field under test — not a complete payload.
    # S4 fills the operational required-field padding at execution.
    create = author_emission(_grounded()).observation_realization.steps[0]
    assert create.field_values == {"Industry": "Technology"}
    assert list(create.field_values.keys()) == ["Industry"]


def test_value_carried_verbatim_in_create_assert_and_claim():
    # The same V threads into the create (set it), the assert (verify it), and the
    # claim's expected_value (the asserted truth) — never derived or invented.
    V = "Healthcare"
    b = author_emission(_grounded(value=V))
    create, _read, assertion = b.observation_realization.steps
    assert create.field_values["Industry"] == V
    assert assertion.predicate.value == V
    assert b.asserted_truth.expected_value.value == V


def test_positive_is_layer1_uncaveated():
    b = author_emission(_grounded())
    assert b.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert b.caveat_required is False
    assert b.caveat_kind is None


def test_value_claim_is_emittable_after_grounding_stash():
    # D-115.3 (Option Q resolved): the grounding stash + the EMITTABLE entry landed
    # together. A real grounded value-claim now reaches PROCEED_TO_EMIT (resolve_intent
    # stashes a GroundedPositive); the gate admits it because author_emission can author
    # it. The EMITTABLE <-> author_emission lockstep is bound by the drift-guard
    # (test_governance_unit.test_emittable_set_matches_author_emission_shapes).
    assert ("data_behavior", "value-claim") in EMITTABLE

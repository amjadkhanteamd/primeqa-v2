"""Integration: the Coordinator accepts a behavioral-negative data-recipe
(D-110.1).

Proves the full write path — registry dispatch, body validation, the
operational-layer `_verify_no_identity_bearing_refs` walk — accepts a
`data-recipe` whose `CreateStep` carries `expect_rejection`
(`RejectionExpectation`), and that it reads back with the expectation intact.
This is the end-to-end confirmation that the operational projection (scalar-only)
clears the identity-bearing-ref boundary the claim's `RejectionSignal` would trip.
"""
from __future__ import annotations

from primeqa.test_representation import SemanticTransactionCoordinator
from primeqa.test_representation.models.recipes.data_recipe import DataRecipeBody

from ._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_execution_environment_body,
)
from ._fixtures import arrange_approved_claim

_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


def _negative_data_recipe() -> DataRecipeBody:
    return DataRecipeBody.model_validate({
        "api_choice": "rest",
        "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": [{
            "kind": "create",
            "step_id": "create-violating",
            "target_object": {"ref_kind": "logical",
                              "entity_type": "Object", "external_id": "Lead"},
            "field_values": {"Company": "Acme"},
            "expect_rejection": {"error_code": _VR_CODE},
        }],
    })


def test_write_recipe_accepts_behavioral_negative(session):
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)

    written = coord.write_recipe(
        session, actor="human", recipe_id=None, claim_test_id=test_id,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=_negative_data_recipe(),
        execution_environment=build_minimal_execution_environment_body())

    assert written.recipe_id is not None

    # Read back through the Coordinator (registry-decoded) — the expectation
    # survives the JSONB round-trip + the operational-layer walk.
    read = coord.get_recipe_latest(session, written.recipe_id)
    step = read.observation_realization.steps[0]
    assert step.expect_rejection is not None
    assert step.expect_rejection.error_code == _VR_CODE

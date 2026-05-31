"""Live VR-rejection proof: the behavioral-negative spine matches a REAL
validation-rule rejection (D-110.3).

Gated on @pytest.mark.sandbox + SF_* env vars AND this package's ``session``
fixture. The **product-realistic** counterpart of the required-field mechanism
proof (`test_s4_data_negative_live.py`): instead of a platform `REQUIRED_FIELD_MISSING`,
this seeds the behavioral negative for a real **validation rule** — the
managed-package ``Contract_is_Required`` VR on ``CHANNEL_ORDERS__Service_Order__c``,
whose ``ISBLANK( CHANNEL_ORDERS__Partner_Contract_Rules__c )`` formula the D-107
parser derives to ``{…: None}`` — and asserts the org rejects the create with
``FIELD_CUSTOM_VALIDATION_EXCEPTION`` → ``matched`` → ``passed``.

This is exactly the S3-thin emission path (the violating payload the parser
derives = the create's field_values), proven against a live org. The VR is a
managed-package rule; a standard-object demo VR is a separate sandbox-content
task (DEFERRED_ITEMS). A rejected create leaves no record, so the test is
self-contained.

Run with:
    pytest tests/integration/test_representation/test_s4_vr_negative_live.py -v -m sandbox
"""
from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

REQUIRED_ENV = ("SF_INSTANCE_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_REFRESH_TOKEN")
HAS_SANDBOX_CREDS = all(os.environ.get(k) for k in REQUIRED_ENV)

pytestmark = pytest.mark.sandbox

_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"
_SOBJECT = "CHANNEL_ORDERS__Service_Order__c"
# The VR formula the D-107 parser derives → the violating payload (S3-thin path).
_VR_FORMULA = "ISBLANK( CHANNEL_ORDERS__Partner_Contract_Rules__c )"


def _real_data_client():
    from primeqa.integrations.sf_client import SF_API_VERSION, SalesforceClient
    from primeqa.execution_engine.data_mutation_client import DataMutationClient

    with SalesforceClient(
        instance_url=os.environ["SF_INSTANCE_URL"],
        client_id=os.environ["SF_CLIENT_ID"],
        client_secret=os.environ["SF_CLIENT_SECRET"],
        refresh_token=os.environ["SF_REFRESH_TOKEN"],
    ) as c:
        c._refresh_access_token()
        token = c._access_token
    return DataMutationClient(os.environ["SF_INSTANCE_URL"], SF_API_VERSION, token)


def _derived_violating_payload() -> dict:
    """The exact payload S3's `_author_negative` would emit for this VR — the
    D-107 parser's derivation, so the test exercises the real emission path."""
    from primeqa.generation.verified_negative import VerifiedNegative, derive
    from primeqa.semantic.formula import parse

    result = derive(parse(_VR_FORMULA))
    assert isinstance(result, VerifiedNegative)        # the formula IS derivable
    return result.violating_payload


def _seed_approved_vr_negative(session, coord, violating_payload):
    from primeqa.test_representation.models.recipes.data_recipe import DataRecipeBody
    from primeqa.test_representation.models.triggers.data_mutation import (
        DataMutationTriggerBody,
    )

    from ._builders import build_minimal_execution_environment_body
    from ._fixtures import arrange_approved_claim

    test_id, _ = arrange_approved_claim(session, coord)
    target = {"ref_kind": "logical", "entity_type": "Object", "external_id": _SOBJECT}
    trigger = DataMutationTriggerBody.model_validate({
        "operation": "create", "target": target,
        "identity_context": "system", "volume": "single"})
    body = DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": [{
            "kind": "create", "step_id": "create-violating",
            "target_object": target,
            "field_values": violating_payload,
            "expect_rejection": {"error_code": _VR_CODE},
        }]})
    written = coord.write_recipe(
        session, actor="human", recipe_id=None, claim_test_id=test_id,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=trigger, observation_realization=body,
        execution_environment=build_minimal_execution_environment_body())
    session.execute(text(
        "UPDATE test_recipes SET status = 'approved' "
        "WHERE recipe_id = :r AND version_seq = :v"),
        {"r": str(written.recipe_id), "v": written.version_seq})
    session.flush()
    return test_id, written.recipe_id


def test_vr_negative_spine_live(session):
    if not HAS_SANDBOX_CREDS:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        pytest.skip(f"Sandbox credentials not configured (missing: {missing})")

    from primeqa.execution_engine.result_store import S4ExecutionRun
    from primeqa.execution_engine.run import run_recipe_execution
    from primeqa.test_representation import SemanticTransactionCoordinator

    coord = SemanticTransactionCoordinator()
    payload = _derived_violating_payload()
    test_id, recipe_id = _seed_approved_vr_negative(session, coord, payload)

    client = _real_data_client()
    try:
        result = run_recipe_execution(
            session, test_id, environment_id=0, client=client, coordinator=coord)
    finally:
        client.close()

    # The VR fired → the rejection matched the expected code → passed.
    assert result.ran is True
    assert result.selected_recipe_id == recipe_id
    assert result.evidence.outcome == "passed"

    step = result.evidence.steps[0]
    assert step.kind == "create"
    assert step.success is False and step.matched is True
    assert step.error_code == _VR_CODE
    assert _VR_CODE in [e.get("errorCode") for e in step.rejection_body]
    assert step.cleanup.attempted is False        # a rejected create creates nothing

    # both rows persisted; posture agrees.
    run_row = (session.query(S4ExecutionRun)
               .filter(S4ExecutionRun.run_id == result.evidence.run_id).one())
    assert run_row.outcome == "passed"
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state.last_run_outcome == "passed"

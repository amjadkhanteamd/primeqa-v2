"""Live spine proof: the data-recipe behavioral-negative vertical end-to-end
against the real Salesforce sandbox (D-110.2 slice 3).

Gated on @pytest.mark.sandbox + SF_* env vars (the slice-2 gate) AND this
package's ``session`` fixture (the migrated tenant_1 DB). Seeds an approved
behavioral-negative data-recipe — a create on **Opportunity** omitting required
fields, ``expect_rejection = REQUIRED_FIELD_MISSING`` — then runs the whole spine
(`select → dispatch → bridge → execute-LIVE → finalize`) with a **real injected
DataMutationClient**, and asserts the org rejects the create with the expected
code → `matched` → `passed`.

**Mechanism / spine proof, not the product use case.** It uses a deterministic
*platform* rejection (a missing-required-field, which Salesforce always returns
as ``REQUIRED_FIELD_MISSING``) — reliable, self-contained (a rejected create
creates nothing → no cleanup), and **org-independent** (no sandbox VR required).
The product case (a validation rule firing → ``FIELD_CUSTOM_VALIDATION_EXCEPTION``)
is the same spine with org-specific content; its live proof is opportunistic /
deferred (D-110.2 N-4).

Run with:
    pytest tests/integration/test_representation/test_s4_data_negative_live.py -v -m sandbox
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

REQUIRED_ENV = ("SF_INSTANCE_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_REFRESH_TOKEN")
HAS_SANDBOX_CREDS = all(os.environ.get(k) for k in REQUIRED_ENV)

pytestmark = pytest.mark.sandbox

_REQUIRED_FIELD_MISSING = "REQUIRED_FIELD_MISSING"


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


def _seed_approved_negative_recipe(session, coord):
    """Approved claim + an approved behavioral-negative data-recipe: a create
    on Opportunity missing required fields, expecting REQUIRED_FIELD_MISSING."""
    from primeqa.test_representation.models.recipes.data_recipe import DataRecipeBody
    from primeqa.test_representation.models.triggers.data_mutation import (
        DataMutationTriggerBody,
    )

    from ._builders import build_minimal_execution_environment_body
    from ._fixtures import arrange_approved_claim

    test_id, _ = arrange_approved_claim(session, coord)
    trigger = DataMutationTriggerBody.model_validate({
        "operation": "create",
        "target": {"ref_kind": "logical", "entity_type": "Object", "external_id": "Opportunity"},
        "identity_context": "system", "volume": "single"})
    body = DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": [{
            "kind": "create", "step_id": "create-incomplete-opportunity",
            "target_object": {"ref_kind": "logical", "entity_type": "Object", "external_id": "Opportunity"},
            # Name only — StageName + CloseDate are required → REQUIRED_FIELD_MISSING.
            "field_values": {"Name": f"PQA_NEG_{uuid4().hex[:8]}"},
            "expect_rejection": {"error_code": _REQUIRED_FIELD_MISSING},
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


def test_data_negative_spine_live(session):
    if not HAS_SANDBOX_CREDS:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        pytest.skip(f"Sandbox credentials not configured (missing: {missing})")

    from primeqa.execution_engine.result_store import S4ExecutionRun
    from primeqa.execution_engine.run import run_recipe_execution
    from primeqa.test_representation import SemanticTransactionCoordinator

    coord = SemanticTransactionCoordinator()
    test_id, recipe_id = _seed_approved_negative_recipe(session, coord)

    client = _real_data_client()
    try:
        result = run_recipe_execution(
            session, test_id, environment_id=0, client=client, coordinator=coord)
    finally:
        client.close()

    # The org rejected the create with the expected code → matched → passed.
    assert result.ran is True
    assert result.selected_recipe_id == recipe_id
    assert result.evidence.outcome == "passed"

    step = result.evidence.steps[0]
    assert step.kind == "create"
    assert step.success is False and step.matched is True
    assert step.error_code == _REQUIRED_FIELD_MISSING
    assert step.cleanup.attempted is False        # a rejected create creates nothing

    # both rows persisted; posture agrees.
    run_row = (session.query(S4ExecutionRun)
               .filter(S4ExecutionRun.run_id == result.evidence.run_id).one())
    assert run_row.outcome == "passed"
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state.last_run_id == result.evidence.run_id
    assert state.last_run_outcome == "passed"

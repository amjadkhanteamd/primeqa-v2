"""Live whole-spine test: run the run path against the real Salesforce sandbox
(D-108.4).

Gated on @pytest.mark.sandbox + SF_* env vars (the slice-2 gate) AND this
package's ``session`` fixture (the migrated tenant_1 DB). Seeds an approved
metadata-inspection recipe in tenant_1, then runs ``run_recipe_execution`` with a
**real injected ToolingReadClient** (the local test DB has no
``environments``/``connections`` for ``resolve_tooling_client``, D-108.4) — so
select → bridge → execute-LIVE → finalize runs against the org, and both result
rows land.

Run with:
    pytest tests/integration/test_representation/test_s4_run_path_live.py -v -m sandbox
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


def _real_client():
    from primeqa.integrations.sf_client import SF_API_VERSION, SalesforceClient
    from primeqa.execution_engine.tooling_client import ToolingReadClient

    with SalesforceClient(
        instance_url=os.environ["SF_INSTANCE_URL"],
        client_id=os.environ["SF_CLIENT_ID"],
        client_secret=os.environ["SF_CLIENT_SECRET"],
        refresh_token=os.environ["SF_REFRESH_TOKEN"],
    ) as c:
        c._refresh_access_token()
        token = c._access_token
    return ToolingReadClient(os.environ["SF_INSTANCE_URL"], SF_API_VERSION, token)


def _seed_approved_inspection_recipe(session, coord, subject):
    from primeqa.generation.emission import GroundedNegative, _Endpoint, author_emission
    from ._fixtures import arrange_approved_claim

    test_id, _ = arrange_approved_claim(session, coord)
    bundle = author_emission(GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="delete", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id=subject),
        requirement_excerpt=f"Users must not delete a {subject} without a reason."))
    written = coord.write_recipe(
        session, actor="human", recipe_id=None, claim_test_id=test_id,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment)
    session.execute(text(
        "UPDATE test_recipes SET status = 'approved' "
        "WHERE recipe_id = :r AND version_seq = :v"),
        {"r": str(written.recipe_id), "v": written.version_seq})
    session.flush()
    return test_id, written.recipe_id


@pytest.mark.parametrize("subject", ["Account", "Lead"])
def test_run_path_whole_spine_live(session, subject):
    """select → bridge → execute-live → finalize, end to end against the org."""
    if not HAS_SANDBOX_CREDS:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        pytest.skip(f"Sandbox credentials not configured (missing: {missing})")

    from primeqa.execution_engine.result_store import S4ExecutionRun
    from primeqa.execution_engine.run import run_recipe_execution
    from primeqa.test_representation import SemanticTransactionCoordinator

    coord = SemanticTransactionCoordinator()
    test_id, recipe_id = _seed_approved_inspection_recipe(session, coord, subject)
    client = _real_client()
    try:
        result = run_recipe_execution(
            session, test_id, environment_id=0, client=client, coordinator=coord)
    finally:
        client.close()

    # The read ran against the org → a grounded outcome (never errored).
    assert result.ran is True
    assert result.selected_recipe_id == recipe_id
    assert result.evidence.outcome in ("passed", "failed")
    assert result.evidence.error is None

    # both rows persisted, and the posture agrees with what S4 observed.
    run_row = (session.query(S4ExecutionRun)
               .filter(S4ExecutionRun.run_id == result.evidence.run_id).one())
    assert run_row.recipe_id == recipe_id
    assert run_row.evidence["steps"][0]["subject_external_id"] == subject
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state.last_run_id == result.evidence.run_id
    assert state.last_run_outcome == result.evidence.outcome

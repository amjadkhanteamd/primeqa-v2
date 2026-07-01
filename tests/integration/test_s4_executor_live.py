"""Live verification of the S4 scoped `APPLIES_TO` Tooling query (D-108.1,
Decision 2 — "scoped first, verify live").

Gated on @pytest.mark.sandbox + the SF_* env vars (same gate as
test_sf_client_live.py); skips automatically when creds aren't set.

What it verifies: the translator's scoped filter
`WHERE EntityDefinition.QualifiedApiName = '<Object>'` is **valid SOQL the live
Tooling API accepts** (not the >1000-row unscoped traversal the S1-sync fetcher
avoids), run through the thin S4-local ToolingReadClient. An access token is
minted from the available refresh-token creds.

Run with:
    pytest tests/integration/test_s4_executor_live.py -v -m sandbox
"""
from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = ("SF_INSTANCE_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_REFRESH_TOKEN")
HAS_SANDBOX_CREDS = all(os.environ.get(k) for k in REQUIRED_ENV)

pytestmark = pytest.mark.sandbox


def _access_token() -> str:
    """Mint a fresh access token from the sandbox refresh-token creds."""
    from primeqa.integrations.sf_client import SalesforceClient

    with SalesforceClient(
        instance_url=os.environ["SF_INSTANCE_URL"],
        client_id=os.environ["SF_CLIENT_ID"],
        client_secret=os.environ["SF_CLIENT_SECRET"],
        refresh_token=os.environ["SF_REFRESH_TOKEN"],
    ) as c:
        c._refresh_access_token()
        return c._access_token


@pytest.mark.parametrize("object_api_name", ["Account", "Lead"])
def test_scoped_applies_to_query_is_valid_live(object_api_name):
    """The scoped EntityDefinition filter runs against the live org without
    error and returns a (possibly empty) record list — proving the scoped
    translation is accepted, so no bulk-fetch fallback is needed."""
    if not HAS_SANDBOX_CREDS:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        pytest.skip(f"Sandbox credentials not configured (missing: {missing})")

    from primeqa.integrations.sf_client import SF_API_VERSION
    from primeqa.execution_engine.plan import PlannedRead
    from primeqa.execution_engine.tooling_client import ToolingReadClient
    from primeqa.execution_engine.translator import translate_read
    from primeqa.test_representation.models.references import LogicalRef

    query = translate_read(PlannedRead(
        step_id="read-subject",
        target_entity=LogicalRef(entity_type="Object", external_id=object_api_name),
        fields_to_capture=("APPLIES_TO",)))

    # Pass the v-prefixed constant raw — the client normalizes it (and so this
    # also exercises the bare/v-prefixed guard against the live org).
    client = ToolingReadClient(
        os.environ["SF_INSTANCE_URL"], SF_API_VERSION, _access_token())
    try:
        rows = client.query(query.soql)
    finally:
        client.close()

    # The scoped filter is valid SOQL the Tooling API accepts. We assert shape,
    # not cardinality (VR presence is org-specific) — the point is "no error".
    assert isinstance(rows, list)
    for r in rows:
        assert "Id" in r


def test_executor_runs_end_to_end_live():
    """The whole slice-2 executor against the live org: an emitted inspection
    plan -> translate -> read -> eval `exists` -> grounded outcome + evidence.
    Asserts the run resolves to a real outcome (passed | failed — never errored)
    and the evidence captures the query + filter."""
    if not HAS_SANDBOX_CREDS:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        pytest.skip(f"Sandbox credentials not configured (missing: {missing})")

    from uuid import uuid4
    from datetime import datetime, timezone

    from primeqa.integrations.sf_client import SF_API_VERSION
    from primeqa.execution_engine import (
        build_metadata_inspection_plan, execute_metadata_inspection,
    )
    from primeqa.execution_engine.tooling_client import ToolingReadClient
    from types import SimpleNamespace
    from primeqa.generation.emission import _inspection_recipe
    from primeqa.test_representation.coordinator import RecipeRead

    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    # D-293 removed the prohibition -> inspection emission this used to source
    # from; build the inspection recipe directly (a live metadata read over the
    # subject — the executor is a generic inspection runner).
    _trigger, _recipe, _env = _inspection_recipe(
        read_entity_type="Object", read_external_id="Account", capture_field="APPLIES_TO",
        env_detail="read Account metadata to verify a validation rule applies")
    bundle = SimpleNamespace(
        causal_initiation=_trigger, observation_realization=_recipe,
        execution_environment=_env)
    plan = build_metadata_inspection_plan(RecipeRead(
        recipe_id=uuid4(), version_seq=1, valid_from=now, valid_to=None,
        claim_test_id=uuid4(), claim_version_seq=None,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment,
        priority=0, status="generated_unapproved", created_at=now, updated_at=now))

    client = ToolingReadClient(
        os.environ["SF_INSTANCE_URL"], SF_API_VERSION, _access_token())
    try:
        ev = execute_metadata_inspection(plan, client=client, environment_id=0)
    finally:
        client.close()

    # The read succeeded against the org, so the outcome is grounded (not errored).
    assert ev.outcome in ("passed", "failed")
    assert ev.error is None
    read, assertion = ev.steps
    assert read.subject_external_id == "Account"
    assert "EntityDefinition.QualifiedApiName = 'Account'" in read.query
    # `exists` outcome agrees with the row count S4 actually observed.
    assert assertion.held == (read.row_count > 0)
    assert (ev.outcome == "passed") == assertion.held

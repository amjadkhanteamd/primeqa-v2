"""Unit tests for the async run path (D-129) — the brief-transaction wrapper.

`run_recipe_execution_async` brackets the live read with brief transactions so
**no DB connection is held across Salesforce I/O**. These tests drive it with an
injected ``session_scope`` (a fake that tracks open depth), a spy coordinator,
and an asserting client that fails if a scope is open when it is queried — the
load-bearing invariant — with no real PG.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.execution_engine.run import run_recipe_execution_async
from primeqa.generation.emission import (
    GroundedExistence,
    _Endpoint,
    author_emission,
)
from primeqa.test_representation.coordinator import RecipeRead
from primeqa.test_representation.models.environment import ExecutionEnvironmentBody
from primeqa.test_representation.models.recipes.data_recipe import DataRecipeBody
from primeqa.test_representation.models.triggers.data_mutation import (
    DataMutationTriggerBody,
)

_NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fakes: a scope that tracks open depth, a session, a spy coordinator, a client
# that asserts NO scope is open when it is queried.
# ---------------------------------------------------------------------------

class _FakeSession:
    """add/flush only — no begin_nested, so the best-effort interpret step
    fails fast and returns None (D-111.2), exactly as the sync dispatch tests."""

    def add(self, row):
        pass

    def flush(self):
        pass


class _ScopeTracker:
    """An injectable ``session_scope``: increments open depth on enter,
    decrements on exit. ``opens`` counts how many distinct brackets opened."""

    def __init__(self):
        self.open_depth = 0
        self.opens = 0

    @contextmanager
    def scope(self, tenant_id):
        self.open_depth += 1
        self.opens += 1
        try:
            yield _FakeSession()
        finally:
            self.open_depth -= 1


class _AssertingClient:
    """The live read/mutation must run with NO DB connection held — every call
    asserts the scope tracker's open depth is 0 when it fires."""

    def __init__(self, tracker, rows):
        self._tracker = tracker
        self._rows = rows
        self.queries = []
        self.creates = []

    def query(self, soql):
        assert self._tracker.open_depth == 0, "DB connection held during live read!"
        self.queries.append(soql)
        return list(self._rows)

    def create(self, sobject, field_values):
        # D-230.2: the async DATA path must create with NO connection held either.
        assert self._tracker.open_depth == 0, "DB connection held during live create!"
        self.creates.append((sobject, dict(field_values)))
        # A 400 business rejection matching the recipe's expect_rejection → passed.
        return {"success": False, "http_status": 400, "record_id": None,
                "api_response": {"body": [
                    {"errorCode": "FIELD_CUSTOM_VALIDATION_EXCEPTION",
                     "message": "blocked by validation rule"}]}}


class _SpyCoordinator:
    def __init__(self, recipe):
        self._recipe = recipe
        self.report_calls = []

    def select_recipe_for_execution(self, session, test_id, *, available_environment, replay_mode):
        return self._recipe

    def report_run_outcome(self, session, **kwargs):
        self.report_calls.append(kwargs)
        return {"runtime_state": "stub"}


def _wrap(causal, obs, env, *, trigger_kind, recipe_kind):
    return RecipeRead(
        recipe_id=uuid4(), version_seq=2, valid_from=_NOW, valid_to=None,
        claim_test_id=uuid4(), claim_version_seq=None, trigger_kind=trigger_kind,
        recipe_kind=recipe_kind, causal_initiation=causal, observation_realization=obs,
        execution_environment=env, priority=0, status="approved",
        created_at=_NOW, updated_at=_NOW)


def _existence_recipe():
    bundle = author_emission(GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id="Account"),
        requirement_excerpt="Account exists"))
    return _wrap(bundle.causal_initiation, bundle.observation_realization,
                 bundle.execution_environment,
                 trigger_kind="inspection-trigger", recipe_kind="metadata-recipe")


def _data_recipe():
    trigger = DataMutationTriggerBody.model_validate({
        "operation": "create",
        "target": {"ref_kind": "logical", "entity_type": "Object", "external_id": "Lead"},
        "identity_context": "system", "volume": "single"})
    body = DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": [{"kind": "create", "step_id": "c1",
                   "target_object": {"ref_kind": "logical", "entity_type": "Object", "external_id": "Lead"},
                   "field_values": {"Company": "Acme"},
                   "expect_rejection": {"error_code": "FIELD_CUSTOM_VALIDATION_EXCEPTION"}}]})
    return _wrap(trigger, body, ExecutionEnvironmentBody(),
                 trigger_kind="data-mutation-trigger", recipe_kind="data-recipe")


# ---------------------------------------------------------------------------
# The invariant + the brackets
# ---------------------------------------------------------------------------

def test_no_db_connection_held_during_live_read():
    tracker = _ScopeTracker()
    coord = _SpyCoordinator(_existence_recipe())
    client = _AssertingClient(tracker, rows=[{"QualifiedApiName": "Account"}])

    result = run_recipe_execution_async(
        tenant_id=1, test_id=uuid4(), environment_id=7, client=client,
        coordinator=coord, session_scope=tracker.scope)

    assert result.ran is True
    assert result.evidence.outcome == "passed"
    # the live read actually ran (and its in-query assertion held).
    assert client.queries and "FROM EntityDefinition" in client.queries[0]
    # exactly TWO brackets opened (select TX + persist TX), both closed.
    assert tracker.opens == 2
    assert tracker.open_depth == 0
    # posture was reported in the persist bracket.
    assert coord.report_calls


def test_select_and_persist_are_distinct_transactions():
    # the select bracket closes before the persist bracket opens — proven by the
    # open counter never exceeding 1 (no nesting) yet reaching 2 total opens.
    tracker = _ScopeTracker()

    depths = []
    orig = tracker.scope

    # wrap the scope to record the max depth seen.
    @contextmanager
    def recording_scope(tenant_id):
        with orig(tenant_id) as s:
            depths.append(tracker.open_depth)
            yield s

    coord = _SpyCoordinator(_existence_recipe())
    run_recipe_execution_async(
        tenant_id=1, test_id=uuid4(), environment_id=7,
        client=_AssertingClient(tracker, rows=[{"QualifiedApiName": "Account"}]),
        coordinator=coord, session_scope=recording_scope)

    assert depths == [1, 1]   # each bracket saw depth 1 — never nested


def test_data_recipe_async_runs_with_no_connection_held():
    # D-230.2: the async path now brackets the DATA path too — the D-129 deferral is
    # lifted. A 1-step create-rejected negative runs through execute holding NO DB
    # connection (the create asserts open_depth==0); two brackets open (select+prep,
    # then persist), and the live create fires between them.
    tracker = _ScopeTracker()
    coord = _SpyCoordinator(_data_recipe())
    client = _AssertingClient(tracker, rows=[])
    result = run_recipe_execution_async(
        tenant_id=1, test_id=uuid4(), environment_id=7, client=client,
        coordinator=coord, session_scope=tracker.scope)

    assert result.ran is True
    assert result.evidence.outcome == "passed"          # the 400 matched the expectation
    assert client.creates == [("Lead", {"Company": "Acme"})]   # fired with no scope open
    assert tracker.opens == 2                            # select+prep TX, then persist TX
    assert tracker.open_depth == 0


def test_no_eligible_recipe_returns_ran_false():
    tracker = _ScopeTracker()
    coord = _SpyCoordinator(None)   # select returns None
    result = run_recipe_execution_async(
        tenant_id=1, test_id=uuid4(), environment_id=7,
        client=_AssertingClient(tracker, rows=[]),
        coordinator=coord, session_scope=tracker.scope)
    assert result.ran is False
    assert result.reason == "no_eligible_recipe"
    assert tracker.opens == 1   # only the select bracket

"""Unit tests for the run-path recipe-kind dispatch (D-110.2 slice 3) — stub
client + spy coordinator, no DB.

`run_recipe_execution` dispatches on `recipe_kind`: `metadata-recipe` → the
inspection path; `data-recipe` → the behavioral-negative path; an unknown kind
fails loud. These tests drive a spy coordinator that returns a canned recipe of
each kind + a stub client, so the whole chain runs with no org and no DB.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.execution_engine.errors import PlanTranslationError
from primeqa.execution_engine.run import run_recipe_execution
from primeqa.generation.emission import (
    GroundedNegative,
    _Endpoint,
    author_emission,
)
from primeqa.test_representation.coordinator import RecipeRead
from primeqa.test_representation.models.environment import ExecutionEnvironmentBody
from primeqa.test_representation.models.recipes.data_recipe import DataRecipeBody
from primeqa.test_representation.models.triggers.data_mutation import (
    DataMutationTriggerBody,
)

_NOW = datetime(2026, 5, 27, tzinfo=timezone.utc)
_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


# ---------------------------------------------------------------------------
# Stub client (serves both verticals) + spy coordinator
# ---------------------------------------------------------------------------

class _StubClient:
    """Serves a read (inspection) AND a create/delete (negative)."""

    def __init__(self, *, rows=None, create_result=None):
        self._rows = list(rows or [{"Id": "1"}])
        self._create_result = create_result
        self.queries, self.creates = [], []

    def query(self, soql):
        self.queries.append(soql)
        return list(self._rows)

    def create(self, sobject, field_values):
        self.creates.append((sobject, field_values))
        return self._create_result

    def delete(self, sobject, record_id):
        return {"success": True}


class _SpyCoordinator:
    def __init__(self, recipe):
        self._recipe = recipe
        self.report_calls = []

    def select_recipe_for_execution(self, session, test_id, *, available_environment, replay_mode):
        return self._recipe

    def report_run_outcome(self, session, **kwargs):
        self.report_calls.append(kwargs)
        return {"runtime_state": "stub", "last_run_outcome": kwargs["last_run_outcome"]}


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


# ---------------------------------------------------------------------------
# Recipe builders, per kind
# ---------------------------------------------------------------------------

def _inspection_recipe():
    bundle = author_emission(GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="delete", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id="Lead"),
        requirement_excerpt="x"))
    return _wrap(bundle.causal_initiation, bundle.observation_realization,
                 bundle.execution_environment,
                 trigger_kind="inspection-trigger", recipe_kind="metadata-recipe")


def _existence_recipe():
    # an existence-claim recipe (D-127): a self-read of the subject's own
    # metadata + assert `exists` — routes through the same metadata path.
    from primeqa.generation.emission import GroundedExistence
    bundle = author_emission(GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id="Account"),
        requirement_excerpt="Account exists"))
    return _wrap(bundle.causal_initiation, bundle.observation_realization,
                 bundle.execution_environment,
                 trigger_kind="inspection-trigger", recipe_kind="metadata-recipe")


def _negative_data_recipe():
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
                   "expect_rejection": {"error_code": _VR_CODE}}]})
    return _wrap(trigger, body, ExecutionEnvironmentBody(),
                 trigger_kind="data-mutation-trigger", recipe_kind="data-recipe")


def _wrap(causal, obs, env, *, trigger_kind, recipe_kind, recipe_id=None):
    return RecipeRead(
        recipe_id=recipe_id or uuid4(), version_seq=2, valid_from=_NOW, valid_to=None,
        claim_test_id=uuid4(), claim_version_seq=None, trigger_kind=trigger_kind,
        recipe_kind=recipe_kind, causal_initiation=causal, observation_realization=obs,
        execution_environment=env, priority=0, status="approved",
        created_at=_NOW, updated_at=_NOW)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def test_metadata_recipe_routes_to_inspection_path():
    coord = _SpyCoordinator(_inspection_recipe())
    client = _StubClient(rows=[{"Id": "03d"}])
    result = run_recipe_execution(
        _FakeSession(), uuid4(), environment_id=7, client=client, coordinator=coord)

    assert result.ran is True
    assert result.evidence.outcome == "passed"
    # the inspection path issued a query, not a create.
    assert len(client.queries) == 1 and client.creates == []
    assert result.evidence.steps[0].kind == "read"


def test_existence_recipe_routes_through_self_read_metadata_path():
    # D-127: an existence recipe routes to the metadata path, issues the
    # EntityDefinition self-read query, and grounds an outcome — no create.
    coord = _SpyCoordinator(_existence_recipe())
    client = _StubClient(rows=[{"QualifiedApiName": "Account"}])
    result = run_recipe_execution(
        _FakeSession(), uuid4(), environment_id=7, client=client, coordinator=coord)

    assert result.ran is True
    assert result.evidence.outcome == "passed"
    assert len(client.queries) == 1 and client.creates == []
    assert "FROM EntityDefinition" in client.queries[0]
    assert result.evidence.steps[0].edge == "sf_api_name"


def test_property_recipe_routes_and_grounds_equals():
    # D-128: a property (length) recipe routes to the metadata path, issues the
    # FieldDefinition self-read, and grounds equals over the captured column.
    from primeqa.generation.emission import GroundedProperty
    bundle = author_emission(GroundedProperty(
        archetype="configuration", claim_kind="property-claim", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Field", external_id="Account.Description"),
        property_name="length", expected_value=255, requirement_excerpt="x"))
    coord = _SpyCoordinator(_wrap(
        bundle.causal_initiation, bundle.observation_realization, bundle.execution_environment,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe"))
    client = _StubClient(rows=[{"Length": 255}])
    result = run_recipe_execution(
        _FakeSession(), uuid4(), environment_id=7, client=client, coordinator=coord)

    assert result.ran is True
    assert result.evidence.outcome == "passed"
    assert "SELECT Length FROM FieldDefinition" in client.queries[0]
    assert result.evidence.steps[1].predicate == "equals"


def test_data_recipe_routes_to_negative_path():
    rid = uuid4()
    coord = _SpyCoordinator(
        _wrap(*_negative_parts(), trigger_kind="data-mutation-trigger",
              recipe_kind="data-recipe", recipe_id=rid))
    client = _StubClient(create_result=_rejected())
    result = run_recipe_execution(
        _FakeSession(), uuid4(), environment_id=7, client=client, coordinator=coord)

    assert result.ran is True
    assert result.selected_recipe_id == rid
    assert result.evidence.outcome == "passed"          # rejected + code matches
    # the negative path issued a create, not a query.
    assert len(client.creates) == 1 and client.queries == []
    assert result.evidence.steps[0].kind == "create"
    assert coord.report_calls[0]["last_run_outcome"] == "passed"


def test_unknown_recipe_kind_fails_loud():
    rid = uuid4()
    coord = _SpyCoordinator(_wrap(
        *_negative_parts(), trigger_kind="ui-trigger", recipe_kind="ui-recipe", recipe_id=rid))
    with pytest.raises(PlanTranslationError, match="no executor for recipe_kind"):
        run_recipe_execution(
            _FakeSession(), uuid4(), environment_id=7,
            client=_StubClient(), coordinator=coord)


# helpers reusing the negative recipe's parts
def _negative_parts():
    r = _negative_data_recipe()
    return r.causal_initiation, r.observation_realization, r.execution_environment


def _rejected():
    return {"api_response": {"status_code": 400,
            "body": [{"errorCode": _VR_CODE, "message": "reason required"}]},
            "http_status": 400, "success": False, "record_id": None}

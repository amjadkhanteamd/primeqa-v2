"""Unit tests for the VR05-arc ENTRY update — bridge projection + executor
semantics, pure stubs (no PG, no live org).

The entry's laws (mirroring the D-333 arc):
  - the entry is STAGING: the org's own transition into the gated prior state
    must SUCCEED; a refusal/raise → ``errored`` (the prohibition under test was
    never exercised), never ``failed``;
  - a direct create into the gated state would bypass the org's controls — the
    3-step shape [create → entry-update(expect_acceptance) → rejected mutation]
    is the legitimate path (the S3 transition witness authors it);
  - teardown always runs.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from primeqa.execution_engine.bridge import PlanTranslationError, _project_negative
from primeqa.execution_engine.data_executor import execute_data_recipe
from primeqa.execution_engine.plan import (
    DataRecipePlan, PlannedCreate, PlannedUpdate,
)
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep, UpdateStep,
)
from primeqa.test_representation.models.references import LogicalRef

_ENV_ID = 59
_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"
_TARGET = LogicalRef(entity_type="Object", external_id="PLS_BM_Deal__c")


def _env(status, body, record_id=None):
    return {"api_response": {"status_code": status, "body": body},
            "http_status": status, "success": 200 <= status < 300,
            "record_id": record_id}


class _Client:
    """Scripted client: per-call update envelopes, permissive create/delete."""

    def __init__(self, update_results):
        self._updates = list(update_results)
        self.update_calls, self.deletes = [], []

    def create(self, sobject, field_values):
        return _env(201, {"id": "a01XYZ", "success": True}, "a01XYZ")

    def update(self, sobject, record_id, field_changes):
        self.update_calls.append(dict(field_changes))
        return self._updates.pop(0)

    def delete(self, sobject, record_id):
        self.deletes.append(record_id)
        return _env(204, None)


class _NoWorldS1:
    """The world-construction stub: no required fields, nothing to pad."""

    def current_version_seq(self):
        return 7

    def get_entities(self, entity_type, at_seq, filters=None):
        class _O:
            id = "obj-1"
        return [_O()] if entity_type == "Object" else []

    def get_related(self, entity_id, edge_types, direction, at_seq):
        return []

    def get_entity_details(self, entity_id, at_seq):
        return {}


# -- bridge projection ------------------------------------------------------------

def _steps(entry_target=_TARGET):
    return [
        CreateStep(step_id="create-setup", target_object=_TARGET,
                   field_values={"PLS_BM_Stage__c": "Contract Review"}),
        UpdateStep(step_id="update-entry", target=entry_target,
                   field_changes={"PLS_BM_Stage__c": "Approved"},
                   expect_acceptance=True),
        UpdateStep(step_id="update-violating", target=_TARGET,
                   field_changes={"PLS_BM_Deal_Value__c": 2000000.02},
                   expect_rejection=RejectionExpectation(error_code=_VR_CODE)),
    ]


def test_bridge_projects_the_entry_shape():
    planned = _project_negative(_steps(), recipe_id=uuid4())
    assert [type(p).__name__ for p in planned] \
        == ["PlannedCreate", "PlannedUpdate", "PlannedUpdate"]
    entry, mutation = planned[1], planned[2]
    assert entry.expect_acceptance is True and entry.expect_rejection is None
    assert mutation.expect_rejection is not None


def test_bridge_refuses_entry_on_a_different_object():
    other = LogicalRef(entity_type="Object", external_id="Account")
    with pytest.raises(PlanTranslationError):
        _project_negative(_steps(entry_target=other), recipe_id=uuid4())


def test_bridge_still_refuses_unflagged_3_step():
    steps = _steps()
    steps[1] = UpdateStep(step_id="update-entry", target=_TARGET,
                          field_changes={"PLS_BM_Stage__c": "Approved"})
    with pytest.raises(PlanTranslationError):
        _project_negative(steps, recipe_id=uuid4())


# -- executor semantics -----------------------------------------------------------

def _plan():
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-setup", target_object=_TARGET,
                          field_values={"PLS_BM_Stage__c": "Contract Review"},
                          expect_rejection=None),
            PlannedUpdate(step_id="update-entry", target_object=_TARGET,
                          field_changes={"PLS_BM_Stage__c": "Approved"},
                          expect_rejection=None, expect_acceptance=True,
                          setup_step_id="create-setup"),
            PlannedUpdate(
                step_id="update-violating", target_object=_TARGET,
                field_changes={"PLS_BM_Deal_Value__c": 2000000.02},
                expect_rejection=RejectionExpectation(error_code=_VR_CODE),
                setup_step_id="create-setup"),
        ))


def test_entry_succeeds_then_mutation_rejected_is_passed():
    client = _Client(update_results=[
        _env(204, None),                                       # the entry accepts
        _env(400, [{"errorCode": _VR_CODE,                     # the lock fires
                    "message": "Deal Value cannot be changed", "fields": []}]),
    ])
    ev = execute_data_recipe(_plan(), client=client,
                             environment_id=_ENV_ID, s1=_NoWorldS1())
    assert ev.outcome == "passed"
    kinds = [s.kind for s in ev.steps]
    assert kinds == ["create", "update", "update"]
    entry_ev, mut_ev = ev.steps[1], ev.steps[2]
    assert entry_ev.success is True
    assert mut_ev.success is False and mut_ev.matched is True
    assert client.deletes                                       # teardown ran


def test_entry_rejected_is_errored_never_failed():
    client = _Client(update_results=[
        _env(400, [{"errorCode": _VR_CODE,                     # the ENTRY refused
                    "message": "cannot move to Approved", "fields": []}]),
    ])
    ev = execute_data_recipe(_plan(), client=client,
                             environment_id=_ENV_ID, s1=_NoWorldS1())
    assert ev.outcome == "errored"
    assert ev.error is not None and ev.error.phase == "update-entry"
    # only one update was attempted (the mutation was never reached)
    assert len(client.update_calls) == 1
    assert client.deletes                                       # teardown ran

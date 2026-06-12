"""Unit tests for the data-recipe behavioral-negative executor (D-110.2 slice 2)
— stub client, no org, no PG.

Covers the grounded 4-way eval (passed / failed-success / failed-wrong-code /
errored), the targeted best-effort cleanup, the `CreateAttemptEvidence` shape,
the thin `DataMutationClient` envelope (no network), and that the evidence
serializes through the existing `persist_run_evidence` unchanged (store reused).
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

from primeqa.execution_engine.data_executor import execute_data_recipe
from primeqa.execution_engine.data_mutation_client import DataMutationClient
from primeqa.execution_engine.evidence import CreateAttemptEvidence
from primeqa.execution_engine.plan import DataRecipePlan, PlannedCreate
from primeqa.execution_engine.result_store import persist_run_evidence
from primeqa.integrations.exceptions import SFRequestError
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.references import LogicalRef

_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"
_ENV_ID = 7


# ---------------------------------------------------------------------------
# Stubs + envelope builders
# ---------------------------------------------------------------------------

class _StubClient:
    def __init__(self, *, create_result=None, create_raises=None,
                 delete_result=None, delete_raises=None):
        self._create_result = create_result
        self._create_raises = create_raises
        self._delete_result = delete_result or {"success": True}
        self._delete_raises = delete_raises
        self.creates, self.deletes = [], []

    def create(self, sobject, field_values):
        self.creates.append((sobject, field_values))
        if self._create_raises is not None:
            raise self._create_raises
        return self._create_result

    def delete(self, sobject, record_id):
        self.deletes.append((sobject, record_id))
        if self._delete_raises is not None:
            raise self._delete_raises
        return self._delete_result


def _rejected(error_code=_VR_CODE, message="A reason is required", status=400):
    return {"api_response": {"status_code": status,
                             "body": [{"errorCode": error_code, "message": message, "fields": []}]},
            "http_status": status, "success": False, "record_id": None}


def _success(record_id="001xx0000000001"):
    return {"api_response": {"status_code": 201, "body": {"id": record_id, "success": True}},
            "http_status": 201, "success": True, "record_id": record_id}


def _plan(*, expect_code=_VR_CODE, expect_pattern=None, recipe_id=None):
    return DataRecipePlan(
        recipe_id=recipe_id or uuid4(), recipe_version_seq=2,
        claim_test_id=uuid4(), claim_version_seq=None, api_choice="rest",
        steps=(PlannedCreate(
            step_id="create-violating",
            target_object=LogicalRef(entity_type="Object", external_id="Lead"),
            field_values={"Company": "Acme"},
            expect_rejection=RejectionExpectation(
                error_code=expect_code, error_message_pattern=expect_pattern)),))


# ---------------------------------------------------------------------------
# The grounded 4-way eval
# ---------------------------------------------------------------------------

def test_passed_when_rejected_and_code_matches():
    client = _StubClient(create_result=_rejected(_VR_CODE))
    ev = execute_data_recipe(_plan(), client=client, environment_id=_ENV_ID)

    assert ev.outcome == "passed"
    step = ev.steps[0]
    assert isinstance(step, CreateAttemptEvidence)
    assert step.matched is True
    assert step.error_code == _VR_CODE
    assert step.cleanup.attempted is False     # nothing created → no cleanup
    assert client.deletes == []                # no delete issued


def test_failed_when_create_succeeds_and_cleans_up():
    client = _StubClient(create_result=_success("001ABC"))
    ev = execute_data_recipe(_plan(), client=client, environment_id=_ENV_ID)

    # the prohibition did NOT enforce → failed.
    assert ev.outcome == "failed"
    step = ev.steps[0]
    assert step.success is True and step.matched is False
    # targeted best-effort delete of the created record.
    assert step.cleanup.attempted is True and step.cleanup.succeeded is True
    assert step.cleanup.record_id == "001ABC"
    assert client.deletes == [("Lead", "001ABC")]


def test_failed_when_rejected_for_wrong_code():
    # rejected, but NOT with the expected code — the exact case v1 wrongly passes.
    client = _StubClient(create_result=_rejected("DUPLICATE_VALUE"))
    ev = execute_data_recipe(_plan(expect_code=_VR_CODE), client=client, environment_id=_ENV_ID)

    assert ev.outcome == "failed"
    assert ev.steps[0].matched is False
    assert ev.steps[0].cleanup.attempted is False   # nothing created


def test_errored_on_transport_failure():
    client = _StubClient(create_raises=SFRequestError("boom"))
    ev = execute_data_recipe(_plan(), client=client, environment_id=_ENV_ID)

    assert ev.outcome == "errored"
    assert ev.error is not None and ev.error.phase == "create"
    assert ev.steps[0].http_status is None


def test_errored_on_non_business_error_response():
    # 401 = the org didn't perform a business evaluation → couldn't attempt.
    client = _StubClient(create_result=_rejected("INVALID_SESSION_ID", status=401))
    ev = execute_data_recipe(_plan(), client=client, environment_id=_ENV_ID)

    assert ev.outcome == "errored"
    assert ev.error is not None
    assert ev.steps[0].http_status == 401


# ---------------------------------------------------------------------------
# Match nuances + evidence
# ---------------------------------------------------------------------------

def test_message_pattern_must_also_match():
    client = _StubClient(create_result=_rejected(_VR_CODE, message="A reason is required"))
    # code matches + pattern matches → passed.
    ev_ok = execute_data_recipe(
        _plan(expect_pattern="reason is required"), client=client, environment_id=_ENV_ID)
    assert ev_ok.outcome == "passed"
    # code matches but pattern does NOT → failed.
    ev_no = execute_data_recipe(
        _plan(expect_pattern="totally different"),
        client=_StubClient(create_result=_rejected(_VR_CODE, message="A reason is required")),
        environment_id=_ENV_ID)
    assert ev_no.outcome == "failed"


def test_match_robust_to_multi_error_body():
    body = {"api_response": {"status_code": 400,
            "body": [{"errorCode": "OTHER", "message": "x"},
                     {"errorCode": _VR_CODE, "message": "y"}]},
            "http_status": 400, "success": False, "record_id": None}
    ev = execute_data_recipe(_plan(), client=_StubClient(create_result=body), environment_id=_ENV_ID)
    assert ev.outcome == "passed"          # matches if ANY error's code matches
    assert len(ev.steps[0].rejection_body) == 2   # full body captured


def test_run_id_minted_fresh_per_run():
    p, c = _plan(), _StubClient(create_result=_rejected())
    e1 = execute_data_recipe(p, client=c, environment_id=_ENV_ID)
    e2 = execute_data_recipe(p, client=c, environment_id=_ENV_ID)
    assert isinstance(e1.run_id, UUID) and e1.run_id != e2.run_id


def test_cleanup_failure_is_best_effort():
    # create succeeds (failed outcome) but the cleanup delete raises → recorded,
    # not fatal; outcome stays failed.
    client = _StubClient(create_result=_success("001Z"), delete_raises=SFRequestError("nope"))
    ev = execute_data_recipe(_plan(), client=client, environment_id=_ENV_ID)
    assert ev.outcome == "failed"
    assert ev.steps[0].cleanup.attempted is True and ev.steps[0].cleanup.succeeded is False


# ---------------------------------------------------------------------------
# Evidence serializes through the existing persister (store reused, no change)
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


def test_create_evidence_persists_via_existing_store():
    ev = execute_data_recipe(_plan(), client=_StubClient(create_result=_rejected()),
                             environment_id=_ENV_ID)
    session = _FakeSession()
    persist_run_evidence(session, ev)
    row = session.added[0]
    assert row.outcome == "passed"
    # the create-attempt step serialized into the existing evidence JSONB.
    json.dumps(row.evidence)               # JSONB-safe (no datetime/tuple leak)
    step = row.evidence["steps"][0]
    assert step["kind"] == "create"
    assert step["sobject"] == "Lead"
    assert step["matched"] is True
    assert step["error_code"] == _VR_CODE
    assert step["cleanup"]["attempted"] is False
    assert isinstance(step["rejection_body"], list)


# ---------------------------------------------------------------------------
# The thin DataMutationClient envelope (no network)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code, payload=None, content=b"{}"):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = content

    def json(self):
        return self._payload


def test_client_create_does_not_raise_on_400_rejection():
    c = DataMutationClient("https://acme.my.salesforce.com", "60.0", "tok")
    c._session.post = lambda url, json=None, timeout=None: _Resp(
        400, [{"errorCode": _VR_CODE, "message": "no"}])
    env = c.create("Lead", {"Company": "x"})
    assert env["success"] is False and env["http_status"] == 400
    assert env["api_response"]["body"][0]["errorCode"] == _VR_CODE


def test_client_create_envelope_on_success():
    c = DataMutationClient("https://acme.my.salesforce.com", "60.0", "tok")
    c._session.post = lambda url, json=None, timeout=None: _Resp(
        201, {"id": "001Z", "success": True})
    env = c.create("Lead", {"Company": "x"})
    assert env["success"] is True and env["record_id"] == "001Z"


def test_client_create_raises_on_transport_failure():
    import pytest
    import requests
    c = DataMutationClient("https://acme.my.salesforce.com", "60.0", "tok")

    def _boom(url, json=None, timeout=None):
        raise requests.ConnectionError("down")

    c._session.post = _boom
    with pytest.raises(SFRequestError):
        c.create("Lead", {"Company": "x"})


# ---------------------------------------------------------------------------
# D-225 — error_fields extraction (FLS/access denials name the blocked fields)
# ---------------------------------------------------------------------------

def test_error_fields_extracted_from_rejection_body():
    body = [
        {"errorCode": "INSUFFICIENT_ACCESS_OR_READONLY",
         "message": "no access", "fields": ["Last_Escalation_Date__c"]},
        {"errorCode": "INVALID_FIELD_FOR_INSERT_UPDATE",
         "message": "not writable",
         "fields": ["Last_Escalation_Date__c", "Status__c"]},
    ]
    client = _StubClient(create_result={
        "api_response": {"status_code": 400, "body": body},
        "http_status": 400, "success": False, "record_id": None})
    ev = execute_data_recipe(_plan(), client=client, environment_id=_ENV_ID)
    step = ev.steps[0]
    # deduped, order-preserving
    assert step.error_fields == ("Last_Escalation_Date__c", "Status__c")


def test_error_fields_empty_when_body_names_none():
    client = _StubClient(create_result=_rejected(_VR_CODE))
    ev = execute_data_recipe(_plan(), client=client, environment_id=_ENV_ID)
    assert ev.steps[0].error_fields == ()

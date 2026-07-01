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

from primeqa.execution_engine.data_executor import (
    _is_permission_rejection,
    execute_data_recipe,
)
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


def test_d297_1_html_entity_message_matches_rendered_pattern():
    # D-297.1: emission escapes the RENDERED message (it HTML-unescapes the
    # Tooling-stored entity first); the org's runtime rejection may return EITHER the
    # HTML entity or the rendered char. _matches HTML-unescapes the runtime side, so
    # BOTH forms match -> passed. An encoding mismatch must never grade a correct
    # rejection `failed`.
    import re as _re
    rendered = "Loans over ₹50,00,000 need approval."
    pat = _re.escape(rendered)
    for runtime_msg in ("Loans over &#8377;50,00,000 need approval.", rendered):
        ev = execute_data_recipe(
            _plan(expect_pattern=pat),
            client=_StubClient(create_result=_rejected(_VR_CODE, message=runtime_msg)),
            environment_id=_ENV_ID)
        assert ev.outcome == "passed", runtime_msg
    # teeth preserved: a genuinely different message still fails (unescape is no-op).
    ev_wrong = execute_data_recipe(
        _plan(expect_pattern=pat),
        client=_StubClient(create_result=_rejected(_VR_CODE, message="Unrelated error.")),
        environment_id=_ENV_ID)
    assert ev_wrong.outcome == "failed"


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


# ---------------------------------------------------------------------------
# D-290 — permission/FLS rejection (403 + INSUFFICIENT_ACCESS*) is the org
# ENFORCING the prohibition by access control: the same business outcome as the
# 400 VR path, flowing through the SAME 4-way grade. Closes the hole where it
# mis-graded as `errored`. Over-broadening guarded: only the unambiguous
# permission signal reclassifies; any other 403 stays errored.
# ---------------------------------------------------------------------------

_PERM_CODE = "INSUFFICIENT_ACCESS"


def test_permission_rejection_403_passes_when_expected():
    # THE FIX: a 403 + INSUFFICIENT_ACCESS where the recipe expected it now grades
    # `passed` (org enforced the prohibition) — previously `errored`.
    client = _StubClient(create_result=_rejected(_PERM_CODE, status=403))
    ev = execute_data_recipe(
        _plan(expect_code=_PERM_CODE), client=client, environment_id=_ENV_ID)
    assert ev.outcome == "passed"
    step = ev.steps[0]
    assert step.matched is True and step.http_status == 403
    assert step.error_code == _PERM_CODE
    assert step.cleanup.attempted is False     # nothing created → no cleanup
    assert ev.error is None                    # a graded outcome, not an error


def test_permission_rejection_400_already_passes_via_business_path():
    # A permission denial that surfaces as HTTP 400 (the data API often does)
    # was ALREADY a business rejection — the new 403 branch does not change it.
    client = _StubClient(
        create_result=_rejected("INSUFFICIENT_ACCESS_OR_READONLY", status=400))
    ev = execute_data_recipe(
        _plan(expect_code="INSUFFICIENT_ACCESS_OR_READONLY"),
        client=client, environment_id=_ENV_ID)
    assert ev.outcome == "passed"
    assert ev.steps[0].http_status == 400


def test_permission_rejection_wrong_kind_is_failed_not_passed():
    # A permission rejection when the recipe expected a VR → FAILED (the asserted
    # mechanism did not fire), never silently passed and no longer errored.
    client = _StubClient(create_result=_rejected(_PERM_CODE, status=403))
    ev = execute_data_recipe(
        _plan(expect_code=_VR_CODE), client=client, environment_id=_ENV_ID)
    assert ev.outcome == "failed"
    assert ev.steps[0].matched is False


def test_ambiguous_403_stays_errored_the_guard():
    # THE HONESTY GUARD: a 403 whose code is NOT a recognized permission signal
    # (API disabled, IP range, …) is NOT a business rejection — it stays errored.
    # A wrong optimistic-pass is worse than the original errored mis-grade.
    client = _StubClient(create_result=_rejected("API_DISABLED_FOR_ORG", status=403))
    ev = execute_data_recipe(
        _plan(expect_code=_PERM_CODE), client=client, environment_id=_ENV_ID)
    assert ev.outcome == "errored"
    assert ev.error is not None and ev.steps[0].http_status == 403


def test_bare_403_no_code_stays_errored():
    # A 403 with no structured error code (empty/unparseable body) → errored.
    client = _StubClient(create_result={
        "api_response": {"status_code": 403, "body": []},
        "http_status": 403, "success": False, "record_id": None})
    ev = execute_data_recipe(_plan(expect_code=_PERM_CODE),
                             client=client, environment_id=_ENV_ID)
    assert ev.outcome == "errored"


def test_vr_400_path_unchanged_by_permission_branch():
    # VR path byte-identical: a 400 VR with a matching code still passes; a 400
    # with a wrong code still fails — the permission branch is only reached for
    # non-400 statuses, so the 400 grade is untouched.
    ev_ok = execute_data_recipe(
        _plan(expect_code=_VR_CODE),
        client=_StubClient(create_result=_rejected(_VR_CODE, status=400)),
        environment_id=_ENV_ID)
    assert ev_ok.outcome == "passed"
    ev_no = execute_data_recipe(
        _plan(expect_code=_VR_CODE),
        client=_StubClient(create_result=_rejected("DUPLICATE_VALUE", status=400)),
        environment_id=_ENV_ID)
    assert ev_no.outcome == "failed"


def test_is_permission_rejection_guard_unit():
    # Direct unit on the discriminator — the UNAMBIGUOUS access-denial family (a
    # taxonomy PERMISSION code MINUS the dual-meaning ones).
    for code in ("INSUFFICIENT_ACCESS", "INSUFFICIENT_ACCESS_OR_READONLY",
                 "INSUFFICIENT_FIELD_ACCESS",
                 "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY"):
        assert _is_permission_rejection([{"errorCode": code}]) is True
    # NOT an unambiguous access denial — incl. the DUAL-MEANING code (D-290.1):
    # INVALID_FIELD_FOR_INSERT_UPDATE is a taxonomy permission code but also fires
    # for a structurally non-writable field, so it must NOT reclassify.
    for code in (_VR_CODE, "DUPLICATE_VALUE", "MALFORMED_QUERY",
                 "API_DISABLED_FOR_ORG", "INVALID_SESSION_ID",
                 "INVALID_FIELD_FOR_INSERT_UPDATE"):
        assert _is_permission_rejection([{"errorCode": code}]) is False
    # no code / empty / non-dict entries:
    assert _is_permission_rejection([{"message": "x"}]) is False
    assert _is_permission_rejection([]) is False
    assert _is_permission_rejection(["not-a-dict", None]) is False
    # any-of: a multi-error body with one unambiguous access-denial code → True
    assert _is_permission_rejection(
        [{"errorCode": "OTHER"}, {"errorCode": "INSUFFICIENT_ACCESS"}]) is True


# ---------------------------------------------------------------------------
# D-290.1 — adversarial-review hardening: the dual-meaning code stays errored, and
# the permission branch is gated on HTTP 403 so a transient (5xx/429) body that
# happens to carry a permission code keeps its re-runnable (errored) signal.
# ---------------------------------------------------------------------------

def test_invalid_field_for_insert_update_stays_errored():
    # The reviewer's break: INVALID_FIELD_FOR_INSERT_UPDATE is dual-meaning (FLS OR
    # a structurally non-writable field). Even at 403 and even when the recipe
    # expects it, it must NOT grade `passed` — honest-uncertainty → errored.
    client = _StubClient(create_result=_rejected(
        "INVALID_FIELD_FOR_INSERT_UPDATE", status=403))
    ev = execute_data_recipe(
        _plan(expect_code="INVALID_FIELD_FOR_INSERT_UPDATE"),
        client=client, environment_id=_ENV_ID)
    assert ev.outcome == "errored"


def test_transient_5xx_with_permission_code_stays_errored():
    # The status gate: a 503 whose multi-error body carries a permission sub-error
    # must stay `errored` (transient → re-runnable, D-273), NOT be reclassified as a
    # business outcome. The permission branch is gated on HTTP 403.
    body = {"api_response": {"status_code": 503, "body": [
                {"errorCode": "SERVER_UNAVAILABLE", "message": "try again"},
                {"errorCode": "INSUFFICIENT_ACCESS", "message": "no"}]},
            "http_status": 503, "success": False, "record_id": None}
    ev = execute_data_recipe(
        _plan(expect_code="INSUFFICIENT_ACCESS"), client=_StubClient(create_result=body),
        environment_id=_ENV_ID)
    assert ev.outcome == "errored"
    assert ev.steps[0].http_status == 503

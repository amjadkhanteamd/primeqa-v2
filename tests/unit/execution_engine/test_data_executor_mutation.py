"""Unit tests for the 2-step behavioral negative (D-203) — stub client + stub
S1, no org, no PG.

Covers both mutation kinds (update + delete) across the 4-way grading
(rejected-matching → passed / mutation-succeeds → failed / wrong-code → failed /
transport + non-400 → errored), the setup-failure paths (setup rejected →
errored-SetupRejected, the mutation never attempted; unfillable world), the
teardown-always invariant (incl. the wrongly-successful-delete 404 tolerance),
the bare-ification of qualified field names at the live boundary, the missing-s1
guard, and that the new evidence kinds serialize through the existing persister
unchanged."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from primeqa.execution_engine.data_executor import execute_data_recipe
from primeqa.execution_engine.errors import PlanTranslationError
from primeqa.execution_engine.plan import (
    DataRecipePlan,
    PlannedCreate,
    PlannedDelete,
    PlannedUpdate,
)
from primeqa.execution_engine.result_store import persist_run_evidence
from primeqa.integrations.exceptions import SFRequestError
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.references import LogicalRef

_ENV_ID = 7
_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


# ---------------------------------------------------------------------------
# Stubs — S1 requiredness reader + client with sequenced update/delete results
# (the mutation attempt and the teardown share client.delete, so delete results
# must be consumable in order).
# ---------------------------------------------------------------------------

class _Ent:
    def __init__(self, eid, etype, api):
        self.id, self.entity_type, self.sf_api_name = eid, etype, api
        self.attributes = {}


class _Rel:
    def __init__(self, entity):
        self.entity = entity


class _StubS1:
    def __init__(self, object_api="Opportunity", fields=None, version=5):
        self._api, self._version = object_api, version
        self._obj_id = "obj-" + object_api
        self._fents, self._detail = [], {}
        for f in (fields or []):
            fid = "fld-" + f["api"]
            self._fents.append(_Ent(fid, "Field", f["api"]))
            self._detail[fid] = {
                "field_type": f.get("field_type", "string"),
                "is_nillable": f.get("is_nillable", True),
                "is_calculated": f.get("is_calculated", False),
                "references_object_entity_id": f.get("references_object_entity_id"),
                "picklist_value_set_entity_id": None, "length": None}

    def current_version_seq(self):
        return self._version

    def get_entities(self, entity_type, at_seq, filters=None):
        if (entity_type == "Object" and filters
                and filters.get("sf_api_name") == self._api):
            return [_Ent(self._obj_id, "Object", self._api)]
        return []

    def get_related(self, entity_id, edge_types, direction, at_seq):
        return [_Rel(e) for e in self._fents] if entity_id == self._obj_id else []

    def get_entity_details(self, entity_id, at_seq):
        return self._detail.get(entity_id)


class _StubClient:
    def __init__(self, *, create_result=None, create_raises=None,
                 update_result=None, update_raises=None,
                 delete_results=None, delete_raises=None):
        self._create_result = create_result
        self._create_raises = create_raises
        self._update_result = update_result
        self._update_raises = update_raises
        # Consumed in call order; the last entry repeats once exhausted.
        self._delete_results = list(delete_results or [{"success": True}])
        self._delete_raises = delete_raises
        self.creates, self.updates, self.deletes = [], [], []

    def create(self, sobject, field_values):
        self.creates.append((sobject, dict(field_values)))
        if self._create_raises is not None:
            raise self._create_raises
        return self._create_result

    def update(self, sobject, record_id, field_changes):
        self.updates.append((sobject, record_id, dict(field_changes)))
        if self._update_raises is not None:
            raise self._update_raises
        return self._update_result

    def delete(self, sobject, record_id):
        self.deletes.append((sobject, record_id))
        if self._delete_raises is not None:
            raise self._delete_raises
        if len(self._delete_results) > 1:
            return self._delete_results.pop(0)
        return self._delete_results[0]


def _env(status, body, record_id=None):
    return {"api_response": {"status_code": status, "body": body},
            "http_status": status, "success": 200 <= status < 300,
            "record_id": record_id}


def _created(record_id="006XYZ"):
    return _env(201, {"id": record_id, "success": True}, record_id)


def _rejected(code=_VR_CODE, message="cannot exceed", status=400):
    return _env(status, [{"errorCode": code, "message": message, "fields": []}])


def _no_content():
    """A successful PATCH/DELETE (204, empty body)."""
    return _env(204, None)


def _update_plan(*, expect=None, object_api="Opportunity"):
    target = LogicalRef(entity_type="Object", external_id=object_api)
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(
                step_id="create-setup", target_object=target,
                field_values={f"{object_api}.Amount": 500}, expect_rejection=None),
            PlannedUpdate(
                step_id="update-violating", target_object=target,
                field_changes={f"{object_api}.Amount": 2000000},
                expect_rejection=expect or RejectionExpectation(error_code=_VR_CODE),
                setup_step_id="create-setup"),
        ))


def _delete_plan(*, object_api="Opportunity"):
    target = LogicalRef(entity_type="Object", external_id=object_api)
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(
                step_id="create-setup", target_object=target,
                field_values={f"{object_api}.Amount": 500}, expect_rejection=None),
            PlannedDelete(
                step_id="delete-violating", target_object=target,
                expect_rejection=RejectionExpectation(error_code=_VR_CODE),
                setup_step_id="create-setup"),
        ))


def _s1():
    """Opportunity with a required Name (padding) + an optional Amount."""
    return _StubS1("Opportunity", fields=[
        {"api": "Amount", "field_type": "currency", "is_nillable": True},
        {"api": "Name", "field_type": "string", "is_nillable": False}])


def _run(plan, client):
    return execute_data_recipe(plan, client=client, environment_id=_ENV_ID, s1=_s1())


# ---------------------------------------------------------------------------
# Update — the 4-way grading
# ---------------------------------------------------------------------------

def test_update_rejected_matching_is_passed():
    client = _StubClient(create_result=_created("006A"), update_result=_rejected())
    ev = _run(_update_plan(), client)
    assert ev.outcome == "passed"
    assert [s.kind for s in ev.steps] == ["create", "update"]
    mut = ev.steps[1]
    assert mut.matched is True and mut.record_id == "006A"
    assert mut.error_code == _VR_CODE
    # The subject is torn down (the teardown delete), reported on the SETUP
    # create's evidence — the step that created the record.
    assert client.deletes == [("Opportunity", "006A")]
    assert ev.steps[0].cleanup.attempted is True
    assert ev.steps[0].cleanup.succeeded is True


def test_update_succeeds_is_failed_and_torn_down():
    # The org accepted the prohibited update — not enforced.
    client = _StubClient(create_result=_created("006A"), update_result=_no_content())
    ev = _run(_update_plan(), client)
    assert ev.outcome == "failed"
    assert ev.steps[1].success is True and ev.steps[1].matched is False
    assert client.deletes == [("Opportunity", "006A")]      # teardown still runs


def test_update_rejected_wrong_code_is_failed():
    client = _StubClient(create_result=_created(),
                         update_result=_rejected(code="DUPLICATE_VALUE"))
    ev = _run(_update_plan(), client)
    assert ev.outcome == "failed"
    assert ev.steps[1].matched is False
    assert client.deletes                                   # torn down


def test_update_transport_failure_is_errored_and_torn_down():
    client = _StubClient(create_result=_created("006A"),
                         update_raises=SFRequestError("down"))
    ev = _run(_update_plan(), client)
    assert ev.outcome == "errored" and ev.error.phase == "update"
    assert client.deletes == [("Opportunity", "006A")]


def test_update_non_business_response_is_errored():
    client = _StubClient(create_result=_created(),
                         update_result=_rejected(status=500))
    ev = _run(_update_plan(), client)
    assert ev.outcome == "errored"
    assert ev.error.error_type == "UnexpectedResponse"
    assert client.deletes


def test_update_patches_bare_field_names_on_the_setup_record():
    # Qualified recipe names (Opportunity.Amount) reach the live API bare.
    client = _StubClient(create_result=_created("006A"), update_result=_rejected())
    ev = _run(_update_plan(), client)
    sobject, record_id, changes = client.updates[0]
    assert (sobject, record_id) == ("Opportunity", "006A")
    assert changes == {"Amount": 2000000}
    assert ev.steps[1].field_changes == {"Amount": 2000000}
    # The setup create also got bare names + padding (never the semantic field).
    posted = client.creates[0][1]
    assert posted["Amount"] == 500 and posted["Name"] == "PQA"


# ---------------------------------------------------------------------------
# Delete — the same grading through client.delete
# ---------------------------------------------------------------------------

def test_delete_rejected_matching_is_passed():
    client = _StubClient(
        create_result=_created("006B"),
        delete_results=[_rejected(),                # the mutation attempt
                        {"success": True}])          # the teardown
    ev = _run(_delete_plan(), client)
    assert ev.outcome == "passed"
    assert [s.kind for s in ev.steps] == ["create", "delete"]
    assert ev.steps[1].matched is True and ev.steps[1].record_id == "006B"
    # Two deletes: the rejected attempt, then the teardown of the still-present subject.
    assert client.deletes == [("Opportunity", "006B"), ("Opportunity", "006B")]
    assert ev.steps[0].cleanup.succeeded is True


def test_delete_succeeds_is_failed_and_teardown_404_is_tolerated():
    # The org accepted the prohibited delete → failed. The record is GONE, so
    # the teardown's delete 404s — best-effort records it, never raises.
    client = _StubClient(
        create_result=_created("006B"),
        delete_results=[_no_content(),               # the mutation attempt succeeded
                        _env(404, [{"errorCode": "ENTITY_IS_DELETED",
                                    "message": "gone"}])])
    ev = _run(_delete_plan(), client)
    assert ev.outcome == "failed"
    assert ev.steps[1].success is True
    assert ev.steps[0].cleanup.attempted is True
    assert ev.steps[0].cleanup.succeeded is False           # honest: 404 recorded


def test_delete_rejected_wrong_code_is_failed():
    client = _StubClient(
        create_result=_created(),
        delete_results=[_rejected(code="DELETE_FAILED"), {"success": True}])
    ev = _run(_delete_plan(), client)
    assert ev.outcome == "failed" and ev.steps[1].matched is False


# ---------------------------------------------------------------------------
# Setup failures — always errored, the mutation never attempted
# ---------------------------------------------------------------------------

def test_setup_rejected_is_errored_not_failed():
    client = _StubClient(create_result=_rejected(), update_result=_rejected())
    ev = _run(_update_plan(), client)
    assert ev.outcome == "errored"
    assert ev.error.error_type == "SetupRejected"
    assert client.updates == []                             # never attempted
    assert ev.steps[0].error is not None


def test_setup_transport_failure_is_errored():
    client = _StubClient(create_raises=SFRequestError("down"))
    ev = _run(_update_plan(), client)
    assert ev.outcome == "errored" and ev.error.phase == "create"
    assert client.updates == []


def test_unfillable_world_errors_before_the_setup_create():
    # A required reference to an unresolvable object — S4 cannot construct it.
    s1 = _StubS1("Opportunity", fields=[
        {"api": "Amount", "field_type": "currency", "is_nillable": True},
        {"api": "Custom_Ref__c", "field_type": "reference", "is_nillable": False,
         "references_object_entity_id": "obj-Unresolvable"}])
    client = _StubClient(create_result=_created())
    ev = execute_data_recipe(
        _update_plan(), client=client, environment_id=_ENV_ID, s1=s1)
    assert ev.outcome == "errored"
    assert ev.error.error_type == "UnfillableWorld"
    assert client.creates == [] and client.updates == []


def test_two_step_plan_without_s1_raises():
    with pytest.raises(PlanTranslationError, match="requiredness reader"):
        execute_data_recipe(
            _update_plan(), client=_StubClient(), environment_id=_ENV_ID)


# ---------------------------------------------------------------------------
# Regression — the 1-step create-rejected negative still routes WITHOUT s1
# ---------------------------------------------------------------------------

def test_one_step_create_rejected_still_runs_without_s1():
    target = LogicalRef(entity_type="Object", external_id="Opportunity")
    plan = DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(PlannedCreate(
            step_id="create-violating", target_object=target,
            field_values={"Amount": 2000000},
            expect_rejection=RejectionExpectation(error_code=_VR_CODE)),))
    client = _StubClient(create_result=_rejected())
    ev = execute_data_recipe(plan, client=client, environment_id=_ENV_ID)
    assert ev.outcome == "passed"


# ---------------------------------------------------------------------------
# Evidence persistence — the new kinds flow through the store unchanged
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


@pytest.mark.parametrize("plan_fn,kinds", [
    (_update_plan, ["create", "update"]),
    (_delete_plan, ["create", "delete"]),
])
def test_mutation_evidence_persists_via_existing_store(plan_fn, kinds):
    client = _StubClient(
        create_result=_created("006A"),
        update_result=_rejected(),
        delete_results=[_rejected(), {"success": True}])
    ev = _run(plan_fn(), client)
    session = _FakeSession()
    persist_run_evidence(session, ev)
    row = session.added[0]
    assert row.outcome == "passed"
    json.dumps(row.evidence)                    # JSONB-safe (no datetime/tuple leak)
    assert [s["kind"] for s in row.evidence["steps"]] == kinds
    assert row.evidence["steps"][1]["matched"] is True


# ---------------------------------------------------------------------------
# The new client `update` method (no network)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.content = b"x" if payload is not None else b""
        self.text = text

    def json(self):
        return self._payload


def test_client_update_patches_and_envelopes_204():
    from primeqa.execution_engine.data_mutation_client import DataMutationClient
    c = DataMutationClient("https://acme.my.salesforce.com", "60.0", "tok")
    seen = {}

    def _patch(url, json=None, timeout=None):
        seen["url"], seen["json"] = url, json
        return _Resp(204)                       # SF returns 204 No Content on update

    c._session.patch = _patch
    env = c.update("Opportunity", "006A", {"Amount": 2000000})
    assert seen["url"].endswith("/sobjects/Opportunity/006A")
    assert seen["json"] == {"Amount": 2000000}
    assert env["success"] is True and env["http_status"] == 204


def test_client_update_captures_rejection_response():
    # A 400 rejection is captured data, not an exception (the D-203 evidence).
    from primeqa.execution_engine.data_mutation_client import DataMutationClient
    c = DataMutationClient("https://acme.my.salesforce.com", "60.0", "tok")
    c._session.patch = lambda url, json=None, timeout=None: _Resp(
        400, [{"errorCode": _VR_CODE, "message": "no"}])
    env = c.update("Opportunity", "006A", {"Amount": 1})
    assert env["success"] is False and env["http_status"] == 400
    assert env["api_response"]["body"][0]["errorCode"] == _VR_CODE


# ---------------------------------------------------------------------------
# D-290 — permission/FLS rejection on the prohibited mutation (403 +
# INSUFFICIENT_ACCESS*) is the org enforcing the prohibition by access control:
# the SAME 4-way grade as the 400 VR path, through the shared _run_mutation_attempt
# branch (update + delete). Over-broadening guarded: an ambiguous 403 stays errored.
# ---------------------------------------------------------------------------

_PERM = "INSUFFICIENT_ACCESS"


def _delete_plan_expecting(code, *, object_api="Opportunity"):
    target = LogicalRef(entity_type="Object", external_id=object_api)
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(
                step_id="create-setup", target_object=target,
                field_values={f"{object_api}.Amount": 500}, expect_rejection=None),
            PlannedDelete(
                step_id="delete-violating", target_object=target,
                expect_rejection=RejectionExpectation(error_code=code),
                setup_step_id="create-setup"),
        ))


def test_update_permission_rejection_403_passes():
    # THE FIX (update): a 403 + INSUFFICIENT_ACCESS on the prohibited update where
    # the recipe expected it grades `passed` — previously `errored`. The setup
    # create succeeded; the subject is still torn down.
    client = _StubClient(
        create_result=_created("006A"),
        update_result=_rejected(code=_PERM, status=403))
    ev = _run(_update_plan(expect=RejectionExpectation(error_code=_PERM)), client)
    assert ev.outcome == "passed"
    assert ev.steps[1].matched is True and ev.steps[1].http_status == 403
    assert client.deletes == [("Opportunity", "006A")]      # teardown still runs


def test_update_permission_wrong_kind_is_failed():
    # Expected a VR but the org enforced via permission → failed (not passed,
    # no longer errored).
    client = _StubClient(
        create_result=_created("006A"),
        update_result=_rejected(code=_PERM, status=403))
    ev = _run(_update_plan(), client)        # _update_plan defaults to VR expect
    assert ev.outcome == "failed"
    assert ev.steps[1].matched is False


def test_update_ambiguous_403_stays_errored():
    # THE GUARD: a non-permission 403 is NOT a business rejection → errored.
    client = _StubClient(
        create_result=_created("006A"),
        update_result=_rejected(code="API_DISABLED_FOR_ORG", status=403))
    ev = _run(_update_plan(expect=RejectionExpectation(error_code=_PERM)), client)
    assert ev.outcome == "errored"
    assert client.deletes == [("Opportunity", "006A")]      # teardown still runs


def test_delete_permission_rejection_403_passes():
    # The shared branch covers delete too: a 403 + INSUFFICIENT_ACCESS on the
    # prohibited delete, expected, grades `passed`.
    client = _StubClient(
        create_result=_created("006A"),
        delete_results=[_rejected(code=_PERM, status=403), {"success": True}])
    ev = _run(_delete_plan_expecting(_PERM), client)
    assert ev.outcome == "passed"
    assert ev.steps[1].matched is True and ev.steps[1].http_status == 403


# ---------------------------------------------------------------------------
# D-300 S4: field_overrides on the SETUP create (staging is run-time test data;
# override beats padding, NEVER the derived semantic setup fields).
# ---------------------------------------------------------------------------

def test_setup_create_honors_field_overrides_for_padding():
    # The live D-300 finding: padding's picklist default hit a VR-gated stage
    # and staged nothing. An override steers the PADDED field; the derived
    # semantic setup field and the prohibited update are untouched.
    client = _StubClient(create_result=_created(), update_result=_rejected())
    ev = execute_data_recipe(
        _update_plan(), client=client, environment_id=_ENV_ID, s1=_s1(),
        field_overrides={"Name": "Custom Stage Steer"})
    assert ev.outcome == "passed"
    sobject, payload = client.creates[0]
    assert payload["Name"] == "Custom Stage Steer"     # override beat padding
    assert payload["Amount"] == 500                    # semantic setup intact
    _, _, changes = client.updates[0]
    assert changes == {"Amount": 2000000}              # the mutation untouched


def test_setup_override_never_beats_the_semantic_setup_field():
    # Contrast the positive path (overrides win there): the staging override
    # must not perturb the DERIVED non-violating state (e.g. a bva boundary
    # value) — the semantic field wins even across bare/qualified key forms.
    client = _StubClient(create_result=_created(), update_result=_rejected())
    ev = execute_data_recipe(
        _update_plan(), client=client, environment_id=_ENV_ID, s1=_s1(),
        field_overrides={"Amount": 999})
    assert ev.outcome == "passed"
    _, payload = client.creates[0]
    assert payload["Amount"] == 500                    # derived value wins


# ---------------------------------------------------------------------------
# D-305: expect_acceptance — the create IS the assertion.
# ---------------------------------------------------------------------------

def test_grade_rejected_create_acceptance_any_400_is_failed():
    from primeqa.execution_engine.data_executor import _grade_rejected_create
    # unattributed 400 (the AmbiguousRejection case) -> FAILED for acceptance
    out, err = _grade_rejected_create(
        400, [{"errorCode": "FIELD_CUSTOM_VALIDATION_EXCEPTION",
               "message": "no", "fields": []}],
        {"Amount"}, expect_acceptance=True)
    assert out == "failed" and err is None
    # padding-attributed 400 -> still FAILED (the staged STATE was evaluated)
    out2, _ = _grade_rejected_create(
        400, [{"errorCode": "X", "message": "no", "fields": ["Name"]}],
        {"Amount"}, expect_acceptance=True)
    assert out2 == "failed"
    # non-400 stays errored (transport/authz — not business-evaluated)
    out3, err3 = _grade_rejected_create(
        503, [], {"Amount"}, expect_acceptance=True)
    assert out3 == "errored" and err3 is not None


def test_grade_rejected_create_default_path_byte_identical():
    from primeqa.execution_engine.data_executor import _grade_rejected_create
    # the pre-D-305 disambiguation is untouched without the flag
    out, err = _grade_rejected_create(
        400, [{"errorCode": "X", "message": "no", "fields": []}], {"Amount"})
    assert out == "errored" and err.error_type == "AmbiguousRejection"


# ---------------------------------------------------------------------------
# D-338: the prohibition's premise includes what is asserted BLANK
# ---------------------------------------------------------------------------

def test_setup_override_on_null_asserted_field_is_stripped():
    # The 2-step negative's premise is partly defined by ABSENCE — no
    # override (nor filler) may realize a premise state the claim excludes.
    client = _StubClient(create_result=_created(), update_result=_rejected())
    ev = execute_data_recipe(
        _update_plan(), client=client, environment_id=_ENV_ID, s1=_s1(),
        field_overrides={"Risk__c": "High"},
        null_asserted_fields={"Opportunity.Risk__c"})
    _, payload = client.creates[0]
    assert "Risk__c" not in payload
    assert ev.outcome == "passed"

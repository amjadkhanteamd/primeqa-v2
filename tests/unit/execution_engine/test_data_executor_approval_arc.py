"""Unit tests for the D-333 approval-action arc — bridge projection +
executor semantics, pure stubs (no PG, no live org).

The arc's laws under test:
  - the arc is STAGING: any refused/failed action → ``errored`` (the
    assertion downstream was never reached), never ``failed``;
  - workitem ids thread submit → approve/reject via ``newWorkitemIds``,
    falling back to a live workitem query;
  - a still-PENDING instance is RECALLED (``Removed``) before teardown —
    a pending approval locks the record (D-308.1's watch item); a failed
    recall is a LOGGED LEAK on the arc evidence, never a raise.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.execution_engine.bridge import (
    PlanTranslationError,
    _project_negative,
    _project_positive,
)
from primeqa.execution_engine.data_executor import execute_data_recipe
from primeqa.execution_engine.plan import (
    DataRecipePlan,
    PlannedApprovalAction,
    PlannedAssertion,
    PlannedCreate,
    PlannedDataRead,
    PlannedUpdate,
)
from primeqa.test_representation.models.primitives import (
    AssertionPredicate,
    RejectionExpectation,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    ApprovalActionStep,
    AssertStep,
    CreateStep,
    ReadStep,
    UpdateStep,
)
from primeqa.test_representation.models.references import LogicalRef

_ENV_ID = 59
_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"
_TARGET = LogicalRef(entity_type="Object", external_id="Opportunity")


def _env(status, body, record_id=None):
    return {"api_response": {"status_code": status, "body": body},
            "http_status": status, "success": 200 <= status < 300,
            "record_id": record_id}


def _created(record_id="006XYZ"):
    return _env(201, {"id": record_id, "success": True}, record_id)


def _rejected(code=_VR_CODE, message="approval required"):
    return _env(400, [{"errorCode": code, "message": message, "fields": []}])


def _approval_ok(*, status="Pending", instance="04g1", workitems=("04i1",)):
    return _env(200, [{"success": True, "instanceId": instance,
                       "instanceStatus": status,
                       "newWorkitemIds": list(workitems), "errors": None}])


def _approval_refused(message="ALREADY_IN_PROCESS"):
    return _env(400, [{"success": False, "errors": [
        {"statusCode": "ALREADY_IN_PROCESS", "message": message}]}])


class _ArcClient:
    """Stub client: scripted approval responses, permissive create/update,
    recorded calls (incl. recalls) for assertions."""

    def __init__(self, *, create_result=None, update_result=None,
                 approval_results=None, query_results=None,
                 recall_result=None):
        self._create = create_result or _created()
        self._update = update_result
        self._approvals = list(approval_results or [])
        self._queries = list(query_results or [])
        self._recall = recall_result or _approval_ok(status="Removed",
                                                     workitems=())
        self.approval_calls, self.query_calls, self.deletes = [], [], []

    def create(self, sobject, field_values):
        return self._create

    def update(self, sobject, record_id, field_changes):
        return self._update

    def delete(self, sobject, record_id):
        self.deletes.append((sobject, record_id))
        return _env(204, None)

    def query(self, soql):
        self.query_calls.append(soql)
        return self._queries.pop(0) if self._queries else []

    def approval_action(self, request):
        self.approval_calls.append(dict(request))
        if request.get("actionType") == "Removed":
            return self._recall
        return self._approvals.pop(0) if self._approvals else _approval_ok()


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


def _neg_arc_plan(actions=("submit",)):
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-setup", target_object=_TARGET,
                          field_values={"Loan_Amount__c": 6000000},
                          expect_rejection=None),
        ) + tuple(
            PlannedApprovalAction(step_id=f"arc-{i}", action=a,
                                  setup_step_id="create-setup")
            for i, a in enumerate(actions)
        ) + (
            PlannedUpdate(
                step_id="update-blocked", target_object=_TARGET,
                field_changes={"StageName": "Approved"},
                expect_rejection=RejectionExpectation(error_code=_VR_CODE),
                setup_step_id="create-setup"),
        ))


def _pos_arc_plan(actions=("submit", "approve")):
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-record", target_object=_TARGET,
                          field_values={"Loan_Amount__c": 6000000},
                          expect_rejection=None),
        ) + tuple(
            PlannedApprovalAction(step_id=f"arc-{i}", action=a,
                                  setup_step_id="create-record")
            for i, a in enumerate(actions)
        ) + (
            PlannedUpdate(
                step_id="update-accepted", target_object=_TARGET,
                field_changes={"StageName": "Approved"},
                expect_rejection=None, setup_step_id="create-record",
                expect_acceptance=True),
            PlannedDataRead(
                step_id="read-created", target=_TARGET,
                soql="SELECT Id FROM Opportunity WHERE Id = '$create-record.id'",
                fields_to_capture=("Id",)),
            PlannedAssertion(
                step_id="assert-exists",
                predicate=AssertionPredicate(
                    subject_ref="read-created.Id", predicate="exists")),
        ))


# ---------------------------------------------------------------------------
# Executor — the negative arc (TC-049 / TC-036 shapes)
# ---------------------------------------------------------------------------

def test_pending_arc_blocked_update_passes_and_recalls():
    # TC-049: submit → Pending; the blocked update rejects matching → PASSED;
    # teardown RECALLS the pending instance (Removed) before the delete.
    client = _ArcClient(update_result=_rejected(),
                        approval_results=[_approval_ok()],
                        query_results=[[{"Id": "04i9"}]])
    ev = execute_data_recipe(_neg_arc_plan(("submit",)), client=client,
                             environment_id=_ENV_ID, s1=_NoWorldS1())
    assert ev.outcome == "passed"
    kinds = [s.kind for s in ev.steps]
    assert kinds == ["create", "approval_action", "update"]
    arc_ev = ev.steps[1]
    assert arc_ev.instance_status == "Pending" and arc_ev.success
    # the recall happened (Removed posted) BEFORE the delete
    removed = [c for c in client.approval_calls
               if c["actionType"] == "Removed"]
    assert len(removed) == 1
    assert client.deletes, "the subject was deleted after the recall"
    assert arc_ev.recall is not None and arc_ev.recall.attempted
    assert arc_ev.recall.succeeded is True


def test_submit_reject_arc_no_recall():
    # TC-036: submit → reject resolves the instance — nothing pending, no
    # recall; the blocked update still passes.
    client = _ArcClient(
        update_result=_rejected(),
        approval_results=[
            _approval_ok(),                                   # submit
            _approval_ok(status="Rejected", workitems=()),    # reject
        ])
    ev = execute_data_recipe(_neg_arc_plan(("submit", "reject")),
                             client=client, environment_id=_ENV_ID,
                             s1=_NoWorldS1())
    assert ev.outcome == "passed"
    assert [s.kind for s in ev.steps] == [
        "create", "approval_action", "approval_action", "update"]
    # the reject consumed the submit's newWorkitemIds — no live lookup needed
    assert client.query_calls == []
    assert client.approval_calls[1]["actionType"] == "Reject"
    assert client.approval_calls[1]["contextId"] == "04i1"
    assert not [c for c in client.approval_calls
                if c["actionType"] == "Removed"]


def test_arc_refused_is_errored_never_failed():
    # A refused submit is a STAGING failure: errored, the prohibited update
    # is never attempted.
    client = _ArcClient(update_result=_rejected(),
                        approval_results=[_approval_refused()])
    ev = execute_data_recipe(_neg_arc_plan(("submit",)), client=client,
                             environment_id=_ENV_ID, s1=_NoWorldS1())
    assert ev.outcome == "errored"
    assert ev.error is not None
    assert ev.error.error_type == "ApprovalActionRefused"
    assert [s.kind for s in ev.steps] == ["create", "approval_action"]


def test_approve_without_workitem_is_errored():
    # An approve with no pending workitem (no prior submit result AND an
    # empty live lookup) is a loud staging break.
    client = _ArcClient(update_result=_rejected(),
                        approval_results=[], query_results=[[]])
    plan = _neg_arc_plan(("approve",))
    ev = execute_data_recipe(plan, client=client, environment_id=_ENV_ID,
                             s1=_NoWorldS1())
    assert ev.outcome == "errored"
    assert ev.error.error_type == "NoPendingWorkitem"


def test_failed_recall_is_a_logged_leak_on_the_evidence():
    # The pending instance's recall fails → CleanupRecord(succeeded=False)
    # rides the arc evidence; the run outcome is UNCHANGED (best-effort).
    client = _ArcClient(update_result=_rejected(),
                        approval_results=[_approval_ok()],
                        query_results=[[{"Id": "04i9"}]],
                        recall_result=_env(400, [{"success": False,
                                                  "errors": []}]))
    ev = execute_data_recipe(_neg_arc_plan(("submit",)), client=client,
                             environment_id=_ENV_ID, s1=_NoWorldS1())
    assert ev.outcome == "passed"
    arc_ev = ev.steps[1]
    assert arc_ev.recall.attempted and arc_ev.recall.succeeded is False


# ---------------------------------------------------------------------------
# Executor — the positive arc (TC-035 shape)
# ---------------------------------------------------------------------------

def test_positive_arc_approved_update_accepted_passes():
    client = _ArcClient(
        update_result=_env(204, None),
        approval_results=[
            _approval_ok(),                                   # submit
            _approval_ok(status="Approved", workitems=()),    # approve
        ],
        query_results=[[{"Id": "006XYZ"}]])                   # the read-back
    ev = execute_data_recipe(_pos_arc_plan(), client=client,
                             environment_id=_ENV_ID, s1=_NoWorldS1())
    assert ev.outcome == "passed"
    assert [s.kind for s in ev.steps] == [
        "create", "approval_action", "approval_action", "update", "read",
        "assert"]
    assert ev.steps[2].instance_status == "Approved"
    # resolved instance — no recall
    assert not [c for c in client.approval_calls
                if c["actionType"] == "Removed"]


def test_positive_arc_refusal_recalls_and_errors():
    # The approve is refused mid-arc while the submit left the instance
    # pending → errored + recall before teardown.
    client = _ArcClient(
        update_result=_env(204, None),
        approval_results=[_approval_ok(), _approval_refused()],
        query_results=[[{"Id": "04i9"}]])
    ev = execute_data_recipe(_pos_arc_plan(), client=client,
                             environment_id=_ENV_ID, s1=_NoWorldS1())
    assert ev.outcome == "errored"
    removed = [c for c in client.approval_calls
               if c["actionType"] == "Removed"]
    assert len(removed) == 1
    kinds = [s.kind for s in ev.steps]
    assert kinds == ["create", "approval_action", "approval_action"]
    assert ev.steps[-1].error is not None


# ---------------------------------------------------------------------------
# Bridge — projection shapes
# ---------------------------------------------------------------------------

def _create_step(step_id="create-setup", expect_rejection=None):
    return CreateStep(step_id=step_id, target_object=_TARGET,
                      field_values={"Opportunity.Loan_Amount__c": 6000000},
                      expect_rejection=expect_rejection)


def _update_step(*, expect_rejection=None, expect_acceptance=False):
    return UpdateStep(step_id="upd", target=_TARGET,
                      field_changes={"Opportunity.StageName": "Approved"},
                      expect_rejection=expect_rejection,
                      expect_acceptance=expect_acceptance)


def test_bridge_negative_arc_projects_in_order():
    steps = [
        _create_step(),
        ApprovalActionStep(step_id="a1", action="submit"),
        ApprovalActionStep(step_id="a2", action="reject"),
        _update_step(expect_rejection=RejectionExpectation(error_code=_VR_CODE)),
    ]
    planned = _project_negative(steps, recipe_id=uuid4())
    assert [p.kind for p in planned] == [
        "create", "approval_action", "approval_action", "update"]
    assert planned[1].setup_step_id == "create-setup"
    assert planned[2].action == "reject"


def test_bridge_positive_arc_requires_the_update():
    steps = [
        _create_step("create-record"),
        ApprovalActionStep(step_id="a1", action="submit"),
        ReadStep(step_id="r", target=_TARGET, soql="SELECT Id FROM Opportunity",
                 fields_to_capture=["Id"]),
        AssertStep(step_id="s", predicate=AssertionPredicate(
            subject_ref="r.Id", predicate="exists")),
    ]
    try:
        _project_positive(steps, recipe_id=uuid4())
        raise AssertionError("arc without an update must refuse")
    except PlanTranslationError as e:
        assert "must be followed by" in str(e)


def test_bridge_positive_arc_projects_bound_to_terminal_create():
    steps = [
        _create_step("create-record"),
        ApprovalActionStep(step_id="a1", action="submit"),
        ApprovalActionStep(step_id="a2", action="approve"),
        _update_step(expect_acceptance=True),
        ReadStep(step_id="r", target=_TARGET, soql="SELECT Id FROM Opportunity",
                 fields_to_capture=["Id"]),
        AssertStep(step_id="s", predicate=AssertionPredicate(
            subject_ref="r.Id", predicate="exists")),
    ]
    planned = _project_positive(steps, recipe_id=uuid4())
    assert [p.kind for p in planned] == [
        "create", "approval_action", "approval_action", "update", "read",
        "assert"]
    assert planned[1].setup_step_id == "create-record"
    assert planned[3].setup_step_id == "create-record"

"""Unit tests for the data-recipe positive create-and-verify executor (D-115 side
B) — stub client + stub S1, no org, no PG.

Covers the outcome grammar (passed / failed-mismatch / errored-0row /
errored-read-transport / errored-create-transport / errored-non-400), the
400-rejection disambiguation (semantic → failed / padding → errored / none →
errored), the unfillable-world short-circuit, the k14 always-teardown, the k16
padding (never the semantic field), the ``$create-record.id`` substitution, the
new data ``query`` client method, and that the evidence serializes through the
existing persister unchanged."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from primeqa.execution_engine.data_executor import execute_data_recipe
from primeqa.execution_engine.data_mutation_client import DataMutationClient
from primeqa.execution_engine.errors import PlanTranslationError
from primeqa.execution_engine.plan import (
    DataRecipePlan,
    PlannedAssertion,
    PlannedCreate,
    PlannedDataRead,
)
from primeqa.execution_engine.result_store import persist_run_evidence
from primeqa.integrations.exceptions import SFRequestError
from primeqa.test_representation.models.primitives import AssertionPredicate
from primeqa.test_representation.models.references import LogicalRef

_ENV_ID = 7


# ---------------------------------------------------------------------------
# Stub S1 (the requiredness reader) + stub client (create / query / delete)
# ---------------------------------------------------------------------------

class _Ent:
    def __init__(self, eid, etype, api):
        self.id, self.entity_type, self.sf_api_name = eid, etype, api
        self.attributes = {}


class _Rel:
    def __init__(self, entity):
        self.entity = entity


class _StubS1:
    def __init__(self, object_api="Account", fields=None, version=5):
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
                 query_result=None, query_raises=None,
                 delete_result=None, delete_raises=None):
        self._create_result = create_result
        self._create_raises = create_raises
        self._query_result = [] if query_result is None else query_result
        self._query_raises = query_raises
        self._delete_result = delete_result or {"success": True}
        self._delete_raises = delete_raises
        self.creates, self.queries, self.deletes = [], [], []

    def create(self, sobject, field_values):
        self.creates.append((sobject, dict(field_values)))
        if self._create_raises is not None:
            raise self._create_raises
        return self._create_result

    def query(self, soql):
        self.queries.append(soql)
        if self._query_raises is not None:
            raise self._query_raises
        return list(self._query_result)

    def delete(self, sobject, record_id):
        self.deletes.append((sobject, record_id))
        if self._delete_raises is not None:
            raise self._delete_raises
        return self._delete_result


def _success(record_id="001ABC"):
    return {"api_response": {"status_code": 201,
                            "body": {"id": record_id, "success": True}},
            "http_status": 201, "success": True, "record_id": record_id}


def _rejected(*, fields, status=400, code="FIELD_CUSTOM_VALIDATION_EXCEPTION"):
    return {"api_response": {"status_code": status,
            "body": [{"errorCode": code, "message": "no", "fields": list(fields)}]},
            "http_status": status, "success": False, "record_id": None}


def _plan(*, field="Status__c", value="Active", object_api="Account"):
    target = LogicalRef(entity_type="Object", external_id=object_api)
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-record", target_object=target,
                          field_values={field: value}, expect_rejection=None),
            PlannedDataRead(
                step_id="read-created", target=target,
                soql=f"SELECT {field} FROM {object_api} WHERE Id = '$create-record.id'",
                fields_to_capture=(field,)),
            PlannedAssertion(
                step_id="assert-value",
                predicate=AssertionPredicate(
                    subject_ref=f"read-created.{field}", predicate="equals", value=value)),
        ))


def _s1():
    """Account with a required Name (padding) + an optional Status__c (semantic)."""
    return _StubS1("Account", fields=[
        {"api": "Status__c", "field_type": "picklist", "is_nillable": True},
        {"api": "Name", "field_type": "string", "is_nillable": False}])


def _run(plan, client, s1):
    return execute_data_recipe(plan, client=client, environment_id=_ENV_ID, s1=s1)


# ---------------------------------------------------------------------------
# Outcome grammar
# ---------------------------------------------------------------------------

def test_passed_when_observed_equals_value():
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Status__c": "Active"}])
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "passed"
    assert [s.kind for s in ev.steps] == ["create", "read", "assert"]
    assert ev.steps[2].held is True
    # k14 teardown — the created record is deleted.
    assert client.deletes == [("Account", "001Z")]
    # k16 — the create posts the semantic value verbatim + S4's padding, and S4
    # never chooses the value under test.
    posted = client.creates[0][1]
    assert posted["Status__c"] == "Active" and posted["Name"] == "PQA"


def test_failed_when_observed_differs():
    client = _StubClient(create_result=_success(), query_result=[{"Status__c": "Inactive"}])
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "failed" and ev.steps[2].held is False
    assert client.deletes                                   # still torn down


def test_errored_when_read_returns_zero_rows():
    client = _StubClient(create_result=_success(), query_result=[])
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "errored" and ev.error.error_type == "RecordNotObserved"
    assert client.deletes                                   # torn down despite no observation


def test_errored_when_read_transport_fails():
    client = _StubClient(create_result=_success(), query_raises=SFRequestError("down"))
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "errored" and ev.error.phase == "read"
    assert client.deletes


def test_substitutes_record_id_into_soql():
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Status__c": "Active"}])
    _run(_plan(), client, _s1())
    assert client.queries == ["SELECT Status__c FROM Account WHERE Id = '001Z'"]


# ---------------------------------------------------------------------------
# 400-rejection disambiguation (the headline finding vs S4's own gap)
# ---------------------------------------------------------------------------

def test_400_on_semantic_field_is_failed():
    client = _StubClient(create_result=_rejected(fields=["Status__c"]))
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "failed"
    assert ev.steps[0].success is False
    assert client.deletes == []                             # nothing created


def test_400_on_padding_field_is_errored():
    client = _StubClient(create_result=_rejected(fields=["Name"]))
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "errored" and ev.error.error_type == "PaddingRejection"
    assert client.deletes == []


def test_400_with_no_field_attribution_is_errored():
    client = _StubClient(create_result=_rejected(fields=[]))
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "errored" and ev.error.error_type == "AmbiguousRejection"


# ---------------------------------------------------------------------------
# Create-side errors
# ---------------------------------------------------------------------------

def test_create_transport_failure_is_errored():
    client = _StubClient(create_raises=SFRequestError("boom"))
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "errored" and ev.error.phase == "create"
    assert client.creates and client.deletes == [] and client.queries == []


def test_non_business_error_response_is_errored():
    client = _StubClient(create_result=_rejected(fields=[], status=401))
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "errored" and ev.error.error_type == "UnexpectedResponse"


# ---------------------------------------------------------------------------
# Unfillable world (errored pre-create)
# ---------------------------------------------------------------------------

def test_unfillable_world_errors_before_create():
    s1 = _StubS1("Account", fields=[
        {"api": "Status__c", "is_nillable": True},
        {"api": "Owner__c", "field_type": "reference", "is_nillable": False,
         "references_object_entity_id": "obj-User"}])
    client = _StubClient(create_result=_success())
    ev = _run(_plan(), client, s1)
    assert ev.outcome == "errored" and ev.error.error_type == "UnfillableWorld"
    assert "Owner__c" in ev.error.message
    assert client.creates == []                             # no create attempted


# ---------------------------------------------------------------------------
# k14 teardown is best-effort
# ---------------------------------------------------------------------------

def test_teardown_failure_is_best_effort():
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Status__c": "Active"}],
                         delete_raises=SFRequestError("nope"))
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "passed"                           # teardown never flips the verdict
    assert ev.steps[0].cleanup.attempted is True
    assert ev.steps[0].cleanup.succeeded is False


# ---------------------------------------------------------------------------
# Dispatch guard + evidence persistence
# ---------------------------------------------------------------------------

def test_positive_plan_without_s1_raises():
    with pytest.raises(PlanTranslationError, match="requiredness reader"):
        execute_data_recipe(
            _plan(), client=_StubClient(create_result=_success()), environment_id=_ENV_ID)


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


def test_evidence_persists_via_existing_store():
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Status__c": "Active"}])
    ev = _run(_plan(), client, _s1())
    session = _FakeSession()
    persist_run_evidence(session, ev)
    row = session.added[0]
    assert row.outcome == "passed"
    json.dumps(row.evidence)                                # JSONB-safe (no datetime/tuple leak)
    assert [s["kind"] for s in row.evidence["steps"]] == ["create", "read", "assert"]


# ---------------------------------------------------------------------------
# The new data `query` client method (no network)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def test_client_query_returns_records():
    c = DataMutationClient("https://acme.my.salesforce.com", "60.0", "tok")
    c._session.get = lambda url, params=None, timeout=None: _Resp(
        200, {"records": [{"Id": "1", "Status__c": "Active"}], "done": True})
    assert c.query("SELECT Status__c FROM Account") == [{"Id": "1", "Status__c": "Active"}]


def test_client_query_raises_on_error_response():
    c = DataMutationClient("https://acme.my.salesforce.com", "60.0", "tok")
    c._session.get = lambda url, params=None, timeout=None: _Resp(400, {"m": "bad"}, text="bad")
    with pytest.raises(SFRequestError):
        c.query("SELECT bad FROM Account")


# ---------------------------------------------------------------------------
# End-to-end: S3 emission (GroundedPositive) → bridge → S4 execution.
# Proves "generate → run" closes — S4 runs a genuinely S3-emitted positive recipe,
# and the $create-record.id side A *authored* resolves to the live id side B made.
# ---------------------------------------------------------------------------

def test_s3_emitted_positive_recipe_executes_to_passed():
    from datetime import datetime, timezone

    from primeqa.execution_engine.bridge import build_data_recipe_plan
    from primeqa.generation.emission import (
        GroundedPositive,
        _Endpoint,
        author_emission,
    )
    from primeqa.test_representation.coordinator import RecipeRead

    bundle = author_emission(GroundedPositive(
        archetype="data_behavior", claim_kind="value-claim", version_seq=7,
        target_object=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id="Account"),
        field=_Endpoint(entity_id=uuid4(), entity_type="Field", external_id="Status__c"),
        value="Active", requirement_excerpt="Status must be Active"))

    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    recipe = RecipeRead(
        recipe_id=uuid4(), version_seq=2, valid_from=now, valid_to=None,
        claim_test_id=uuid4(), claim_version_seq=None,
        trigger_kind=bundle.trigger_kind, recipe_kind=bundle.recipe_kind,
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment,
        priority=0, status="approved", created_at=now, updated_at=now)

    plan = build_data_recipe_plan(recipe)
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Status__c": "Active"}])
    ev = execute_data_recipe(plan, client=client, environment_id=_ENV_ID, s1=_s1())

    assert ev.outcome == "passed"
    assert [s.kind for s in ev.steps] == ["create", "read", "assert"]
    # side A authored '$create-record.id'; side B resolved it to the live id.
    assert client.queries == ["SELECT Status__c FROM Account WHERE Id = '001Z'"]
    assert client.deletes == [("Account", "001Z")]                  # k14 teardown
    # k16 — the requirement's value flowed verbatim through to the create.
    assert client.creates[0][1]["Status__c"] == "Active"


# ---------------------------------------------------------------------------
# F6.2 — parent provisioning end-to-end through _run_positive: a target with a
# required lookup builds its parent first, both land in created_records, and
# reverse-order teardown deletes the target before its parent.
# ---------------------------------------------------------------------------

class _GraphS1ById:
    """A multi-object S1 over a ``{object_api: [field-spec]}`` graph that resolves
    get_entities by ``sf_api_name`` OR ``id`` (construct_world fetches parents by
    id)."""

    def __init__(self, graph, version=5, raise_on_id=()):
        self._version = version
        self._raise_on_id = set(raise_on_id)        # by-id fetches that raise ValueError
        self._objs = {api: "obj-" + api for api in graph}
        self._byid = {oid: api for api, oid in self._objs.items()}
        self._fields, self._detail = {}, {}
        for api, fields in graph.items():
            ents = []
            for f in fields:
                fid = f"fld-{api}-{f['api']}"
                ents.append(_Ent(fid, "Field", f["api"]))
                self._detail[fid] = {
                    "field_type": f.get("field_type", "string"),
                    "is_nillable": f.get("is_nillable", True),
                    "is_calculated": False,
                    "is_createable": f.get("is_createable", True),
                    "references_object_entity_id": f.get("references_object_entity_id"),
                    "picklist_value_set_entity_id": None, "length": None}
            self._fields["obj-" + api] = ents

    def current_version_seq(self):
        return self._version

    def get_entities(self, entity_type, at_seq, filters=None):
        if entity_type != "Object" or not filters:
            return []
        if "sf_api_name" in filters:
            oid = self._objs.get(filters["sf_api_name"])
            return [_Ent(oid, "Object", filters["sf_api_name"])] if oid else []
        if "id" in filters:
            if filters["id"] in self._raise_on_id:   # simulate an S1 read failure
                raise ValueError(f"S1 read boom on {filters['id']}")
            api = self._byid.get(filters["id"])
            return [_Ent(filters["id"], "Object", api)] if api else []
        return []

    def get_related(self, entity_id, edge_types, direction, at_seq):
        return [_Rel(e) for e in self._fields.get(entity_id, [])]

    def get_entity_details(self, entity_id, at_seq):
        return self._detail.get(entity_id)


class _CountingClient:
    """create → a unique id per call; query → fixed rows; delete → recorded."""

    def __init__(self, query_result):
        self._q = query_result
        self.creates, self.queries, self.deletes = [], [], []
        self._n = 0

    def create(self, sobject, field_values):
        self.creates.append((sobject, dict(field_values)))
        self._n += 1
        return _success(f"id-{sobject}-{self._n}")

    def query(self, soql):
        self.queries.append(soql)
        return list(self._q)

    def delete(self, sobject, record_id):
        self.deletes.append((sobject, record_id))
        return {"success": True}


def _contact_plan():
    target = LogicalRef(entity_type="Object", external_id="Contact")
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-record", target_object=target,
                          field_values={"Email": "a@b.com"}, expect_rejection=None),
            PlannedDataRead(
                step_id="read-created", target=target,
                soql="SELECT Email FROM Contact WHERE Id = '$create-record.id'",
                fields_to_capture=("Email",)),
            PlannedAssertion(
                step_id="assert-value",
                predicate=AssertionPredicate(
                    subject_ref="read-created.Email", predicate="equals", value="a@b.com")),
        ))


def test_positive_with_required_parent_builds_threads_and_tears_down():
    s1 = _GraphS1ById({
        "Contact": [
            {"api": "Email", "field_type": "email", "is_nillable": True},  # semantic (recipe-set)
            {"api": "LastName", "field_type": "string", "is_nillable": False},
            {"api": "AccountId", "field_type": "reference", "is_nillable": False,
             "references_object_entity_id": "obj-Account"}],
        "Account": [{"api": "Name", "field_type": "string", "is_nillable": False}],
    })
    client = _CountingClient(query_result=[{"Email": "a@b.com"}])
    ev = execute_data_recipe(_contact_plan(), client=client, environment_id=_ENV_ID, s1=s1)

    assert ev.outcome == "passed"
    # the parent Account was built first, then the target Contact.
    assert [c[0] for c in client.creates] == ["Account", "Contact"]
    # the parent id was threaded into the Contact's AccountId lookup.
    assert client.creates[1][1]["AccountId"] == "id-Account-1"
    # k16 — the semantic value flowed verbatim; LastName was operationally padded.
    assert client.creates[1][1]["Email"] == "a@b.com"
    assert client.creates[1][1]["LastName"] == "PQA"
    # created_records audits BOTH, in creation order (parent then target).
    assert [(c.sobject, c.created_seq) for c in ev.created_records] == [
        ("Account", 0), ("Contact", 1)]
    # reverse-order teardown: the target Contact deleted BEFORE its Account parent.
    assert client.deletes == [("Contact", "id-Contact-2"), ("Account", "id-Account-1")]


def _thing_plan():
    target = LogicalRef(entity_type="Object", external_id="Thing")
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-record", target_object=target,
                          field_values={"X__c": "v"}, expect_rejection=None),
            PlannedDataRead(step_id="read-created", target=target,
                            soql="SELECT X__c FROM Thing WHERE Id = '$create-record.id'",
                            fields_to_capture=("X__c",)),
            PlannedAssertion(step_id="assert-value",
                             predicate=AssertionPredicate(
                                 subject_ref="read-created.X__c", predicate="equals", value="v")),
        ))


def test_s1_read_error_mid_construct_is_errored_and_first_parent_torn_down():
    # An S1 read error (ValueError — NOT SFClientError) while fetching the SECOND
    # required parent must tear down the FIRST (already-built) parent and surface an
    # errored run. Catching only SFClientError would leak the first parent.
    s1 = _GraphS1ById({
        "Thing": [
            {"api": "AccountId", "field_type": "reference", "is_nillable": False,
             "references_object_entity_id": "obj-Account"},
            {"api": "WidgetId", "field_type": "reference", "is_nillable": False,
             "references_object_entity_id": "obj-Widget"}],
        "Account": [{"api": "Name", "field_type": "string", "is_nillable": False}],
        "Widget": [{"api": "Name", "field_type": "string", "is_nillable": False}],
    }, raise_on_id={"obj-Widget"})       # the 2nd parent's S1 fetch blows up
    client = _CountingClient(query_result=[])
    ev = execute_data_recipe(_thing_plan(), client=client, environment_id=_ENV_ID, s1=s1)

    assert ev.outcome == "errored"
    assert ev.error.phase == "construct" and ev.error.error_type == "ValueError"
    # the first parent (Account) was built, then torn down — no leak, no target create.
    assert client.deletes == [("Account", "id-Account-1")]
    assert all(c[0] != "Thing" for c in client.creates)     # target never attempted


def _qualified_plan(obj="Opportunity", field="StageName", value="Prospecting"):
    q = f"{obj}.{field}"
    target = LogicalRef(entity_type="Object", external_id=obj)
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-record", target_object=target,
                          field_values={q: value}, expect_rejection=None),
            PlannedDataRead(step_id="read-created", target=target,
                            soql=f"SELECT {q} FROM {obj} WHERE Id = '$create-record.id'",
                            fields_to_capture=(q,)),
            PlannedAssertion(step_id="assert-value",
                             predicate=AssertionPredicate(
                                 subject_ref=f"read-created.{q}", predicate="equals", value=value)),
        ))


def test_object_qualified_field_names_are_bare_ified_for_salesforce():
    # The recipe + padding speak S1's object-qualified names (Opportunity.StageName);
    # the executor must translate to bare SF names (StageName) at every SF call —
    # create payload, read SOQL, and the assert's field lookup (SF returns bare keys).
    s1 = _StubS1("Opportunity", fields=[
        {"api": "Opportunity.StageName", "field_type": "picklist", "is_nillable": True},
        {"api": "Opportunity.Name", "field_type": "string", "is_nillable": False}])
    client = _StubClient(create_result=_success("006Z"),
                         query_result=[{"StageName": "Prospecting"}])   # SF returns BARE keys
    ev = _run(_qualified_plan(), client, s1)

    assert ev.outcome == "passed" and ev.steps[2].held is True
    # create payload keys are bare (the 'Opportunity.' self-prefix stripped).
    assert client.creates[0][1] == {"StageName": "Prospecting", "Name": "PQA"}
    # the read SOQL is bare; FROM Opportunity (no dot) untouched.
    assert client.queries == ["SELECT StageName FROM Opportunity WHERE Id = '006Z'"]
    assert client.deletes == [("Opportunity", "006Z")]      # k14 teardown

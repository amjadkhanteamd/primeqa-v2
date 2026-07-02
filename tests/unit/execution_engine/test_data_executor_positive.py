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
    PlannedUpdate,
)
from primeqa.execution_engine.result_store import persist_run_evidence
from primeqa.integrations.exceptions import SFRequestError
from primeqa.test_representation.models.primitives import (
    AssertionPredicate,
    RejectionExpectation,
)
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


def test_async_world_plans_path_equals_sync_s1_path():
    # D-230.2 coverage: the ASYNC data execute path drives the SAME positive recipe
    # through pre-resolved world_plans + s1=None (NO live S1 read during execute) and
    # must produce an IDENTICAL outcome to the sync s1 path. This is the load-bearing
    # world-building case (k16 scalar padding resolved by plan_data_recipe_world ->
    # build_world) that the 1-step-create-rejected async test does not exercise. That
    # the run returns "passed" with s1=None is itself proof no S1 read escaped to execute.
    from primeqa.execution_engine.data_executor import plan_data_recipe_world

    plan, s1 = _plan(), _s1()

    sync_client = _StubClient(create_result=_success("001S"),
                              query_result=[{"Status__c": "Active"}])
    sync_ev = execute_data_recipe(plan, client=sync_client, environment_id=_ENV_ID, s1=s1)

    world_plans = plan_data_recipe_world(plan, s1)        # bracket-1 pre-resolution
    async_client = _StubClient(create_result=_success("001S"),
                               query_result=[{"Status__c": "Active"}])
    async_ev = execute_data_recipe(plan, client=async_client, environment_id=_ENV_ID,
                                   s1=None, world_plans=world_plans)

    assert async_ev.outcome == sync_ev.outcome == "passed"
    assert async_client.creates == sync_client.creates    # same payload, padding incl.
    assert async_client.creates[0][1]["Name"] == "PQA"    # k16 padding from the snapshot
    assert async_client.deletes == sync_client.deletes    # k14 teardown identical
    assert [s.kind for s in async_ev.steps] == [s.kind for s in sync_ev.steps]
    assert async_ev.steps[2].held is True


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


def test_s1_read_error_mid_construct_is_errored_no_org_write():
    # An S1 read error (ValueError — NOT SFClientError) while resolving the SECOND
    # required parent surfaces an errored run. D-230.2: plan_world resolves ALL S1
    # reads (the whole parent recursion) BEFORE build_world creates anything, so a
    # read error during planning means NO org write happened — strictly safer than
    # the old interleaved create-then-teardown (which built the first parent before
    # the second parent's read blew up).
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
    # The read error preceded any build — nothing created, nothing to tear down.
    assert client.creates == []
    assert client.deletes == []


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


# ---------------------------------------------------------------------------
# D-205 — the N-create chain (Account -> Contact with a cross-step ref)
# ---------------------------------------------------------------------------

class _TwoObjStubS1:
    """Account (required Name) + Contact (required LastName, OPTIONAL AccountId
    reference — padding never builds the Account; the chain's explicit ref is
    what threads it)."""

    _FIELDS = {
        "Account": [("Name", "string", False, None)],
        "Contact": [("LastName", "string", False, None),
                    ("Email", "email", True, None),
                    ("AccountId", "reference", True, "obj-Account")],
    }

    def current_version_seq(self):
        return 7

    def get_entities(self, entity_type, at_seq, filters=None):
        api = (filters or {}).get("sf_api_name")
        if entity_type == "Object" and api in self._FIELDS:
            return [_Ent("obj-" + api, "Object", api)]
        return []

    def get_related(self, entity_id, edge_types, direction, at_seq):
        api = entity_id.removeprefix("obj-")
        if api in self._FIELDS:
            return [_Rel(_Ent(f"fld-{api}-{n}", "Field", f"{api}.{n}"))
                    for n, *_ in self._FIELDS[api]]
        return []

    def get_entity_details(self, entity_id, at_seq):
        _, api, name = entity_id.split("-", 2)
        for n, ftype, nillable, ref in self._FIELDS[api]:
            if n == name:
                return {"field_type": ftype, "is_nillable": nillable,
                        "is_calculated": False, "is_createable": True,
                        "references_object_entity_id": ref,
                        "picklist_value_set_entity_id": None, "length": None}
        return None

    def get_picklist_values(self, pvs_id, at_seq):
        return []


class _SeqClient:
    """Create results consumed in call order; query + delete as the plain stub."""

    def __init__(self, create_results, query_result=None):
        self._create_results = list(create_results)
        self._query_result = query_result or []
        self.creates, self.queries, self.deletes = [], [], []

    def create(self, sobject, field_values):
        self.creates.append((sobject, dict(field_values)))
        return self._create_results.pop(0)

    def query(self, soql):
        self.queries.append(soql)
        return list(self._query_result)

    def delete(self, sobject, record_id):
        self.deletes.append((sobject, record_id))
        return {"success": True}


def _chain_plan(*, account_ref="$create-account.id"):
    account = LogicalRef(entity_type="Object", external_id="Account")
    contact = LogicalRef(entity_type="Object", external_id="Contact")
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-account", target_object=account,
                          field_values={"Account.Name": "PQA Chain"},
                          expect_rejection=None),
            PlannedCreate(step_id="create-contact", target_object=contact,
                          field_values={"Contact.Email": "pqa@example.com",
                                        "Contact.AccountId": account_ref},
                          expect_rejection=None),
            PlannedDataRead(
                step_id="read-contact", target=contact,
                soql="SELECT Email FROM Contact WHERE Id = '$create-contact.id'",
                fields_to_capture=("Contact.Email",)),
            PlannedAssertion(
                step_id="assert-email",
                predicate=AssertionPredicate(
                    subject_ref="read-contact.Contact.Email",
                    predicate="equals", value="pqa@example.com")),
        ))


def test_two_create_chain_passes_with_resolved_ref():
    client = _SeqClient(
        [_success("001ACC"), _success("003CON")],
        query_result=[{"Email": "pqa@example.com"}])
    ev = execute_data_recipe(
        _chain_plan(), client=client, environment_id=_ENV_ID, s1=_TwoObjStubS1())
    assert ev.outcome == "passed"
    assert [s.kind for s in ev.steps] == ["create", "create", "read", "assert"]
    assert [s.ordinal for s in ev.steps] == [0, 1, 2, 3]
    # The second create POSTED the RESOLVED first-create id, bare-keyed.
    contact_posted = client.creates[1][1]
    assert contact_posted["AccountId"] == "001ACC"
    assert contact_posted["Email"] == "pqa@example.com"
    assert contact_posted["LastName"] == "PQA"               # padding
    # The read resolved the second create's id.
    assert client.queries == ["SELECT Email FROM Contact WHERE Id = '003CON'"]
    # Teardown reverse order: Contact before Account.
    assert client.deletes == [("Contact", "003CON"), ("Account", "001ACC")]
    # Cleanup attribution by record id — each create's evidence carries ITS
    # record's teardown.
    assert ev.steps[0].cleanup.record_id == "001ACC"
    assert ev.steps[1].cleanup.record_id == "003CON"
    assert ev.steps[0].cleanup.succeeded and ev.steps[1].cleanup.succeeded


def test_mid_chain_rejection_grades_and_tears_down():
    # Create #2 rejected on ITS semantic field -> failed; create #1 torn down.
    client = _SeqClient([
        _success("001ACC"),
        _rejected(fields=["Email"]),
    ])
    ev = execute_data_recipe(
        _chain_plan(), client=client, environment_id=_ENV_ID, s1=_TwoObjStubS1())
    assert ev.outcome == "failed"
    assert [s.kind for s in ev.steps] == ["create", "create"]
    assert ev.steps[1].success is False
    assert client.deletes == [("Account", "001ACC")]
    assert ev.steps[0].cleanup.record_id == "001ACC"         # attributed teardown


def test_unresolved_chain_ref_is_errored_and_torn_down():
    client = _SeqClient([_success("001ACC")])
    ev = execute_data_recipe(
        _chain_plan(account_ref="$create-acount.id"),        # typo'd step id
        client=client, environment_id=_ENV_ID, s1=_TwoObjStubS1())
    assert ev.outcome == "errored"
    assert ev.error.phase == "create"
    assert "create-acount" in ev.error.message
    assert client.deletes == [("Account", "001ACC")]         # no leak


def test_chain_evidence_persists_jsonb_safe():
    client = _SeqClient(
        [_success("001ACC"), _success("003CON")],
        query_result=[{"Email": "pqa@example.com"}])
    ev = execute_data_recipe(
        _chain_plan(), client=client, environment_id=_ENV_ID, s1=_TwoObjStubS1())
    session = _FakeSession()
    persist_run_evidence(session, ev)
    row = session.added[0]
    json.dumps(row.evidence)
    assert [s["kind"] for s in row.evidence["steps"]] == [
        "create", "create", "read", "assert"]


# ---------------------------------------------------------------------------
# Typed-tolerant assert equality (D-211)
# ---------------------------------------------------------------------------

def test_values_equal_numeric_string_vs_float():
    # the live false-failure: requirement literal "5000" vs SF Currency 5000.0
    from primeqa.execution_engine.data_executor import _values_equal
    assert _values_equal(5000.0, "5000")
    assert _values_equal("5000", 5000.0)
    assert _values_equal(5000, "5000.0")
    assert not _values_equal(5000.0, "5001")


def test_values_equal_bools_not_numbers():
    from primeqa.execution_engine.data_executor import _values_equal
    assert _values_equal(True, "true")
    assert _values_equal(True, "TRUE")
    assert _values_equal(False, "false")
    assert not _values_equal(True, "1")     # bools never coerce numerically
    assert not _values_equal(True, 1.0)
    assert not _values_equal(False, "")


def test_values_equal_strings_stay_strict():
    from primeqa.execution_engine.data_executor import _values_equal
    assert _values_equal("Active", "Active")
    assert not _values_equal("Active", "active")   # no case folding on text
    assert not _values_equal("5,000", 5000.0)      # thousands separator is not a number


def test_values_equal_none_observed():
    from primeqa.execution_engine.data_executor import _values_equal
    assert not _values_equal(None, "5000")
    assert _values_equal(None, None)


# ---------------------------------------------------------------------------
# D-210 — cross-object effect reads: exists predicate, bounded retry,
# automation-created-record teardown
# ---------------------------------------------------------------------------

def _effect_plan(*, predicate="exists", effect_field="Level__c", effect_value=None):
    """create Order__c -> read Order_Log__c (the Flow-created effect) -> assert."""
    trigger = LogicalRef(entity_type="Object", external_id="Account")
    effect = LogicalRef(entity_type="Object", external_id="Order_Log__c")
    pred = (AssertionPredicate(subject_ref="read-effect.Id",
                               predicate="exists", value=None)
            if predicate == "exists" else
            AssertionPredicate(subject_ref=f"read-effect.{effect_field}",
                               predicate="equals", value=effect_value))
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-record", target_object=trigger,
                          field_values={}, expect_rejection=None),
            PlannedDataRead(
                step_id="read-effect", target=effect,
                soql="SELECT Id FROM Order_Log__c WHERE Order__c = '$create-record.id'",
                fields_to_capture=("Id",)),
            PlannedAssertion(step_id="assert-effect", predicate=pred),
        ))


def test_exists_passes_and_effect_record_torn_down(monkeypatch):
    client = _StubClient(create_result=_success("001TRIG"),
                         query_result=[{"Id": "a0X001", "attributes": {}}])
    ev = execute_data_recipe(_effect_plan(), client=client,
                             environment_id=_ENV_ID, s1=_s1())
    assert ev.outcome == "passed"
    # the Flow-created effect record was registered + deleted at teardown
    assert ("Order_Log__c", "a0X001") in client.deletes
    # ... and torn down BEFORE the subject (reverse order: effect is newest)
    assert client.deletes.index(("Order_Log__c", "a0X001")) < \
        client.deletes.index(("Account", "001TRIG"))


def test_exists_zero_rows_is_failed_after_bounded_retry(monkeypatch):
    import primeqa.execution_engine.data_executor as dx
    sleeps = []
    monkeypatch.setattr(dx.time, "sleep", lambda s: sleeps.append(s))
    client = _StubClient(create_result=_success("001TRIG"), query_result=[])
    ev = execute_data_recipe(_effect_plan(), client=client,
                             environment_id=_ENV_ID, s1=_s1())
    # the automation never produced the record: a FAILED evaluation (the
    # finding), never an errored crash
    assert ev.outcome == "failed"
    assert ev.error is None
    read_ev = [s for s in ev.steps if s.kind == "read"][0]
    assert read_ev.attempts == 3                 # bounded retry exhausted
    assert len(sleeps) == 2                      # pauses BETWEEN attempts only
    assert len(client.queries) == 3


def test_nonempty_first_read_does_not_retry(monkeypatch):
    import primeqa.execution_engine.data_executor as dx
    monkeypatch.setattr(dx.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("slept")))
    client = _StubClient(create_result=_success("001TRIG"),
                         query_result=[{"Id": "a0X001", "attributes": {}}])
    ev = execute_data_recipe(_effect_plan(), client=client,
                             environment_id=_ENV_ID, s1=_s1())
    read_ev = [s for s in ev.steps if s.kind == "read"][0]
    assert read_ev.attempts == 1
    assert len(client.queries) == 1


def test_equals_zero_rows_on_side_effect_grades_failed_after_retry(monkeypatch):
    # D-227.7 (supersedes the D-210 pin): a SIDE-EFFECT read's 0 rows is the
    # finding — the automation produced nothing, equality cannot hold. The
    # bounded retry still runs first (the automation may land late).
    import primeqa.execution_engine.data_executor as dx
    monkeypatch.setattr(dx.time, "sleep", lambda s: None)
    client = _StubClient(create_result=_success("001TRIG"), query_result=[])
    ev = execute_data_recipe(
        _effect_plan(predicate="equals", effect_value="INFO"),
        client=client, environment_id=_ENV_ID, s1=_s1())
    assert ev.outcome == "failed"
    assert ev.steps[-1].held is False
    assert ev.error is None
    assert len(client.queries) == 3              # the retry still ran first


# ---------------------------------------------------------------------------
# D-227 — the not_null stamp assert (no stable literal to equals against)
# ---------------------------------------------------------------------------

def _not_null_plan(*, field="Last_Escalation_Date__c", object_api="Account"):
    target = LogicalRef(entity_type="Object", external_id=object_api)
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-record", target_object=target,
                          field_values={}, expect_rejection=None),
            PlannedDataRead(
                step_id="read-effect", target=target,
                soql=f"SELECT {field} FROM {object_api} WHERE Id = '$create-record.id'",
                fields_to_capture=(field,)),
            PlannedAssertion(
                step_id="assert-stamp",
                predicate=AssertionPredicate(
                    subject_ref=f"read-effect.{field}", predicate="not_null")),
        ))


def test_not_null_passes_when_stamp_observed():
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Id": "001Z",
                                        "Last_Escalation_Date__c": "2026-06-12"}])
    ev = _run(_not_null_plan(), client, _s1())
    assert ev.outcome == "passed"
    assert ev.steps[-1].held is True


def test_not_null_fails_when_stamp_absent():
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Id": "001Z",
                                        "Last_Escalation_Date__c": None}])
    ev = _run(_not_null_plan(), client, _s1())
    assert ev.outcome == "failed"
    assert ev.steps[-1].held is False


def test_not_null_zero_rows_is_record_not_observed():
    # The read is by a known-created Id; 0 rows is infrastructure, not a
    # finding — same RecordNotObserved semantics as equals.
    client = _StubClient(create_result=_success("001Z"), query_result=[])
    ev = _run(_not_null_plan(), client, _s1())
    assert ev.outcome == "errored"
    assert ev.error.error_type == "RecordNotObserved"


# ---------------------------------------------------------------------------
# D-227.7 — 0-row SIDE-EFFECT reads grade as the finding (P1 perturbation)
# ---------------------------------------------------------------------------

def _side_effect_plan():
    """The child-of-trigger automation shape: read the effect object by
    LOOKUP (not by a created id) and assert one of its fields."""
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-record",
                          target_object=LogicalRef(entity_type="Object",
                                                   external_id="Case"),
                          field_values={}, expect_rejection=None),
            PlannedDataRead(
                step_id="read-effect",
                target=LogicalRef(entity_type="Object",
                                  external_id="Case_SLA__c"),
                soql=("SELECT Id, Status__c FROM Case_SLA__c "
                      "WHERE Case__c = '$create-record.id'"),
                fields_to_capture=("Id", "Status__c")),
            PlannedAssertion(
                step_id="assert-effect",
                predicate=AssertionPredicate(
                    subject_ref="read-effect.Status__c",
                    predicate="equals", value="Active")),
        ))


def test_side_effect_zero_rows_grades_failed_not_errored():
    # The automation produced nothing (e.g. the Flow is off) — that IS the
    # finding: failed, so S6 grades automation_not_triggered (not errored).
    client = _StubClient(create_result=_success("500Z"), query_result=[])
    s1 = _StubS1("Case", fields=[])
    ev = _run(_side_effect_plan(), client, s1)
    assert ev.outcome == "failed"
    assert ev.steps[-1].held is False
    assert ev.error is None


def test_self_observation_zero_rows_stays_record_not_observed():
    # The read targets the record the run CREATED by Id — 0 rows is
    # infrastructure, unchanged semantics.
    client = _StubClient(create_result=_success("001Z"), query_result=[])
    ev = _run(_plan(), client, _s1())
    assert ev.outcome == "errored"
    assert ev.error.error_type == "RecordNotObserved"


# ---------------------------------------------------------------------------
# D-235 — run-time test-data injection (field_overrides), positive vertical only
# ---------------------------------------------------------------------------

def _run_ov(plan, client, s1, overrides):
    return execute_data_recipe(plan, client=client, environment_id=_ENV_ID, s1=s1,
                               field_overrides=overrides)


def _two_create_plan():
    """Two independent Account creates then a read+assert on the 2nd — the
    'subject is the LAST create' fixture."""
    target = LogicalRef(entity_type="Object", external_id="Account")
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-1", target_object=target,
                          field_values={"Status__c": "First"}, expect_rejection=None),
            PlannedCreate(step_id="create-2", target_object=target,
                          field_values={"Status__c": "Second"}, expect_rejection=None),
            PlannedDataRead(
                step_id="read", target=target,
                soql="SELECT Status__c FROM Account WHERE Id = '$create-2.id'",
                fields_to_capture=("Status__c",)),
            PlannedAssertion(
                step_id="assert",
                predicate=AssertionPredicate(
                    subject_ref="read.Status__c", predicate="equals", value="Second")),
        ))


def test_override_wins_over_recipe_value():
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Status__c": "Active"}])
    _run_ov(_plan(field="Status__c", value="Active"), client, _s1(),
            {"Status__c": "Injected"})
    posted = client.creates[0][1]
    assert posted["Status__c"] == "Injected"   # override beat the recipe's "Active"
    assert posted["Name"] == "PQA"             # k16 padding still applied


def test_override_adds_a_field_not_in_the_recipe():
    client = _StubClient(create_result=_success(), query_result=[{"Status__c": "Active"}])
    _run_ov(_plan(), client, _s1(), {"Description": "scenario-7"})
    posted = client.creates[0][1]
    assert posted["Description"] == "scenario-7"   # added
    assert posted["Status__c"] == "Active"         # recipe value untouched


def test_override_qualified_key_is_bare_ified():
    client = _StubClient(create_result=_success(), query_result=[{"Status__c": "Active"}])
    _run_ov(_plan(), client, _s1(), {"Account.Status__c": "Q"})
    assert client.creates[0][1]["Status__c"] == "Q"   # qualified key -> bare, wins


def test_no_overrides_matches_today():
    c1 = _StubClient(create_result=_success(), query_result=[{"Status__c": "Active"}])
    _run_ov(_plan(), c1, _s1(), None)
    c2 = _StubClient(create_result=_success(), query_result=[{"Status__c": "Active"}])
    execute_data_recipe(_plan(), client=c2, environment_id=_ENV_ID, s1=_s1())
    assert c1.creates == c2.creates                 # None overrides == today's behavior


def test_override_targets_only_the_last_create_in_a_chain():
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Status__c": "Second"}])
    _run_ov(_two_create_plan(), client, _s1(), {"Status__c": "OVERRIDE"})
    assert client.creates[0][1]["Status__c"] == "First"      # parent create untouched
    assert client.creates[1][1]["Status__c"] == "OVERRIDE"   # subject (last) overridden


def test_negative_vertical_ignores_overrides():
    # A 1-step create-rejected negative must NOT receive overrides — an override
    # could flip whether the org rejects the create and invalidate the test.
    target = LogicalRef(entity_type="Object", external_id="Lead")
    neg = DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(PlannedCreate(
            step_id="create-violating", target_object=target,
            field_values={"Company": "Acme"},
            expect_rejection=RejectionExpectation(
                error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION",
                error_message_pattern=None)),))
    client = _StubClient(create_result=_rejected(fields=["Company"]))
    # passing overrides must be a no-op on the negative path (no s1 needed — a
    # 1-step create-rejected constructs no world)
    execute_data_recipe(neg, client=client, environment_id=_ENV_ID,
                        field_overrides={"Company": "Injected"})
    assert client.creates[0][1]["Company"] == "Acme"   # override did NOT apply
    assert "Injected" not in client.creates[0][1].values()


# ---------------------------------------------------------------------------
# D-306: the positive update leg (update-then-observe, lever 7d)
# ---------------------------------------------------------------------------

class _UpdClient(_StubClient):
    """The positive stub + an ``update`` method (the D-306 trigger phase)."""

    def __init__(self, *, update_result=None, update_raises=None, **kw):
        super().__init__(**kw)
        self._update_result = update_result
        self._update_raises = update_raises
        self.updates = []

    def update(self, sobject, record_id, field_changes):
        self.updates.append((sobject, record_id, dict(field_changes)))
        if self._update_raises is not None:
            raise self._update_raises
        return self._update_result


def _upd_env(status, body):
    return {"api_response": {"status_code": status, "body": body},
            "http_status": status, "success": 200 <= status < 300,
            "record_id": None}


def _update_plan(*, expect_acceptance=False, object_api="Account"):
    """CreateStep → positive UpdateStep (qualified keys, D-306) → read → assert."""
    target = LogicalRef(entity_type="Object", external_id=object_api)
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-record", target_object=target,
                          field_values={"Status__c": "Active"},
                          expect_rejection=None),
            PlannedUpdate(step_id="update-trigger", target_object=target,
                          field_changes={f"{object_api}.Status__c": "Renewed"},
                          expect_rejection=None,
                          setup_step_id="create-record",
                          expect_acceptance=expect_acceptance),
            PlannedDataRead(
                step_id="read-created", target=target,
                soql=f"SELECT Status__c FROM {object_api} WHERE Id = '$create-record.id'",
                fields_to_capture=("Status__c",)),
            PlannedAssertion(
                step_id="assert-value",
                predicate=AssertionPredicate(
                    subject_ref="read-created.Status__c",
                    predicate="equals", value="Renewed")),
        ))


def test_update_then_observe_passes():
    client = _UpdClient(create_result=_success("001Z"),
                        update_result=_upd_env(204, None),
                        query_result=[{"Status__c": "Renewed"}])
    ev = _run(_update_plan(), client, _s1())
    assert ev.outcome == "passed"
    assert [s.kind for s in ev.steps] == ["create", "update", "read", "assert"]
    assert [s.ordinal for s in ev.steps] == [0, 1, 2, 3]
    # The update PATCHed the record the terminal create made, keys BARE-ified.
    assert client.updates == [("Account", "001Z", {"Status__c": "Renewed"})]
    upd = ev.steps[1]
    assert upd.success is True and upd.http_status == 204
    # k14 — teardown still deletes the record, AFTER the read.
    assert client.deletes == [("Account", "001Z")]


def test_update_rejected_with_expect_acceptance_grades_failed():
    # D-306 mirror of the D-305 create rule: the update IS the assertion — a
    # business rejection is the FINDING, not an indeterminate.
    client = _UpdClient(
        create_result=_success("001Z"),
        update_result=_upd_env(400, [{
            "errorCode": "FIELD_CUSTOM_VALIDATION_EXCEPTION",
            "message": "no", "fields": ["Status__c"]}]))
    ev = _run(_update_plan(expect_acceptance=True), client, _s1())
    assert ev.outcome == "failed"
    assert ev.error is None
    upd = ev.steps[-1]
    assert upd.kind == "update" and upd.success is False
    assert upd.http_status == 400
    assert upd.error_code == "FIELD_CUSTOM_VALIDATION_EXCEPTION"
    # Teardown ran despite the failure (k14).
    assert client.deletes == [("Account", "001Z")]
    # The read NEVER ran — nothing was observable.
    assert client.queries == []


def test_update_rejected_without_expectation_grades_errored():
    client = _UpdClient(
        create_result=_success("001Z"),
        update_result=_upd_env(400, [{
            "errorCode": "FIELD_CUSTOM_VALIDATION_EXCEPTION",
            "message": "no", "fields": []}]))
    ev = _run(_update_plan(expect_acceptance=False), client, _s1())
    assert ev.outcome == "errored"
    assert ev.error.error_type == "UnexpectedRejection"
    assert client.deletes == [("Account", "001Z")]


def test_update_transport_error_grades_errored():
    client = _UpdClient(create_result=_success("001Z"),
                        update_raises=SFRequestError("boom"))
    ev = _run(_update_plan(expect_acceptance=True), client, _s1())
    assert ev.outcome == "errored"
    assert ev.error.phase == "update"
    assert client.deletes == [("Account", "001Z")]


def test_update_non_400_failure_grades_errored():
    client = _UpdClient(create_result=_success("001Z"),
                        update_result=_upd_env(500, None))
    ev = _run(_update_plan(expect_acceptance=True), client, _s1())
    assert ev.outcome == "errored"
    assert ev.error.error_type == "UnexpectedResponse"
    assert client.deletes == [("Account", "001Z")]


def test_field_overrides_never_touch_the_update():
    # D-306's k16 line: dispatch-time overrides steer the CREATE's padding;
    # the update's field_changes ARE the semantic trigger — verbatim, always.
    client = _UpdClient(create_result=_success("001Z"),
                        update_result=_upd_env(204, None),
                        query_result=[{"Status__c": "Renewed"}])
    ev = execute_data_recipe(
        _update_plan(), client=client, environment_id=_ENV_ID, s1=_s1(),
        field_overrides={"Status__c": "Hijacked"})
    assert ev.outcome == "passed"
    # The override landed on the create (the subject create, D-235)...
    assert client.creates[0][1]["Status__c"] == "Hijacked"
    # ...and the update payload is untouched.
    assert client.updates == [("Account", "001Z", {"Status__c": "Renewed"})]


def test_update_evidence_persists_via_existing_store():
    # The positive run's MIXED evidence tuple (create + update + read + assert)
    # must serialize through the existing persister unchanged
    # (UpdateAttemptEvidence already round-trips for the negative vertical;
    # this pins the D-306 positive placement).
    client = _UpdClient(create_result=_success("001Z"),
                        update_result=_upd_env(204, None),
                        query_result=[{"Status__c": "Renewed"}])
    ev = _run(_update_plan(), client, _s1())
    session = _FakeSession()
    persist_run_evidence(session, ev)
    row = session.added[0]
    assert row.outcome == "passed"
    json.dumps(row.evidence)                                # JSONB-safe
    assert [s["kind"] for s in row.evidence["steps"]] == [
        "create", "update", "read", "assert"]


# ---------------------------------------------------------------------------
# D-306 live-proof fixes: the override-steers-padding merge (executor) +
# failed-at-create prose on positive claims (interpreter)
# ---------------------------------------------------------------------------

def test_override_steers_padding_on_expect_acceptance_create():
    # The L7d live finding: for an expect_acceptance create, the merge buried
    # the dispatch-time override UNDER the padding, so a VR-gated padding
    # value (StageName='Credit Assessment') was sent despite the override
    # meant to steer it. Correct order: padding < override < staged semantic.
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Status__c": "Active"}])
    plan = _plan()                                   # stages Status__c=Active
    # mark the subject create expect_acceptance (the update-observe shape)
    from dataclasses import replace as _rp
    steps = (_rp(plan.steps[0], expect_acceptance=True),) + plan.steps[1:]
    plan = _rp(plan, steps=steps)
    ev = execute_data_recipe(
        plan, client=client, environment_id=_ENV_ID, s1=_s1(),
        # Name is PADDING (required, non-staged) — the override must win it.
        # Status__c is STAGED — the override must LOSE to it.
        field_overrides={"Name": "Steered", "Status__c": "Hijacked"})
    assert ev.outcome == "passed"
    posted = client.creates[0][1]
    assert posted["Name"] == "Steered"           # override steered the padding
    assert posted["Status__c"] == "Active"       # staged state won


def test_staged_refs_stay_resolved_under_override_merge():
    # The staged-wins re-merge must use the RESOLVED semantic values — raw
    # recipe values would re-clobber a "$step.id" ref after resolution.
    from dataclasses import replace as _rp
    target = LogicalRef(entity_type="Object", external_id="Account")
    plan = DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=2, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(
            PlannedCreate(step_id="create-parent", target_object=target,
                          field_values={"Status__c": "P"}, expect_rejection=None),
            PlannedCreate(step_id="create-record", target_object=target,
                          field_values={"Status__c": "$create-parent.id"},
                          expect_rejection=None, expect_acceptance=True),
            PlannedDataRead(
                step_id="read-created", target=target,
                soql="SELECT Status__c FROM Account WHERE Id = '$create-record.id'",
                fields_to_capture=("Status__c",)),
            PlannedAssertion(
                step_id="assert-value",
                predicate=AssertionPredicate(
                    subject_ref="read-created.Status__c", predicate="exists")),
        ))
    client = _StubClient(create_result=_success("001Z"),
                         query_result=[{"Status__c": "001Z"}])
    ev = execute_data_recipe(
        plan, client=client, environment_id=_ENV_ID, s1=_s1(),
        field_overrides={"Name": "Steered"})
    assert ev.outcome == "passed"
    # the terminal create posted the RESOLVED parent id, not the raw "$" ref
    assert client.creates[1][1]["Status__c"] == "001Z"

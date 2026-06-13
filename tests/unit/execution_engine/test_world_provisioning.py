"""Unit: F6.2 parent-lookup provisioning — `world.construct_world` recursively
builds required lookup/master-detail parents (D-196.1). Pure: an in-memory S1
graph + a stub create/delete client; no SF, no DB."""
import pytest

from primeqa.execution_engine.data_executor import _best_effort_delete
from primeqa.execution_engine.provisioning import CreatedRecord, CreatedRecordTracker
from primeqa.execution_engine.world import (
    MAX_PARENT_DEPTH,
    WorldPlan,
    build_world,
    construct_world,
    plan_world,
)
from primeqa.integrations.exceptions import SFClientError

pytestmark = pytest.mark.unit


class _Ent:
    def __init__(self, id, entity_type, sf_api_name):
        self.id, self.entity_type, self.sf_api_name = id, entity_type, sf_api_name


class _Rel:
    def __init__(self, entity):
        self.entity = entity


class _GraphS1:
    """In-memory S1 over a ``{object_api: [field-spec]}`` graph. A field-spec is a
    dict (api + field_details); ``ref`` names a referenced object → a reference
    field whose ``references_object_entity_id`` is that object's id. Resolves
    ``get_entities`` by ``sf_api_name`` OR ``id`` (construct_world fetches parents
    by id)."""

    def __init__(self, graph, *, version=7):
        self._version = version
        self._by_api = {api: f"obj-{api}" for api in graph}
        self._by_id = {oid: api for api, oid in self._by_api.items()}
        self._fields, self._detail = {}, {}
        for api, fields in graph.items():
            ents = []
            for f in fields:
                fid = f"fld-{api}-{f['api']}"
                ents.append(_Ent(fid, "Field", f["api"]))
                self._detail[fid] = {
                    "field_type": f.get("field_type", "string"),
                    "is_nillable": f.get("is_nillable", True),
                    "is_calculated": f.get("is_calculated", False),
                    "is_createable": f.get("is_createable", True),
                    "references_object_entity_id":
                        (f"obj-{f['ref']}" if f.get("ref") else None),
                    "length": f.get("length"),
                }
            self._fields[self._by_api[api]] = ents

    def current_version_seq(self):
        return self._version

    def get_entities(self, entity_type, at_seq, filters=None):
        if entity_type != "Object" or not filters:
            return []
        if "sf_api_name" in filters:
            oid = self._by_api.get(filters["sf_api_name"])
            return [_Ent(oid, "Object", filters["sf_api_name"])] if oid else []
        if "id" in filters:
            api = self._by_id.get(filters["id"])
            return [_Ent(filters["id"], "Object", api)] if api else []
        return []

    def get_related(self, entity_id, edge_types, direction, at_seq):
        return [_Rel(e) for e in self._fields.get(entity_id, [])]

    def get_entity_details(self, entity_id, at_seq):
        return self._detail.get(entity_id)


class _StubClient:
    """Records create + delete calls. Each create returns a unique id and success,
    except objects in ``reject`` (success=False) or ``raise_on`` (SFClientError)."""

    def __init__(self, *, reject=(), raise_on=()):
        self.creates, self.deletes = [], []
        self._reject, self._raise_on = set(reject), set(raise_on)
        self._n = 0

    def create(self, sobject, field_values):
        self.creates.append((sobject, dict(field_values)))
        if sobject in self._raise_on:
            raise SFClientError(f"transport boom on {sobject}")
        self._n += 1
        ok = sobject not in self._reject
        return {"http_status": 201 if ok else 400, "success": ok,
                "record_id": f"id-{sobject}-{self._n}" if ok else None,
                "api_response": {"body": {}}}

    def delete(self, sobject, record_id):
        self.deletes.append((sobject, record_id))
        return {"success": True}


def _scalar(api):
    return {"api": api, "field_type": "string", "is_nillable": False}


def _ref(api, to):
    return {"api": api, "field_type": "reference", "is_nillable": False, "ref": to}


def _construct(graph, object_api, semantic=(), **kw):
    s1, client, tracker = _GraphS1(graph), _StubClient(**kw), CreatedRecordTracker()
    out = construct_world(object_api, set(semantic), s1=s1, client=client,
                          tracker=tracker, at_seq=7)
    return out, client, tracker


# ---------------------------------------------------------------------------

class TestConstructWorld:
    def test_one_hop_parent_built_and_id_threaded(self):
        graph = {
            "Contact": [_scalar("LastName"), _ref("AccountId", "Account")],
            "Account": [_scalar("Name")],
        }
        (scalar, parents, unfillable), client, tracker = _construct(graph, "Contact")
        assert unfillable == ()
        assert scalar == {"LastName": "PQA"}
        assert parents == {"AccountId": "id-Account-1"}      # parent id threaded
        assert client.creates == [("Account", {"Name": "PQA"})]   # only the parent
        assert tracker.records == (CreatedRecord("Account", "id-Account-1", 0),)
        # reverse-order teardown deletes the one parent.
        tracker.teardown(client, _best_effort_delete)
        assert client.deletes == [("Account", "id-Account-1")]

    def test_two_level_chain_built_bottom_up_torn_down_top_down(self):
        graph = {
            "Child": [_ref("C_ref", "Parent")],
            "Parent": [_ref("P_ref", "Grand")],
            "Grand": [_scalar("Name")],
        }
        (scalar, parents, unfillable), client, tracker = _construct(graph, "Child")
        assert unfillable == ()
        # creation order: grandparent, then parent (each needs the one below).
        assert client.creates == [
            ("Grand", {"Name": "PQA"}),
            ("Parent", {"P_ref": "id-Grand-1"}),
        ]
        assert parents == {"C_ref": "id-Parent-2"}
        # teardown is reverse creation order: parent before grandparent.
        tracker.teardown(client, _best_effort_delete)
        assert client.deletes == [("Parent", "id-Parent-2"), ("Grand", "id-Grand-1")]

    def test_owner_reference_is_omitted_not_constructed(self):
        # OwnerId is createable + required, but Salesforce defaults it — we omit it
        # (never build a User to own a test record). No create, no unfillable.
        graph = {
            "Account": [_scalar("Name"), _ref("OwnerId", "User")],
            "User": [_scalar("Username")],
        }
        (scalar, parents, unfillable), client, tracker = _construct(graph, "Account")
        assert scalar == {"Name": "PQA"}
        assert parents == {} and unfillable == ()      # OwnerId omitted, not built
        assert client.creates == [] and tracker.records == ()

    def test_non_createable_required_reference_is_skipped(self):
        # CreatedById (is_createable=False) never reaches the construct path — it is
        # filtered in padding, so it is neither built nor unfillable.
        graph = {
            "Account": [_scalar("Name"),
                        {"api": "CreatedById", "field_type": "reference",
                         "is_nillable": False, "is_createable": False, "ref": "User"}],
            "User": [_scalar("Username")],
        }
        (scalar, parents, unfillable), client, tracker = _construct(graph, "Account")
        assert scalar == {"Name": "PQA"}
        assert parents == {} and unfillable == () and client.creates == []

    def test_self_reference_cycle_guard_creates_nothing(self):
        graph = {"A": [_ref("A_ref", "A")]}      # A requires a parent A
        (scalar, parents, unfillable), client, tracker = _construct(graph, "A")
        assert unfillable == ("A_ref",)
        assert parents == {} and client.creates == []      # zero org writes
        assert tracker.records == ()

    def test_chain_deeper_than_bound_creates_nothing(self):
        assert MAX_PARENT_DEPTH == 3
        graph = {
            "A": [_ref("r", "B")], "B": [_ref("r", "C")], "C": [_ref("r", "D")],
            "D": [_ref("r", "E")], "E": [_scalar("Name")],     # 4 hops > bound
        }
        (scalar, parents, unfillable), client, tracker = _construct(graph, "A")
        assert unfillable == ("r",)
        assert client.creates == [] and tracker.records == ()   # honest, no writes

    def test_scalar_only_object_takes_no_parent_path(self):
        # No required references → behaves exactly like the leaf resolver: scalars
        # padded, no org writes, empty tracker. (The no-regression invariant.)
        graph = {"Lead": [_scalar("LastName"), _scalar("Company")]}
        (scalar, parents, unfillable), client, tracker = _construct(
            graph, "Lead", semantic=("LastName",))      # LastName is recipe-set (k16)
        assert scalar == {"Company": "PQA"}
        assert parents == {} and unfillable == ()
        assert client.creates == [] and tracker.records == ()

    def test_parent_create_rejected_marks_unfillable_no_leak(self):
        graph = {
            "Contact": [_scalar("LastName"), _ref("AccountId", "Account")],
            "Account": [_scalar("Name")],
        }
        (scalar, parents, unfillable), client, tracker = _construct(
            graph, "Contact", reject={"Account"})
        assert unfillable == ("AccountId",)
        assert client.creates == [("Account", {"Name": "PQA"})]   # attempt was made
        assert tracker.records == ()         # a rejected create is NOT recorded

    def test_sibling_partial_build_is_tracked_for_caller_teardown(self):
        # Two required refs: the first parent builds, the second is rejected. The
        # built parent stays on the tracker so the caller (_run_positive) tears it
        # down on the unfillable path — no leak.
        graph = {
            "X": [_ref("ref1", "A"), _ref("ref2", "Bad")],
            "A": [_scalar("Name")], "Bad": [_scalar("Name")],
        }
        (scalar, parents, unfillable), client, tracker = _construct(
            graph, "X", reject={"Bad"})
        assert unfillable == ("ref2",)
        assert tracker.records == (CreatedRecord("A", "id-A-1", 0),)
        tracker.teardown(client, _best_effort_delete)
        assert client.deletes == [("A", "id-A-1")]      # the partial build cleaned

    def test_transport_failure_mid_build_propagates_sfclienterror(self):
        # A transport raise during a parent create propagates (the caller catches
        # it + tears down what was built); the earlier parent is on the tracker.
        graph = {
            "X": [_ref("ref1", "A"), _ref("ref2", "Boom")],
            "A": [_scalar("Name")], "Boom": [_scalar("Name")],
        }
        s1 = _GraphS1(graph)
        client = _StubClient(raise_on={"Boom"})
        tracker = CreatedRecordTracker()
        with pytest.raises(SFClientError):
            construct_world("X", set(), s1=s1, client=client, tracker=tracker, at_seq=7)
        assert tracker.records == (CreatedRecord("A", "id-A-1", 0),)   # built before boom


# ---------------------------------------------------------------------------
# D-227.5 — provisioned-parent creates must speak BARE field names
# ---------------------------------------------------------------------------

class TestParentCreateBareification:
    def test_parent_create_uses_bare_field_names(self):
        # Production S1 field api names are object-QUALIFIED
        # ("Case.IsEscalated"); the live REST API speaks bare names. The
        # provisioned-parent create must bare-ify like the top-level path —
        # qualified keys get rejected by Salesforce and mis-read as an
        # unfillable world (the be56416d live error). The older tests above
        # use bare fixture names, which is why this stayed invisible.
        graph = {
            "Escalation__c": [_ref("Escalation__c.Case__c", "Case")],
            "Case": [{"api": "Case.IsEscalated", "field_type": "boolean",
                      "is_nillable": False}],
        }
        (scalar, parents, unfillable), client, _ = _construct(
            graph, "Escalation__c", semantic=("Escalation__c.Account__c",))
        assert unfillable == ()
        assert client.creates == [("Case", {"IsEscalated": False})]
        assert parents == {"Escalation__c.Case__c": "id-Case-1"}


# ---------------------------------------------------------------------------

class TestPlanBuildSplit:
    """D-230.2: construct_world is split into plan_world (S1 reads → detached
    WorldPlan, NO creates) + build_world (creates from the plan, NO S1 reads). These
    lock that contract so the async data path's snapshot stays drift-proof."""

    _GRAPH = {
        "Contact": [_scalar("LastName"), _ref("AccountId", "Account")],
        "Account": [_scalar("Name")],
    }

    def test_plan_world_resolves_a_detached_plan_without_any_client(self):
        # plan_world takes NO client/tracker — it cannot create. The result is plain
        # detached data: the scalar filler + the recursive parent subtree.
        wp = plan_world("Contact", set(), s1=_GraphS1(self._GRAPH), at_seq=7)
        assert isinstance(wp, WorldPlan)
        assert wp.object_api == "Contact"
        assert wp.scalar_filler == {"LastName": "PQA"}
        assert wp.unfillable == ()
        assert len(wp.parent_plans) == 1
        child_field, parent_api, parent_plan = wp.parent_plans[0]
        assert child_field == "AccountId" and parent_api == "Account"
        assert isinstance(parent_plan, WorldPlan)
        assert parent_plan.scalar_filler == {"Name": "PQA"}
        assert parent_plan.parent_plans == ()

    def test_build_world_creates_from_a_plan_with_no_s1(self):
        # build_world's signature has no s1 — it builds purely from the plan. The
        # parent (Account) is created first, its id threaded into AccountId, and
        # recorded on the tracker for reverse-order teardown.
        wp = plan_world("Contact", set(), s1=_GraphS1(self._GRAPH), at_seq=7)
        client, tracker = _StubClient(), CreatedRecordTracker()
        scalar, parents, unfillable = build_world(wp, client=client, tracker=tracker)
        assert unfillable == ()
        assert scalar == {"LastName": "PQA"}
        assert client.creates == [("Account", {"Name": "PQA"})]
        assert parents == {"AccountId": "id-Account-1"}
        assert tracker.records == (CreatedRecord("Account", "id-Account-1", 0),)

    def test_construct_world_equals_build_of_plan(self):
        # The wrapper is exactly build_world(plan_world(...)) — same outcome as the
        # composed call (proven against the standalone _construct above).
        wp = plan_world("Contact", set(), s1=_GraphS1(self._GRAPH), at_seq=7)
        client, tracker = _StubClient(), CreatedRecordTracker()
        via_split = build_world(wp, client=client, tracker=tracker)
        (scalar, parents, unfillable), client2, _ = _construct(self._GRAPH, "Contact")
        assert via_split == (scalar, parents, unfillable)
        assert client.creates == client2.creates

    def test_plan_data_recipe_world_covers_ordinary_creates_only(self):
        # plan_data_recipe_world builds a WorldPlan per ORDINARY create (the async
        # SELECT-bracket pre-resolution); a create flagged expect_rejection (no
        # world) is excluded. Real PlannedCreate steps (the isinstance gate).
        from primeqa.execution_engine.data_executor import plan_data_recipe_world
        from primeqa.execution_engine.plan import PlannedCreate
        from primeqa.test_representation.models.recipes.data_recipe import (
            RejectionExpectation,
        )
        from primeqa.test_representation.models.references import LogicalRef

        ref = LogicalRef(entity_type="Object", external_id="Account")
        ordinary = PlannedCreate(
            step_id="c-account", target_object=ref, field_values={})
        rejected = PlannedCreate(
            step_id="c-rej", target_object=ref, field_values={},
            expect_rejection=RejectionExpectation(error_code="DUPLICATE_VALUE"))

        plan = type("P", (), {"steps": [ordinary, rejected]})()
        plans = plan_data_recipe_world(plan, _GraphS1(self._GRAPH))
        assert set(plans) == {"c-account"}          # only the ordinary create
        assert isinstance(plans["c-account"], WorldPlan)
        assert plans["c-account"].object_api == "Account"

"""Unit tests for operational-padding construction (D-115 side B, k16) — stub S1,
no PG.

``resolve_operational_padding`` reads an object's required-on-create fields from
S1 and fills the writable, non-lookup, non-semantic ones with type-valid filler;
fields it cannot synthesize (lookups, unknown types, picklists with no readable
value set) land in ``unfillable``. The semantic field-under-test is never padded
(k16)."""
from __future__ import annotations

from primeqa.execution_engine.world import resolve_operational_padding


# ---------------------------------------------------------------------------
# Stub S1 — models one object's fields (+ optional picklist value sets)
# ---------------------------------------------------------------------------

class _Ent:
    def __init__(self, eid, etype, api):
        self.id, self.entity_type, self.sf_api_name = eid, etype, api
        self.attributes = {}


class _Rel:
    def __init__(self, entity):
        self.entity = entity


class _StubS1:
    """``fields``: list of dicts (api + field_details columns). ``picklists``:
    {pvs_id: [ {value_api_name, is_active, is_default, sort_order} ]}."""

    def __init__(self, object_api, fields, *, version=5, picklists=None):
        self._object_api, self._version = object_api, version
        self._obj_id = "obj-" + object_api
        self._field_ents, self._detail, self._pv_ents = [], {}, {}
        for f in fields:
            fid = "fld-" + f["api"]
            self._field_ents.append(_Ent(fid, "Field", f["api"]))
            self._detail[fid] = {
                "field_type": f.get("field_type", "string"),
                "is_nillable": f.get("is_nillable", True),
                "is_calculated": f.get("is_calculated", False),
                "is_createable": f.get("is_createable", True),
                "references_object_entity_id": f.get("references_object_entity_id"),
                "picklist_value_set_entity_id": f.get("picklist_value_set_entity_id"),
                "length": f.get("length"),
            }
        for pvs_id, values in (picklists or {}).items():
            ents = []
            for j, v in enumerate(values):
                pvid = f"pv-{pvs_id}-{j}"
                ents.append(_Ent(pvid, "PicklistValue", v["value_api_name"]))
                self._detail[pvid] = {
                    "value_api_name": v["value_api_name"],
                    "is_active": v.get("is_active", True),
                    "is_default": v.get("is_default", False),
                    "sort_order": v.get("sort_order", j),
                }
            self._pv_ents[pvs_id] = ents

    def current_version_seq(self):
        return self._version

    def get_entities(self, entity_type, at_seq, filters=None):
        if (entity_type == "Object" and filters
                and filters.get("sf_api_name") == self._object_api):
            return [_Ent(self._obj_id, "Object", self._object_api)]
        return []

    def get_related(self, entity_id, edge_types, direction, at_seq):
        if entity_id == self._obj_id:
            return [_Rel(e) for e in self._field_ents]
        if entity_id in self._pv_ents:
            return [_Rel(e) for e in self._pv_ents[entity_id]]
        return []

    def get_entity_details(self, entity_id, at_seq):
        return self._detail.get(entity_id)


def _pad(s1, object_api="Account", semantic=("Status__c",)):
    return resolve_operational_padding(
        object_api, set(semantic), s1=s1, at_seq=s1.current_version_seq())


# ---------------------------------------------------------------------------
# Filling
# ---------------------------------------------------------------------------

def test_required_text_is_filled():
    s1 = _StubS1("Account", [
        {"api": "Status__c"},                                    # semantic — skipped
        {"api": "Name", "field_type": "string", "is_nillable": False}])
    res = _pad(s1)
    assert res.unfillable == ()
    assert res.filler == {"Name": "PQA"}


def test_semantic_field_never_padded_even_if_required():
    # Status__c is required (nillable False) but is the value under test → never filled.
    s1 = _StubS1("Account", [
        {"api": "Status__c", "field_type": "picklist", "is_nillable": False}])
    res = _pad(s1, semantic=("Status__c",))
    assert res.filler == {} and res.unfillable == ()


def test_optional_field_skipped():
    s1 = _StubS1("Account", [{"api": "Note__c", "field_type": "string", "is_nillable": True}])
    assert _pad(s1).filler == {}


def test_calculated_required_field_skipped():
    s1 = _StubS1("Account", [
        {"api": "Roll__c", "field_type": "double", "is_nillable": False, "is_calculated": True}])
    assert _pad(s1).filler == {}


def test_types_filled_by_kind():
    s1 = _StubS1("T__c", [
        {"api": "N__c", "field_type": "double", "is_nillable": False},
        {"api": "B__c", "field_type": "boolean", "is_nillable": False},
        {"api": "D__c", "field_type": "date", "is_nillable": False},
        {"api": "E__c", "field_type": "email", "is_nillable": False}])
    f = _pad(s1, object_api="T__c", semantic=()).filler
    assert f["N__c"] == 1 and f["B__c"] is False
    assert f["E__c"] == "pqa@example.com"
    assert f["D__c"]                          # an ISO date string


# ---------------------------------------------------------------------------
# Required references (F6.2 — collected for construct_world, no longer fenced)
# + genuinely-unfillable types (still a hard stop)
# ---------------------------------------------------------------------------

def test_required_lookup_goes_to_required_refs():
    # F6.2: a required reference is no longer fenced into unfillable — it is
    # collected as a required_ref (field_api, referenced_object_entity_id) so
    # construct_world can build the parent and thread its id.
    s1 = _StubS1("Account", [
        {"api": "Owner__c", "field_type": "reference", "is_nillable": False,
         "references_object_entity_id": "obj-User"}])
    res = _pad(s1)
    assert res.filler == {} and res.unfillable == ()
    assert res.required_refs == (("Owner__c", "obj-User"),)


def test_optional_lookup_is_skipped_not_a_required_ref():
    # A nillable reference is filtered before the reference check (Salesforce
    # does not force it on create) — neither padded, required, nor unfillable.
    s1 = _StubS1("Account", [
        {"api": "Opt__c", "field_type": "reference", "is_nillable": True,
         "references_object_entity_id": "obj-User"}])
    res = _pad(s1)
    assert res.filler == {} and res.unfillable == () and res.required_refs == ()


def test_unknown_type_is_unfillable():
    s1 = _StubS1("Account", [{"api": "Loc__c", "field_type": "location", "is_nillable": False}])
    res = _pad(s1)
    assert res.unfillable == ("Loc__c",) and res.required_refs == ()


def test_non_createable_required_fields_are_skipped():
    # is_nillable=False but is_createable=False — Salesforce-managed audit/system
    # fields (CreatedDate scalar, CreatedById reference). Setting EITHER gets the
    # whole create rejected, so both are skipped: not padded, not a required_ref,
    # not unfillable. Only the createable scalar is padded.
    s1 = _StubS1("Account", [
        {"api": "CreatedDate", "field_type": "datetime", "is_nillable": False,
         "is_createable": False},
        {"api": "CreatedById", "field_type": "reference", "is_nillable": False,
         "is_createable": False, "references_object_entity_id": "obj-User"},
        {"api": "Name", "field_type": "string", "is_nillable": False,
         "is_createable": True}])
    res = _pad(s1)
    assert res.filler == {"Name": "PQA"}
    assert res.unfillable == () and res.required_refs == ()


def test_missing_object_yields_empty_padding():
    s1 = _StubS1("Account", [{"api": "Name", "is_nillable": False}])
    res = resolve_operational_padding("Ghost__c", {"X"}, s1=s1, at_seq=5)
    assert res.filler == {} and res.unfillable == ()


# ---------------------------------------------------------------------------
# Simple-picklist filler
# ---------------------------------------------------------------------------

def test_required_picklist_uses_default_active_value():
    s1 = _StubS1("Account", [
        {"api": "Stage__c", "field_type": "picklist", "is_nillable": False,
         "picklist_value_set_entity_id": "pvs-1"}],
        picklists={"pvs-1": [
            {"value_api_name": "Open", "is_active": True, "sort_order": 1},
            {"value_api_name": "Won", "is_active": True, "is_default": True, "sort_order": 2}]})
    assert _pad(s1).filler == {"Stage__c": "Won"}


def test_required_picklist_first_active_when_no_default():
    s1 = _StubS1("Account", [
        {"api": "Stage__c", "field_type": "picklist", "is_nillable": False,
         "picklist_value_set_entity_id": "pvs-1"}],
        picklists={"pvs-1": [
            {"value_api_name": "B", "is_active": True, "sort_order": 2},
            {"value_api_name": "A", "is_active": True, "sort_order": 1}]})
    assert _pad(s1).filler == {"Stage__c": "A"}            # lowest sort_order


def test_required_picklist_without_value_set_is_unfillable():
    s1 = _StubS1("Account", [
        {"api": "Stage__c", "field_type": "picklist", "is_nillable": False}])
    res = _pad(s1)
    assert res.filler == {} and res.unfillable == ("Stage__c",)

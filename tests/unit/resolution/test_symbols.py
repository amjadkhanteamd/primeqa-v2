"""SymbolTable hydration from a faked SemanticOrgModel bulk-read surface."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.resolution.symbols import hydrate_symbol_table


def _entity(id, etype, api, label, attributes=None):
    return SimpleNamespace(id=id, entity_type=etype, sf_api_name=api,
                           display_name=label, attributes=attributes or {})


class FakeModel:
    """The 5-method bulk surface hydrate_symbol_table consumes."""

    def __init__(self):
        self.obj_id = uuid4()
        self.fld_status = uuid4()
        self.fld_lookup = uuid4()
        self.target_id = uuid4()
        self.pvs_id = uuid4()
        self.connected_org_id = "11111111-1111-1111-1111-111111111111"

    def get_entities(self, entity_type, at_seq, filters=None):
        assert at_seq == 7
        if entity_type == "Object":
            return [
                _entity(self.obj_id, "Object", "PLS_FB_Order__c", "PLS FB Order"),
                _entity(self.target_id, "Object", "Account", "Account"),
            ]
        return []

    def get_entity_details_bulk(self, entity_type, at_seq):
        if entity_type == "Object":
            return {self.obj_id: {"is_custom": True},
                    self.target_id: {"is_custom": False}}
        return {
            self.fld_status: {"field_type": "picklist",
                              "picklist_value_set_entity_id": str(self.pvs_id)},
            self.fld_lookup: {"field_type": "reference",
                              "references_object_entity_id": str(self.target_id)},
        }

    def get_related_bulk(self, edge_types, direction, at_seq):
        assert edge_types == ["BELONGS_TO"] and direction == "inbound"
        return {self.obj_id: [
            SimpleNamespace(entity=_entity(
                self.fld_status, "Field",
                "PLS_FB_Order__c.PLS_FB_Status__c", "Status")),
            SimpleNamespace(entity=_entity(
                self.fld_lookup, "Field",
                "PLS_FB_Order__c.PLS_FB_Account__c", "Account")),
            SimpleNamespace(entity=_entity(
                uuid4(), "ValidationRule", "PLS_FB_Order__c.VR01", "VR01")),
        ]}

    def get_picklist_values_bulk(self, at_seq):
        return {self.pvs_id: [
            {"value_api_name": "Draft", "value_label": "Draft"},
            {"value_api_name": "Submitted", "value_label": "Submitted"},
        ]}


def test_hydration_builds_bare_names_picklists_and_references():
    t = hydrate_symbol_table(FakeModel(), 7)
    assert t.at_seq == 7
    assert str(t.connected_org_id) == "11111111-1111-1111-1111-111111111111"
    obj = t.by_api("pls_fb_order__c")           # ci lookup
    assert obj is not None and obj.is_custom
    names = [f.api_name for f in obj.fields]
    assert names == ["PLS_FB_Account__c", "PLS_FB_Status__c"]   # sorted, bare
    status = next(f for f in obj.fields if f.api_name == "PLS_FB_Status__c")
    assert status.qualified_api_name == "PLS_FB_Order__c.PLS_FB_Status__c"
    assert [v for v, _ in status.picklist_values] == ["Draft", "Submitted"]
    lookup = next(f for f in obj.fields if f.api_name == "PLS_FB_Account__c")
    assert lookup.references_object == "Account"
    # the VR row on the BELONGS_TO walk is ignored (Fields only)
    assert len(obj.fields) == 2
    # a fieldless object still hydrates
    assert t.by_api("Account").fields == ()

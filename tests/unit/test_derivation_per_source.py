"""Unit tests for primeqa.semantic.derivation per-source edge generators.

These are pure-function tests: input row dicts -> expected output edge
dicts. No DB access. Fast.

Each generator function is tested with:
  - Minimal input (only required fields)
  - Maximal input (all optional fields populated)
  - Edge cases (NULL optional FKs, missing trigger types, etc.)
"""
import pytest

from primeqa.semantic.derivation import (
    _edges_from_field_row,
    _edges_from_record_type_row,
    _edges_from_layout_row,
    _edges_from_validation_rule_row,
    _edges_from_picklist_value_row,
    _edges_from_user_row,
    _edges_from_flow_row,
)


pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------
# _edges_from_field_row
# ----------------------------------------------------------------------

class TestFieldRow:
    def test_minimal_field_produces_belongs_to_only(self, sample_uuids):
        field_id, obj_id = sample_uuids[0], sample_uuids[1]
        detail = {"object_entity_id": obj_id,
                  "references_object_entity_id": None,
                  "picklist_value_set_entity_id": None}
        edges = _edges_from_field_row(field_id, detail, {})
        assert len(edges) == 1
        assert edges[0]["edge_type"] == "BELONGS_TO"
        assert edges[0]["source_entity_id"] == field_id
        assert edges[0]["target_entity_id"] == obj_id

    def test_lookup_field_produces_belongs_to_and_relationship(self, sample_uuids):
        field_id, obj_id, ref_obj_id = sample_uuids[0], sample_uuids[1], sample_uuids[2]
        detail = {"object_entity_id": obj_id,
                  "references_object_entity_id": ref_obj_id,
                  "picklist_value_set_entity_id": None}
        edges = _edges_from_field_row(field_id, detail, {})
        edge_types = sorted(e["edge_type"] for e in edges)
        assert edge_types == ["BELONGS_TO", "HAS_RELATIONSHIP_TO"]
        rel = next(e for e in edges if e["edge_type"] == "HAS_RELATIONSHIP_TO")
        assert rel["target_entity_id"] == ref_obj_id

    def test_picklist_field_produces_belongs_to_and_picklist_values(self, sample_uuids):
        field_id, obj_id, pvs_id = sample_uuids[0], sample_uuids[1], sample_uuids[2]
        detail = {"object_entity_id": obj_id,
                  "references_object_entity_id": None,
                  "picklist_value_set_entity_id": pvs_id}
        edges = _edges_from_field_row(field_id, detail, {})
        edge_types = sorted(e["edge_type"] for e in edges)
        assert edge_types == ["BELONGS_TO", "HAS_PICKLIST_VALUES"]
        pl = next(e for e in edges if e["edge_type"] == "HAS_PICKLIST_VALUES")
        assert pl["target_entity_id"] == pvs_id

    def test_field_with_both_ref_and_picklist_produces_three_edges(self, sample_uuids):
        # Edge case: a field that's both a lookup AND has a picklist set.
        # Unusual in practice but the schema doesn't forbid it.
        field_id, obj_id, ref_obj_id, pvs_id = sample_uuids[:4]
        detail = {"object_entity_id": obj_id,
                  "references_object_entity_id": ref_obj_id,
                  "picklist_value_set_entity_id": pvs_id}
        edges = _edges_from_field_row(field_id, detail, {})
        assert len(edges) == 3
        edge_types = sorted(e["edge_type"] for e in edges)
        assert edge_types == ["BELONGS_TO", "HAS_PICKLIST_VALUES", "HAS_RELATIONSHIP_TO"]


# ----------------------------------------------------------------------
# _edges_from_record_type_row
# ----------------------------------------------------------------------

class TestRecordTypeRow:
    def test_record_type_no_grants(self, sample_uuids):
        rt_id, obj_id = sample_uuids[0], sample_uuids[1]
        detail = {"object_entity_id": obj_id}
        edges = _edges_from_record_type_row(rt_id, detail, {}, grants=[])
        assert len(edges) == 1
        assert edges[0]["edge_type"] == "BELONGS_TO"

    def test_record_type_with_grants_produces_edge_per_grant(self, sample_uuids):
        rt_id, obj_id = sample_uuids[0], sample_uuids[1]
        pv1, pv2, pv3 = sample_uuids[2], sample_uuids[3], sample_uuids[4]
        detail = {"object_entity_id": obj_id}
        grants = [
            {"record_type_entity_id": rt_id, "picklist_value_entity_id": pv1},
            {"record_type_entity_id": rt_id, "picklist_value_entity_id": pv2},
            {"record_type_entity_id": rt_id, "picklist_value_entity_id": pv3},
        ]
        edges = _edges_from_record_type_row(rt_id, detail, {}, grants)
        # 1 BELONGS_TO + 3 CONSTRAINS_PICKLIST_VALUES = 4
        assert len(edges) == 4
        constrain_edges = [e for e in edges if e["edge_type"] == "CONSTRAINS_PICKLIST_VALUES"]
        assert len(constrain_edges) == 3
        assert {e["target_entity_id"] for e in constrain_edges} == {pv1, pv2, pv3}


# ----------------------------------------------------------------------
# _edges_from_layout_row
# ----------------------------------------------------------------------

class TestLayoutRow:
    def test_layout_produces_one_belongs_to(self, sample_uuids):
        layout_id, obj_id = sample_uuids[0], sample_uuids[1]
        detail = {"object_entity_id": obj_id}
        edges = _edges_from_layout_row(layout_id, detail, {})
        assert len(edges) == 1
        assert edges[0]["edge_type"] == "BELONGS_TO"
        assert edges[0]["target_entity_id"] == obj_id


# ----------------------------------------------------------------------
# _edges_from_validation_rule_row
# ----------------------------------------------------------------------

class TestValidationRuleRow:
    def test_rule_no_field_refs_produces_belongs_to_and_applies_to(self, sample_uuids):
        rule_id, obj_id = sample_uuids[0], sample_uuids[1]
        detail = {"object_entity_id": obj_id}
        edges = _edges_from_validation_rule_row(rule_id, detail, {}, field_refs=[])
        assert len(edges) == 2
        edge_types = sorted(e["edge_type"] for e in edges)
        assert edge_types == ["APPLIES_TO", "BELONGS_TO"]
        # Both target the same Object
        for e in edges:
            assert e["target_entity_id"] == obj_id

    def test_rule_with_field_refs_produces_references_per_ref(self, sample_uuids):
        rule_id, obj_id, f1, f2 = sample_uuids[0], sample_uuids[1], sample_uuids[2], sample_uuids[3]
        detail = {"object_entity_id": obj_id}
        field_refs = [
            {"validation_rule_entity_id": rule_id, "field_entity_id": f1,
             "reference_type": "read", "is_priorvalue": False,
             "is_ischanged": False, "is_isnew": False},
            {"validation_rule_entity_id": rule_id, "field_entity_id": f1,
             "reference_type": "priorvalue", "is_priorvalue": True,
             "is_ischanged": False, "is_isnew": False},
            {"validation_rule_entity_id": rule_id, "field_entity_id": f2,
             "reference_type": "ischanged", "is_priorvalue": False,
             "is_ischanged": True, "is_isnew": False},
        ]
        edges = _edges_from_validation_rule_row(rule_id, detail, {}, field_refs)
        # 1 BELONGS_TO + 1 APPLIES_TO + 3 REFERENCES = 5
        assert len(edges) == 5
        ref_edges = [e for e in edges if e["edge_type"] == "REFERENCES"]
        assert len(ref_edges) == 3
        # f1 referenced twice (different reference_type)
        f1_refs = [e for e in ref_edges if e["target_entity_id"] == f1]
        assert len(f1_refs) == 2
        # Properties carry through correctly
        ref_types = sorted(e["properties"]["reference_type"] for e in f1_refs)
        assert ref_types == ["priorvalue", "read"]


# ----------------------------------------------------------------------
# _edges_from_picklist_value_row
# ----------------------------------------------------------------------

class TestPicklistValueRow:
    def test_pv_produces_belongs_to_picklist_value_set(self, sample_uuids):
        pv_id, pvs_id = sample_uuids[0], sample_uuids[1]
        detail = {"picklist_value_set_entity_id": pvs_id}
        edges = _edges_from_picklist_value_row(pv_id, detail, {})
        assert len(edges) == 1
        assert edges[0]["edge_type"] == "BELONGS_TO"
        assert edges[0]["target_entity_id"] == pvs_id


# ----------------------------------------------------------------------
# _edges_from_user_row
# ----------------------------------------------------------------------

class TestUserRow:
    def test_user_produces_has_profile(self, sample_uuids):
        user_id, prof_id = sample_uuids[0], sample_uuids[1]
        detail = {"profile_entity_id": prof_id}
        edges = _edges_from_user_row(user_id, detail, {})
        assert len(edges) == 1
        assert edges[0]["edge_type"] == "HAS_PROFILE"
        assert edges[0]["edge_category"] == "PERMISSION"
        assert edges[0]["target_entity_id"] == prof_id


# ----------------------------------------------------------------------
# _edges_from_flow_row
# ----------------------------------------------------------------------

class TestFlowRow:
    def test_flow_with_no_trigger_object_produces_no_edges(self, sample_uuids):
        flow_id = sample_uuids[0]
        detail = {"triggers_on_object_entity_id": None,
                  "trigger_type": None}
        edges = _edges_from_flow_row(flow_id, detail, {})
        assert len(edges) == 0

    def test_flow_with_trigger_object_but_no_type_skips_edge(self, sample_uuids):
        # Anomalous: trigger object set but trigger_type missing.
        # Generator skips with warning rather than failing.
        flow_id, obj_id = sample_uuids[0], sample_uuids[1]
        detail = {"triggers_on_object_entity_id": obj_id,
                  "trigger_type": None}
        edges = _edges_from_flow_row(flow_id, detail, {})
        assert len(edges) == 0

    def test_flow_record_triggered_produces_triggers_on(self, sample_uuids):
        flow_id, obj_id = sample_uuids[0], sample_uuids[1]
        detail = {"triggers_on_object_entity_id": obj_id,
                  "trigger_type": "AfterSave"}
        edges = _edges_from_flow_row(flow_id, detail, {})
        assert len(edges) == 1
        assert edges[0]["edge_type"] == "TRIGGERS_ON"
        assert edges[0]["properties"]["trigger_type"] == "AfterSave"

    def test_flow_with_condition_text_in_attributes(self, sample_uuids):
        flow_id, obj_id = sample_uuids[0], sample_uuids[1]
        detail = {"triggers_on_object_entity_id": obj_id,
                  "trigger_type": "BeforeSave"}
        attrs = {"entry_condition_text": "Amount > 0"}
        edges = _edges_from_flow_row(flow_id, detail, attrs)
        assert len(edges) == 1
        assert edges[0]["properties"]["condition_text"] == "Amount > 0"

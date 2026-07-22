"""D-382 (SUB-4) — the S6 S1 reader propagates UNKNOWN, never invents.

A missing detail row (the live SUB-4 shape: entities synced, detail write
raced/failed) must yield ``is_active``/``is_createable`` = ``None`` — the
prior invented ``True`` defaults let attribution fabricate verdicts on
metadata that was never read."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.interpretation.s1_reader import S1ValidationRuleReader


def _ent(entity_type, api, attrs=None):
    return SimpleNamespace(id=uuid4(), entity_type=entity_type,
                           sf_api_name=api, display_name=api,
                           attributes=attrs or {})


class _Model:
    """Minimal SemanticOrgModel stub: one object, one VR, one Flow, one
    Field; per-entity detail rows injectable (missing = the SUB-4 shape)."""

    def __init__(self, details_by_id=None):
        self.obj = _ent("Object", "PLS_FB_Order__c")
        self.vr = _ent("ValidationRule", "PLS_FB_Order__c.VR01",
                       attrs={"formula_text": "ISBLANK(X__c)",
                              "error_message": "X required"})
        self.flow = _ent("Flow", "PLS_FB_FL01_Default_Priority")
        self.fld = _ent("Field", "PLS_FB_Order__c.PLS_FB_Priority__c")
        self._details = details_by_id or {}

    def current_version_seq(self):
        return 7

    def get_entities(self, entity_type, at_seq, filters=None):
        if entity_type == "Object":
            return [self.obj]
        return []

    def get_related(self, entity_id, edge_types, direction, at_seq):
        if "APPLIES_TO" in edge_types:
            return [SimpleNamespace(entity=self.vr)]
        if "TRIGGERS_ON" in edge_types:
            return [SimpleNamespace(entity=self.flow)]
        if "BELONGS_TO" in edge_types:
            return [SimpleNamespace(entity=self.fld)]
        return []

    def get_entity_details(self, entity_id, at_seq):
        return self._details.get(entity_id, {})


def test_missing_detail_rows_propagate_none_not_true():
    m = _Model()          # NO detail rows anywhere — the SUB-4 shape
    r = S1ValidationRuleReader(m)
    [vr] = r.vrs_for_object("PLS_FB_Order__c")
    assert vr.is_active is None
    [fl] = r.flows_for_object("PLS_FB_Order__c")
    assert fl.is_active is None
    fm = r.field_meta("PLS_FB_Order__c", "PLS_FB_Priority__c")
    assert fm is not None and fm.is_createable is None


def test_present_detail_rows_keep_real_booleans():
    m = _Model()
    m._details = {m.vr.id: {"is_active": False},
                  m.flow.id: {"is_active": True, "trigger_type": "BeforeSave"},
                  m.fld.id: {"is_createable": False}}
    r = S1ValidationRuleReader(m)
    [vr] = r.vrs_for_object("PLS_FB_Order__c")
    assert vr.is_active is False
    [fl] = r.flows_for_object("PLS_FB_Order__c")
    assert fl.is_active is True and fl.trigger_type == "BeforeSave"
    fm = r.field_meta("PLS_FB_Order__c", "PLS_FB_Priority__c")
    assert fm.is_createable is False
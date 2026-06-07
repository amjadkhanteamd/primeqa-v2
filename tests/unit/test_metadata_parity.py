"""Cutover Step 4a (D-185) unit: MetadataParityChecker.

Synthetic readers (plain namespaces matching the meta_* / S1 read shapes) so the
checker's diff logic is exercised without a DB. Confirms: clean when the readers
agree on the shape axis; SHAPE divergence (a flag differs on a shared field/object)
is caught and fails ``clean``; MEMBERSHIP divergence (only_meta / only_s1) is
reported but does NOT fail ``clean``; and the excluded axes (reference_to /
picklist_values) never produce a false mismatch.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from primeqa.metadata_bridge.parity import MetadataParityChecker

pytestmark = pytest.mark.unit


def _obj(oid, api, *, is_createable=True, is_custom=False):
    return NS(id=oid, api_name=api, is_createable=is_createable, is_custom=is_custom)


def _field(api, obj_id, *, field_type="string", is_required=False, is_custom=False,
           is_createable=True, is_updateable=True, reference_to=None,
           picklist_values=()):
    return NS(api_name=api, meta_object_id=obj_id, field_type=field_type,
              is_required=is_required, is_custom=is_custom,
              is_createable=is_createable, is_updateable=is_updateable,
              reference_to=reference_to, picklist_values=picklist_values)


def _vr(rule, obj_id, obj_api, *, error_message="msg"):
    # meta-shape: meta_object_id (int). s1-shape: meta_object (obj with .api_name).
    return NS(rule_name=rule, meta_object_id=obj_id, error_message=error_message,
              meta_object=NS(api_name=obj_api))


class _Reader:
    """A reader answering the meta_*-shaped read interface from fixed lists."""
    def __init__(self, objects, fields, vrs):
        self._o, self._f, self._v = objects, fields, vrs

    def get_objects(self, mv=None):
        return list(self._o)

    def get_fields(self, mv=None, object_id=None):
        if object_id is None:
            return list(self._f)
        return [f for f in self._f if f.meta_object_id == object_id]

    def get_validation_rules(self, mv=None, object_id=None):
        return list(self._v)


def _equal_pair():
    """Two readers that agree exactly (different object-id spaces: int vs uuid-ish
    str, to mirror v1 int PK vs S1 UUID — keyed by api_name, not id)."""
    meta = _Reader(
        [_obj(1, "Account"), _obj(2, "Opportunity")],
        [_field("Industry", 1), _field("StageName", 2, is_required=True)],
        [_vr("BlockClosed", 2, "Opportunity")],
    )
    s1 = _Reader(
        [_obj("a", "Account"), _obj("o", "Opportunity")],
        [_field("Industry", "a"), _field("StageName", "o", is_required=True)],
        # _vr carries both shapes (meta_object_id + meta_object); the S1 key path
        # uses meta_object.api_name, so this resolves to ("Opportunity","BlockClosed")
        # — the same key the meta reader produces.
        [_vr("BlockClosed", "o", "Opportunity")],
    )
    return meta, s1


class TestParityClean:
    def test_identical_readers_are_clean(self):
        meta, s1 = _equal_pair()
        rpt = MetadataParityChecker(meta, s1).check(99)
        assert rpt.clean is True
        assert rpt.shape_mismatches == 0
        assert rpt.objects["both"] == 2
        assert rpt.fields["both"] == 2
        assert rpt.vrs["both"] == 1
        assert rpt.objects["only_meta"] == [] and rpt.objects["only_s1"] == []

    def test_excluded_axes_do_not_false_positive(self):
        """reference_to + picklist_values differ but are excluded from the shape
        axis -> still clean."""
        meta = _Reader([_obj(1, "Account")],
                       [_field("OwnerId", 1, reference_to="User",
                               picklist_values=[{"value": "x"}])], [])
        s1 = _Reader([_obj("a", "Account")],
                     [_field("OwnerId", "a", reference_to=None,
                             picklist_values=("x",))], [])
        rpt = MetadataParityChecker(meta, s1).check(1)
        assert rpt.clean is True

    def test_is_required_difference_is_excluded_from_shape(self):
        """is_required is a by-design definitional difference (meta_* schema rule
        `not nillable AND not defaultedOnCreate` vs S1's layout flag), NOT a reader
        bug — excluded from the shape axis (D-190). A field differing ONLY on
        is_required stays clean."""
        meta = _Reader([_obj(1, "Account")],
                       [_field("Name", 1, is_required=True)], [])
        s1 = _Reader([_obj("a", "Account")],
                     [_field("Name", "a", is_required=False)], [])
        rpt = MetadataParityChecker(meta, s1).check(1)
        assert rpt.clean is True
        assert rpt.shape_mismatches == 0
        assert rpt.fields["both"] == 1


class TestParityShapeDivergence:
    def test_field_flag_mismatch_fails_clean(self):
        meta = _Reader([_obj(1, "Account")],
                       [_field("Industry", 1, is_createable=True)], [])
        s1 = _Reader([_obj("a", "Account")],
                     [_field("Industry", "a", is_createable=False)], [])
        rpt = MetadataParityChecker(meta, s1).check(1)
        assert rpt.clean is False
        assert rpt.shape_mismatches == 1
        mm = rpt.fields["shape_mismatch"][0]
        assert mm["field"] == "Account.Industry"
        assert mm["attrs"]["is_createable"] == {"meta": True, "s1": False}

    def test_object_flag_mismatch_fails_clean(self):
        meta = _Reader([_obj(1, "Account", is_custom=False)], [], [])
        s1 = _Reader([_obj("a", "Account", is_custom=True)], [], [])
        rpt = MetadataParityChecker(meta, s1).check(1)
        assert rpt.clean is False
        assert rpt.objects["shape_mismatch"][0]["attrs"]["is_custom"] == {
            "meta": False, "s1": True}


class TestParityMembershipDrift:
    def test_membership_divergence_reported_but_clean(self):
        """A field/object in one source but not the other is sync-time drift —
        reported in only_meta/only_s1 but does NOT fail clean."""
        meta = _Reader(
            [_obj(1, "Account"), _obj(2, "LegacyObj")],
            [_field("Industry", 1), _field("OldField", 2)], [])
        s1 = _Reader(
            [_obj("a", "Account"), _obj("n", "NewObj")],
            [_field("Industry", "a"), _field("NewField", "n")], [])
        rpt = MetadataParityChecker(meta, s1).check(1)
        assert rpt.clean is True                       # no SHAPE divergence
        assert rpt.objects["only_meta"] == ["LegacyObj"]
        assert rpt.objects["only_s1"] == ["NewObj"]
        assert rpt.fields["only_meta"] == ["LegacyObj.OldField"]
        assert rpt.fields["only_s1"] == ["NewObj.NewField"]

    def test_summary_shape(self):
        meta, s1 = _equal_pair()
        s = MetadataParityChecker(meta, s1).check(1).summary()
        assert s["clean"] is True and s["shape_mismatches"] == 0
        assert s["objects"]["both"] == 2 and s["fields"]["both"] == 2
        # list fields collapse to counts in the summary
        assert s["objects"]["only_meta"] == 0

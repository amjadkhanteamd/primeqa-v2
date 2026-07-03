"""Unit tests for primeqa/semantic/normalization.py.

Per Phase 2 plan §4.1 and D-035 (mandatory normalization before hashing
to prevent phantom changes).

For each entity type, three tests:
  1. Stability — same input → same hash
  2. Phantom-change resistance — volatile additions → same hash
  3. Real change detection — semantic edit → different hash

Plus dispatcher tests and hash-function tests.
"""
from __future__ import annotations

from collections import OrderedDict

import pytest

from primeqa.semantic.normalization import (
    _NORMALIZERS,
    hash_normalized,
    normalize,
)


pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------
# Per-type fixtures (small but realistic)
# ----------------------------------------------------------------------

def _object_fixture() -> dict:
    return {
        "name": "Account",
        "label": "Account",
        "custom": False,
        "fields": [
            {"name": "Industry", "type": "picklist", "label": "Industry"},
            {"name": "AnnualRevenue", "type": "currency", "label": "Annual Revenue"},
        ],
        "recordTypeInfos": [
            {"developerName": "Customer", "name": "Customer"},
            {"developerName": "Partner", "name": "Partner"},
        ],
        "childRelationships": [
            {"relationshipName": "Contacts", "childSObject": "Contact"},
            {"relationshipName": "Cases", "childSObject": "Case"},
        ],
        # Volatile/runtime — should be stripped:
        "urls": {"sobject": "/sobjects/Account", "rowTemplate": "/sobjects/{ID}"},
        "attributes": {"type": "Object", "url": "/services/data/..."},
        "LastModifiedDate": "2026-04-30T10:00:00Z",
    }


def _field_fixture() -> dict:
    return {
        "name": "Industry",
        "type": "picklist",
        "label": "Industry",
        "length": 40,
        "picklistValues": [
            {"value": "Technology", "label": "Technology", "active": True},
            {"value": "Finance", "label": "Finance", "active": True},
            {"value": "Government", "label": "Government", "active": True},
        ],
        "referenceTo": [],
        "urls": {"foo": "bar"},
        "LastModifiedDate": "2026-04-30T10:00:00Z",
    }


def _record_type_fixture() -> dict:
    return {
        "developerName": "CustomerAccount",
        "active": True,
        "picklistValues": [
            {"picklistName": "Industry", "values": []},
            {"picklistName": "Type", "values": []},
        ],
        "urls": {"x": "y"},
    }


def _layout_fixture() -> dict:
    return {
        "fullName": "Account-Account Layout",
        "layoutSections": [
            {
                "index": 1,
                "name": "Address",
                "layoutItems": [
                    {"position": 2, "label": "Billing"},
                    {"position": 1, "label": "Shipping"},
                ],
            },
            {
                "index": 0,
                "name": "Information",
                "layoutItems": [
                    {"position": 1, "label": "Name"},
                    {"position": 2, "label": "Owner"},
                ],
            },
        ],
        "attributes": {"type": "Layout", "url": "/..."},
    }


def _picklist_value_set_fixture() -> dict:
    return {
        "fullName": "IndustryValues",
        "masterLabel": "Industry Values",
        "description": "Standard industry classifications",
        "isRestrictedPicklist": True,
        "customValue": [
            {"valueName": "Technology", "label": "Technology"},
            {"valueName": "Finance", "label": "Finance"},
        ],
        "urls": {"metadata": "/..."},
        "LastModifiedDate": "2026-04-30T11:00:00Z",
    }


def _picklist_value_fixture() -> dict:
    return {
        "fullName": "Technology",
        "label": "Technology",
        "default": False,
        "active": True,
        "urls": {"x": "y"},
    }


def _profile_fixture() -> dict:
    return {
        "fullName": "System Administrator",
        "userLicense": "Salesforce",
        "objectPermissions": [
            {"object": "Account", "allowRead": True, "allowEdit": True},
            {"object": "Contact", "allowRead": True, "allowEdit": True},
        ],
        "fieldPermissions": [
            {"field": "Account.Industry", "readable": True, "editable": True},
            {"field": "Account.Name", "readable": True, "editable": True},
        ],
        "userPermissions": [
            {"name": "ApiEnabled", "enabled": True},
            {"name": "ManageUsers", "enabled": True},
        ],
        "urls": {"x": "y"},
    }


def _permission_set_fixture() -> dict:
    return {
        "fullName": "DataReader",
        "label": "Data Reader",
        "objectPermissions": [
            {"object": "Account", "allowRead": True},
            {"object": "Lead", "allowRead": True},
        ],
        "fieldPermissions": [
            {"field": "Account.Industry", "readable": True},
        ],
        "userPermissions": [
            {"name": "ApiEnabled", "enabled": False},
        ],
    }


def _user_fixture() -> dict:
    return {
        "Username": "admin@example.com.demo",
        "Email": "admin@example.com",
        "FirstName": "System",
        "LastName": "Admin",
        "TimeZoneSidKey": "America/Los_Angeles",
        "LocaleSidKey": "en_US",
        "IsActive": True,
        "ProfileId": "00e000000000001AAA",
        "urls": {"x": "y"},
        "LastModifiedDate": "2026-04-30T10:00:00Z",
        "CreatedDate": "2025-01-01T00:00:00Z",
    }


def _validation_rule_fixture() -> dict:
    return {
        "fullName": "Account.AmountPositive",
        "active": True,
        "errorConditionFormula": "Amount < 0",
        "errorMessage": "Amount must be non-negative.",
        "errorDisplayField": "Amount",
        "urls": {"x": "y"},
    }


def _flow_fixture() -> dict:
    return {
        "fullName": "AccountUpdateFlow",
        "label": "Account Update Flow",
        "processType": "AutoLaunchedFlow",
        "decisions": [
            {"name": "ZetaDecision", "label": "Z"},
            {"name": "AlphaDecision", "label": "A"},
        ],
        "assignments": [
            {"name": "BetaAssignment"},
            {"name": "AlphaAssignment"},
        ],
        "recordCreates": [
            {"name": "CreateLog"},
        ],
        "urls": {"foo": "bar"},
    }


_FIXTURES: dict[str, callable] = {
    "Object":           _object_fixture,
    "Field":            _field_fixture,
    "RecordType":       _record_type_fixture,
    "Layout":           _layout_fixture,
    "PicklistValueSet": _picklist_value_set_fixture,
    "PicklistValue":    _picklist_value_fixture,
    "Profile":          _profile_fixture,
    "PermissionSet":    _permission_set_fixture,
    "User":             _user_fixture,
    "ValidationRule":   _validation_rule_fixture,
    "Flow":             _flow_fixture,
}

ALL_TYPES = sorted(_FIXTURES.keys())


# ----------------------------------------------------------------------
# Test 1: Stability — same input twice -> same hash
# ----------------------------------------------------------------------

@pytest.mark.parametrize("entity_type", ALL_TYPES)
def test_normalize_is_stable(entity_type: str) -> None:
    raw_a = _FIXTURES[entity_type]()
    raw_b = _FIXTURES[entity_type]()
    h1 = hash_normalized(normalize(entity_type, raw_a))
    h2 = hash_normalized(normalize(entity_type, raw_b))
    assert h1 == h2, f"{entity_type}: stability broken: {h1} != {h2}"


# ----------------------------------------------------------------------
# Test 2: Phantom-change resistance — volatile additions don't change hash
# ----------------------------------------------------------------------

@pytest.mark.parametrize("entity_type", ALL_TYPES)
def test_normalize_strips_volatile_keys(entity_type: str) -> None:
    """Adding any of the universal volatile keys (urls, attributes,
    LastModifiedDate, etc.) must not change the hash."""
    raw_clean = _FIXTURES[entity_type]()
    h_clean = hash_normalized(normalize(entity_type, raw_clean))

    raw_dirty = _FIXTURES[entity_type]()
    raw_dirty["urls"] = {"random": "endpoint", "another": "path"}
    raw_dirty["attributes"] = {"type": "X", "url": "/y"}
    raw_dirty["LastModifiedDate"] = "2099-12-31T23:59:59Z"
    raw_dirty["CreatedDate"] = "2020-01-01T00:00:00Z"
    raw_dirty["SystemModstamp"] = "2026-04-30T12:00:00Z"
    raw_dirty["IsDeleted"] = False
    h_dirty = hash_normalized(normalize(entity_type, raw_dirty))

    assert h_clean == h_dirty, (
        f"{entity_type}: volatile keys leaked into hash. clean={h_clean}, dirty={h_dirty}"
    )


def test_normalize_resists_list_reordering_for_object() -> None:
    """Lists-of-dicts in Object reorder freely without changing hash."""
    raw_a = _object_fixture()
    raw_b = _object_fixture()
    # Reverse all the per-type sortable lists in B
    raw_b["fields"].reverse()
    raw_b["recordTypeInfos"].reverse()
    raw_b["childRelationships"].reverse()
    h_a = hash_normalized(normalize("Object", raw_a))
    h_b = hash_normalized(normalize("Object", raw_b))
    assert h_a == h_b


def test_normalize_resists_list_reordering_for_flow() -> None:
    """Flow's decisions/assignments/recordCreates reorder freely."""
    raw_a = _flow_fixture()
    raw_b = _flow_fixture()
    raw_b["decisions"].reverse()
    raw_b["assignments"].reverse()
    h_a = hash_normalized(normalize("Flow", raw_a))
    h_b = hash_normalized(normalize("Flow", raw_b))
    assert h_a == h_b


def test_normalize_resists_list_reordering_for_profile() -> None:
    raw_a = _profile_fixture()
    raw_b = _profile_fixture()
    raw_b["objectPermissions"].reverse()
    raw_b["fieldPermissions"].reverse()
    raw_b["userPermissions"].reverse()
    h_a = hash_normalized(normalize("Profile", raw_a))
    h_b = hash_normalized(normalize("Profile", raw_b))
    assert h_a == h_b


def test_normalize_resists_dict_key_order() -> None:
    """OrderedDict with different insertion order shouldn't matter
    because hash_normalized uses sort_keys=True."""
    raw_a = OrderedDict([("name", "Account"), ("label", "Account"), ("custom", False)])
    raw_b = OrderedDict([("custom", False), ("label", "Account"), ("name", "Account")])
    h_a = hash_normalized(normalize("Object", dict(raw_a)))
    h_b = hash_normalized(normalize("Object", dict(raw_b)))
    assert h_a == h_b


# ----------------------------------------------------------------------
# Test 3: Real change detection — semantic edit changes hash
# ----------------------------------------------------------------------

@pytest.mark.parametrize("entity_type", ALL_TYPES)
def test_normalize_detects_top_level_value_change(entity_type: str) -> None:
    """Modifying a top-level scalar (or adding a new semantic key)
    MUST change the hash."""
    raw_a = _FIXTURES[entity_type]()
    h_a = hash_normalized(normalize(entity_type, raw_a))

    raw_b = _FIXTURES[entity_type]()
    raw_b["__test_semantic_marker__"] = "new_value"
    h_b = hash_normalized(normalize(entity_type, raw_b))

    assert h_a != h_b, (
        f"{entity_type}: adding a semantic key didn't change hash. "
        f"a={h_a}, b={h_b}"
    )


def test_object_field_rename_detected() -> None:
    raw_a = _object_fixture()
    h_a = hash_normalized(normalize("Object", raw_a))
    raw_b = _object_fixture()
    raw_b["fields"][0]["name"] = "RenamedIndustry"
    h_b = hash_normalized(normalize("Object", raw_b))
    assert h_a != h_b


def test_field_picklist_value_addition_detected() -> None:
    raw_a = _field_fixture()
    h_a = hash_normalized(normalize("Field", raw_a))
    raw_b = _field_fixture()
    raw_b["picklistValues"].append(
        {"value": "NewIndustry", "label": "New", "active": True}
    )
    h_b = hash_normalized(normalize("Field", raw_b))
    assert h_a != h_b


def test_validation_rule_formula_change_detected() -> None:
    raw_a = _validation_rule_fixture()
    h_a = hash_normalized(normalize("ValidationRule", raw_a))
    raw_b = _validation_rule_fixture()
    raw_b["errorConditionFormula"] = "Amount > 1000000"
    h_b = hash_normalized(normalize("ValidationRule", raw_b))
    assert h_a != h_b


# ----------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------

class TestDispatcher:
    def test_unknown_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            normalize("NotARealType", {})
        assert "Unknown entity_type" in str(exc_info.value)
        assert "NotARealType" in str(exc_info.value)

    def test_known_types_count(self) -> None:
        """12 normalizers registered — Object, Field, RecordType, Layout,
        PicklistValueSet, PicklistValue, Profile, PermissionSet, User,
        ValidationRule, Flow, ApprovalProcess (D-308)."""
        assert len(_NORMALIZERS) == 12

    @pytest.mark.parametrize("entity_type", ALL_TYPES)
    def test_dispatches_each_known_type(self, entity_type: str) -> None:
        """Each registered type can be dispatched without ValueError."""
        result = normalize(entity_type, _FIXTURES[entity_type]())
        assert isinstance(result, dict)


# ----------------------------------------------------------------------
# Hash function
# ----------------------------------------------------------------------

class TestHashFunction:
    def test_hash_is_deterministic(self) -> None:
        d = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
        h1 = hash_normalized(d)
        h2 = hash_normalized(d)
        assert h1 == h2

    def test_hash_is_64_char_hex(self) -> None:
        d = {"x": 1}
        h = hash_normalized(d)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_handles_non_string_keys_gracefully(self) -> None:
        """Datetime values (or any object) come through via default=str."""
        from datetime import datetime
        d = {"ts": datetime(2026, 4, 30, 10, 0, 0)}
        h = hash_normalized(d)
        assert len(h) == 64

    def test_hash_stable_across_dict_key_order(self) -> None:
        """Inserting keys in different orders produces same hash because
        json.dumps sort_keys=True."""
        d_a = {}
        d_a["a"] = 1; d_a["b"] = 2; d_a["c"] = 3
        d_b = {}
        d_b["c"] = 3; d_b["a"] = 1; d_b["b"] = 2
        assert hash_normalized(d_a) == hash_normalized(d_b)

    def test_hash_distinguishes_different_inputs(self) -> None:
        h1 = hash_normalized({"x": 1})
        h2 = hash_normalized({"x": 2})
        assert h1 != h2


# ----------------------------------------------------------------------
# Whitespace-only string normalization
# ----------------------------------------------------------------------

class TestWhitespaceNormalization:
    def test_whitespace_only_top_level_becomes_none(self) -> None:
        raw = {"name": "Account", "description": "   "}
        n = normalize("Object", raw)
        assert n["description"] is None

    def test_empty_string_becomes_none(self) -> None:
        raw = {"name": "Account", "description": ""}
        n = normalize("Object", raw)
        assert n["description"] is None

    def test_real_string_preserved(self) -> None:
        raw = {"name": "Account", "description": "real content"}
        n = normalize("Object", raw)
        assert n["description"] == "real content"

    def test_whitespace_in_nested_dict_normalized(self) -> None:
        raw = {
            "name": "Account",
            "fields": [{"name": "Industry", "label": "  "}],
        }
        n = normalize("Object", raw)
        assert n["fields"][0]["label"] is None

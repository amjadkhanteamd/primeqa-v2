"""D-403 inline-always picklist capture — the shape adapter + the cap.

Replaces tests/test_standard_value_set_match.py, which covered the retired
D-118 StandardValueSet content-match.
"""
from __future__ import annotations

from primeqa.sync.picklist_capture import (
    MAX_INLINE_PICKLIST_VALUES,
    describe_values_as_metadata,
)


def test_describe_values_translate_to_metadata_dialect():
    """Describe's {value,label,active,defaultValue} → the Metadata dialect
    {valueName,label,isActive,default} the PicklistValue mapper reads."""
    out = describe_values_as_metadata({"picklistValues": [
        {"value": "High", "label": "High priority",
         "active": True, "defaultValue": False},
    ]})
    assert out == {"value": [
        {"valueName": "High", "label": "High priority",
         "isActive": True, "default": False},
    ]}


def test_null_active_counts_as_active():
    """D-204.1: Salesforce sends active: null for never-deactivated values.
    `is not False`, never `.get(k, True)` — the .get default fires only on a
    MISSING key, so null would wrongly read as inactive."""
    out = describe_values_as_metadata({"picklistValues": [
        {"value": "A", "active": None},
        {"value": "B"},
    ]})
    assert [v["isActive"] for v in out["value"]] == [True, True]


def test_inactive_values_are_kept_not_dropped():
    """Retired values must survive capture: the audit needs them to answer
    'INACTIVE (org drift)' rather than 'ABSENT (hallucination)'. This is the
    deliberate departure from D-118, which compared active-vs-active."""
    out = describe_values_as_metadata({"picklistValues": [
        {"value": "Live", "active": True},
        {"value": "Retired", "active": False},
    ]})
    assert [(v["valueName"], v["isActive"]) for v in out["value"]] == [
        ("Live", True), ("Retired", False),
    ]


def test_label_falls_back_to_value_and_junk_is_skipped():
    out = describe_values_as_metadata({"picklistValues": [
        {"value": "X"},                 # no label
        {"value": "Y", "label": ""},    # empty label
        {"label": "no value key"},      # skipped
        "not a dict",                   # skipped
        {"value": "", "label": "Z"},    # falsy value → skipped
    ]})
    assert [(v["valueName"], v["label"]) for v in out["value"]] == [
        ("X", "X"), ("Y", "Y"),
    ]


def test_missing_and_empty_picklist_values():
    assert describe_values_as_metadata({}) == {"value": []}
    assert describe_values_as_metadata({"picklistValues": None}) == {"value": []}
    assert describe_values_as_metadata({"picklistValues": []}) == {"value": []}


def test_default_flag_is_coerced_to_bool():
    out = describe_values_as_metadata({"picklistValues": [
        {"value": "A", "defaultValue": True},
        {"value": "B", "defaultValue": None},
        {"value": "C"},
    ]})
    assert [v["default"] for v in out["value"]] == [True, False, False]


def test_cap_is_generous_enough_for_business_vocabularies():
    """The cap exists to stop platform enumerations (timezones, locales,
    sObject names, page keys), not business picklists. On env-59 the largest
    picklist on any claim-bearing object other than a timezone list is 12
    values; the biggest genuine business vocabulary is Industry at 32."""
    assert MAX_INLINE_PICKLIST_VALUES >= 32

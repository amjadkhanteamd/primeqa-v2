"""Unit: standard picklist field → StandardValueSet content-match (D-118).

Pure functions, no DB. Covers the value extractors (field describe `value` +
active; SVS metadata `valueName` + isActive), the index build, and the matcher
— especially the exact-set-equality policy (subset/superset are NOT matches)
and fail-closed behavior (empty / 0 / ambiguous → None).
"""
from __future__ import annotations

from primeqa.sync.standard_value_set_match import (
    build_svs_index,
    field_active_value_names,
    match_standard_value_set,
)


def _field(values_active):  # [(value, active), ...] -> describe-shaped field
    return {"picklistValues": [
        {"value": v, "label": v, "active": a} for v, a in values_active
    ]}


def _svs(values_active):  # [(valueName, isActive), ...] -> SVS Metadata sub-tree
    return {"standardValue": [
        {"valueName": v, "label": v, "isActive": a} for v, a in values_active
    ]}


# --- field value extraction ---

def test_field_active_value_names_active_only():
    f = _field([("Hot", True), ("Cold", True), ("Stale", False)])
    assert field_active_value_names(f) == {"Hot", "Cold"}


def test_field_active_value_names_empty_and_missing():
    assert field_active_value_names({}) == set()
    # entry missing 'value' is skipped
    assert field_active_value_names({"picklistValues": [{"label": "x"}]}) == set()


# --- index build (mirrors how phase_field reads ctx.svs_metadata_cache) ---

def test_build_svs_index_keys_by_active_value_set():
    cache = {
        "Industry": _svs([("Banking", True), ("Retail", True), ("Dead", False)]),
        "LeadSource": _svs([("Web", True), ("Phone", True)]),
    }
    index = build_svs_index(cache)
    assert index[frozenset({"Banking", "Retail"})] == ["Industry"]   # inactive dropped
    assert index[frozenset({"Web", "Phone"})] == ["LeadSource"]


def test_build_svs_index_skips_empty():
    assert build_svs_index({"Empty": _svs([])}) == {}
    assert build_svs_index({}) == {}
    assert build_svs_index(None) == {}


def test_build_svs_index_groups_identical_value_sets():
    cache = {"A": _svs([("X", True), ("Y", True)]),
             "B": _svs([("X", True), ("Y", True)])}
    assert sorted(build_svs_index(cache)[frozenset({"X", "Y"})]) == ["A", "B"]


# --- the matcher: exact set-equality, fail-closed (the D-118 policy) ---

def test_match_exact_equality_links():
    index = build_svs_index({"Industry": _svs([("Banking", True), ("Retail", True)])})
    assert match_standard_value_set({"Banking", "Retail"}, index) == "Industry"


def test_match_zero_returns_none():
    index = build_svs_index({"Industry": _svs([("Banking", True)])})
    assert match_standard_value_set({"Web", "Phone"}, index) is None


def test_match_ambiguous_returns_none():
    # two SVSes with identical value sets -> fail-closed, refuse to guess
    index = build_svs_index({"A": _svs([("X", True)]), "B": _svs([("X", True)])})
    assert match_standard_value_set({"X"}, index) is None


def test_match_empty_field_returns_none():
    index = build_svs_index({"Industry": _svs([("Banking", True)])})
    assert match_standard_value_set(set(), index) is None


def test_match_subset_is_not_a_match():
    # field's values are a SUBSET of the SVS -> no link (exact, not subset)
    index = build_svs_index({"Industry": _svs(
        [("Banking", True), ("Retail", True), ("Tech", True)])})
    assert match_standard_value_set({"Banking", "Retail"}, index) is None


def test_match_superset_is_not_a_match():
    index = build_svs_index({"Industry": _svs([("Banking", True)])})
    assert match_standard_value_set({"Banking", "Retail"}, index) is None


def test_match_end_to_end_shapes():
    # field describe shape -> field set; SVS metadata shape -> index; they meet.
    field = _field([("Banking", True), ("Retail", True), ("Old", False)])
    cache = {"Industry": _svs([("Banking", True), ("Retail", True), ("Old", False)]),
             "LeadSource": _svs([("Web", True), ("Phone", True)])}
    index = build_svs_index(cache)
    assert match_standard_value_set(field_active_value_names(field), index) == "Industry"

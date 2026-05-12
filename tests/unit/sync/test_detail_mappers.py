"""Tests for primeqa.sync.detail_mappers — per-type detail-table
column mappers.

The mapper module is a parallel registry alongside _PRESENTATION_
FUNCTIONS, _extract_external_id, PHASE_REGISTRY. Tests lock the
per-type mapping shapes and verify the get_detail_mapper dispatch
behavior.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from primeqa.sync.detail_mappers import (
    _map_object_details,
    _map_picklist_value_details,
    get_detail_mapper,
)


class TestMapObjectDetails:
    def test_map_object_details_includes_entity_id(self) -> None:
        """entity_id is always present in the returned row."""
        row = _map_object_details(
            normalized={"name": "Account", "custom": False},
            entity_id="aaaaaaaa-1111-1111-1111-111111111111",
            parent_resolver=lambda **_: None,  # not used by Object
        )
        assert row["entity_id"] == "aaaaaaaa-1111-1111-1111-111111111111"

    def test_map_object_details_passes_key_prefix_through(self) -> None:
        """key_prefix is nullable; passed through verbatim (including
        None)."""
        row_with = _map_object_details(
            normalized={"keyPrefix": "001", "custom": False},
            entity_id="x", parent_resolver=lambda **_: None,
        )
        assert row_with["key_prefix"] == "001"

        row_without = _map_object_details(
            normalized={"custom": False},  # keyPrefix absent
            entity_id="x", parent_resolver=lambda **_: None,
        )
        assert row_without["key_prefix"] is None

    def test_map_object_details_maps_boolean_flags(self) -> None:
        """Salesforce describe flags map to is_* columns when present."""
        row = _map_object_details(
            normalized={
                "name": "Account",
                "custom": False,
                "queryable": True,
                "createable": True,
                "updateable": True,
                "deletable": False,
            },
            entity_id="x", parent_resolver=lambda **_: None,
        )
        assert row["is_custom"] is False
        assert row["is_queryable"] is True
        assert row["is_createable"] is True
        assert row["is_updateable"] is True
        assert row["is_deletable"] is False

    def test_map_object_details_omits_missing_flags(self) -> None:
        """If a flag is missing from normalized, mapper omits the
        column so DB DEFAULT wins. This keeps callers honest about
        which fields are actually in their payload, vs. relying on
        Python-side defaults."""
        row = _map_object_details(
            normalized={"name": "Account"},  # no flags at all
            entity_id="x", parent_resolver=lambda **_: None,
        )
        for absent_col in (
            "is_custom", "is_queryable", "is_createable",
            "is_updateable", "is_deletable",
        ):
            assert absent_col not in row, (
                f"Column {absent_col!r} should be omitted (let DB "
                f"default win); got value {row.get(absent_col)!r}"
            )


class TestMapPicklistValueDetails:
    def _ok_normalized(self, **overrides) -> dict:
        base = {
            "valueName": "Analyst",
            "label": "Analyst",
            "default": False,
            "isActive": True,
            "_parent_external_id": "SVS:AccountType",
            "_sort_order": 3,
        }
        base.update(overrides)
        return base

    def test_map_picklist_value_details_resolves_parent(self) -> None:
        """Mapper calls parent_resolver with entity_type=
        'PicklistValueSet' and the _parent_external_id marker.
        Result is wired to picklist_value_set_entity_id column."""
        resolver = MagicMock(return_value="parent-uuid-001")
        row = _map_picklist_value_details(
            normalized=self._ok_normalized(),
            entity_id="child-uuid-100",
            parent_resolver=resolver,
        )
        resolver.assert_called_once_with(
            entity_type="PicklistValueSet",
            external_id="SVS:AccountType",
        )
        assert row["entity_id"] == "child-uuid-100"
        assert row["picklist_value_set_entity_id"] == "parent-uuid-001"
        assert row["value_label"] == "Analyst"
        assert row["value_api_name"] == "Analyst"
        assert row["is_active"] is True
        assert row["is_default"] is False
        assert row["sort_order"] == 3

    def test_map_picklist_value_details_treats_none_isactive_as_true(
        self,
    ) -> None:
        """Salesforce returns isActive=None on standard value records
        when the flag isn't explicitly set; the semantic default is
        active. None must map to True, NOT False."""
        row = _map_picklist_value_details(
            normalized=self._ok_normalized(isActive=None),
            entity_id="x",
            parent_resolver=lambda **_: "parent",
        )
        assert row["is_active"] is True

    def test_map_picklist_value_details_treats_false_isactive_as_false(
        self,
    ) -> None:
        """Explicit False must remain False — None-as-True coercion
        must NOT swallow the genuine inactive case."""
        row = _map_picklist_value_details(
            normalized=self._ok_normalized(isActive=False),
            entity_id="x",
            parent_resolver=lambda **_: "parent",
        )
        assert row["is_active"] is False

    def test_map_picklist_value_details_falls_back_to_value_name_for_label(
        self,
    ) -> None:
        """Some SVSes return label=None on placeholder entries. The
        mapper substitutes valueName (NOT NULL column requires
        something)."""
        row = _map_picklist_value_details(
            normalized=self._ok_normalized(
                valueName="ColdLead", label=None,
            ),
            entity_id="x",
            parent_resolver=lambda **_: "parent",
        )
        assert row["value_label"] == "ColdLead"
        assert row["value_api_name"] == "ColdLead"

    def test_map_picklist_value_details_defaults_sort_order_to_zero(
        self,
    ) -> None:
        """When _sort_order isn't injected (legacy callers), default
        to 0 so the NOT NULL column is satisfied."""
        normalized = self._ok_normalized()
        normalized.pop("_sort_order")
        row = _map_picklist_value_details(
            normalized=normalized,
            entity_id="x",
            parent_resolver=lambda **_: "parent",
        )
        assert row["sort_order"] == 0

    def test_map_picklist_value_details_raises_on_missing_parent_marker(
        self,
    ) -> None:
        """If phase function forgot to inject _parent_external_id,
        fail loudly with a message naming the missing marker."""
        normalized = self._ok_normalized()
        normalized.pop("_parent_external_id")
        with pytest.raises(ValueError) as excinfo:
            _map_picklist_value_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: "parent",
            )
        assert "_parent_external_id" in str(excinfo.value)

    def test_map_picklist_value_details_raises_on_unresolvable_parent(
        self,
    ) -> None:
        """If parent_resolver returns None (parent not materialized),
        fail with a message identifying the unresolved parent."""
        with pytest.raises(ValueError) as excinfo:
            _map_picklist_value_details(
                normalized=self._ok_normalized(
                    _parent_external_id="SVS:NonexistentVS",
                ),
                entity_id="x",
                parent_resolver=lambda **_: None,
            )
        msg = str(excinfo.value)
        assert "SVS:NonexistentVS" in msg
        assert "PicklistValueSet" in msg
        assert "ENTITY_ORDER" in msg

    def test_map_picklist_value_details_raises_on_missing_value_name(
        self,
    ) -> None:
        """valueName is the per-value identifier; empty/missing is a
        data integrity issue. Fail rather than silently inserting a
        malformed external_id."""
        with pytest.raises(ValueError) as excinfo:
            _map_picklist_value_details(
                normalized=self._ok_normalized(valueName=""),
                entity_id="x",
                parent_resolver=lambda **_: "parent",
            )
        assert "valueName" in str(excinfo.value)


class TestGetDetailMapper:
    def test_get_detail_mapper_returns_tuple_for_object(self) -> None:
        """Object is registered — returns (table_name, mapper_fn)."""
        result = get_detail_mapper("Object")
        assert result is not None
        table_name, mapper = result
        assert table_name == "object_details"
        assert mapper is _map_object_details

    def test_get_detail_mapper_returns_tuple_for_picklist_value(
        self,
    ) -> None:
        """PicklistValue is registered — returns ('picklist_value_
        details', mapper_fn)."""
        result = get_detail_mapper("PicklistValue")
        assert result is not None
        table_name, mapper = result
        assert table_name == "picklist_value_details"
        assert mapper is _map_picklist_value_details

    def test_get_detail_mapper_returns_none_for_picklist_value_set(
        self,
    ) -> None:
        """PicklistValueSet has no detail table per substrate-1
        design (TIER_1_ENTITIES omits it). get_detail_mapper must
        return None so materialize layer skips the detail-write step."""
        assert get_detail_mapper("PicklistValueSet") is None

    def test_get_detail_mapper_returns_none_for_unknown_type(
        self,
    ) -> None:
        """Unknown entity_type returns None (not raises), letting the
        materialize layer treat the absence of a mapper as 'skip
        detail-row writes'. KeyError on unknown entity types is the
        caller's concern — materialize uses get_detail_mapper as a
        feature-flag, not a lookup."""
        assert get_detail_mapper("NotARealEntity") is None

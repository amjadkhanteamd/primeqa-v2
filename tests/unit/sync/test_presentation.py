"""Tests for primeqa.sync.presentation — normalize → presentation
shape adapters per PHASE_2_PLAN_corrections.md §7."""
from __future__ import annotations

import pytest

from primeqa.sync.presentation import (
    _to_presentation_object,
    _to_presentation_picklist_value_set,
    to_presentation,
)


class TestToPresentationObject:
    def test_to_presentation_object_maps_camel_to_snake(self) -> None:
        """Raw Salesforce describe uses camelCase ('custom');
        semantic_text expects snake_case ('is_custom'). Adapter
        bridges the two schemas."""
        normalized = {
            "name": "Account",
            "label": "Account",
            "custom": False,
            "keyPrefix": "001",
        }
        presentation = _to_presentation_object(normalized)
        assert presentation["name"] == "Account"
        assert presentation["label"] == "Account"
        assert presentation["is_custom"] is False
        # camelCase keys NOT carried through (they're consumed by
        # the adapter, not passed on)
        assert "custom" not in presentation
        assert "keyPrefix" not in presentation

    def test_to_presentation_object_handles_missing_description(
        self,
    ) -> None:
        """fetch_objects() bulk responses typically don't carry a
        description field. The adapter passes None through; the
        downstream _to_text_object handles None with a default
        ("no description provided")."""
        normalized = {
            "name": "Account",
            "label": "Account",
            "custom": False,
            # description deliberately absent
        }
        presentation = _to_presentation_object(normalized)
        assert "description" in presentation
        assert presentation["description"] is None
        # key_field_names is also None at the Object phase (would
        # require Field phase data to populate)
        assert "key_field_names" in presentation
        assert presentation["key_field_names"] is None

    def test_to_presentation_object_custom_object_marked_as_custom(
        self,
    ) -> None:
        """A custom sObject (custom=True) maps to is_custom=True."""
        normalized = {
            "name": "MyCustom__c",
            "label": "My Custom",
            "custom": True,
        }
        presentation = _to_presentation_object(normalized)
        assert presentation["is_custom"] is True


class TestToPresentationRouter:
    def test_to_presentation_unknown_entity_type_raises_keyerror(
        self,
    ) -> None:
        """The router rejects entity types without a registered
        adapter, with a message pointing at the registry that
        needs extending."""
        with pytest.raises(KeyError) as excinfo:
            to_presentation("NotAnEntity", {"name": "X"})
        msg = str(excinfo.value)
        assert "NotAnEntity" in msg
        assert "_PRESENTATION_FUNCTIONS" in msg


class TestToPresentationPicklistValueSet:
    def test_to_presentation_picklist_value_set_maps_pascal_to_snake(
        self,
    ) -> None:
        """fetch_global_value_sets returns Tooling-API PascalCase
        (FullName, MasterLabel, Description); the semantic_text
        template expects snake_case (name, label, description).
        Adapter bridges the schemas."""
        normalized = {
            "Id": "0Nt000000000001",
            "FullName": "MyValueSet",
            "MasterLabel": "My Value Set",
            "Description": "A reusable value set",
            "ManageableState": "unmanaged",
            "NamespacePrefix": None,
        }
        presentation = _to_presentation_picklist_value_set(normalized)
        assert presentation["name"] == "MyValueSet"
        assert presentation["label"] == "My Value Set"
        assert presentation["description"] == "A reusable value set"
        # PascalCase keys NOT carried through
        assert "FullName" not in presentation
        assert "MasterLabel" not in presentation
        assert "Description" not in presentation

    def test_to_presentation_picklist_value_set_hardcodes_is_restricted(
        self,
    ) -> None:
        """is_restricted is hardcoded True for GlobalValueSets per
        the adapter's design decision (GVSes are reusable canonical
        enumerations — restricted by intent). Same for
        is_global_value_set since this adapter handles only the
        GVS source.

        This test locks the hardcoding so a future PR can't silently
        flip the semantic_text-output meaning."""
        # Try variants that might tempt a reader to make
        # is_restricted dependent on input — adapter should ignore
        # them and emit True.
        for input_variant in [
            {"FullName": "X", "MasterLabel": "X"},
            {"FullName": "X", "isRestricted": False},  # bogus key
            {"FullName": "X", "is_restricted": False},  # bogus key
        ]:
            presentation = _to_presentation_picklist_value_set(input_variant)
            assert presentation["is_restricted"] is True
            assert presentation["is_global_value_set"] is True

    def test_to_presentation_picklist_value_set_handles_missing_description(
        self,
    ) -> None:
        """Description is None when fetch_global_value_sets returns
        a GVS without a Description. The adapter passes None through;
        downstream _to_text_picklist_value_set substitutes
        "no description provided" via _str_or_default_clean."""
        normalized = {
            "FullName": "MyVS",
            "MasterLabel": "My VS",
            # Description deliberately absent
        }
        presentation = _to_presentation_picklist_value_set(normalized)
        assert presentation["description"] is None

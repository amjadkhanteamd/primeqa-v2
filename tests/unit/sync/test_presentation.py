"""Tests for primeqa.sync.presentation — normalize → presentation
shape adapters per PHASE_2_PLAN_corrections.md §7."""
from __future__ import annotations

import pytest

from primeqa.sync.presentation import (
    _to_presentation_field,
    _to_presentation_object,
    _to_presentation_picklist_value,
    _to_presentation_picklist_value_set,
    _to_presentation_record_type,
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

    def test_to_presentation_picklist_value_set_defaults_to_gvs_when_source_missing(
        self,
    ) -> None:
        """Backward compatibility: raw payloads without `_source`
        (e.g., fixtures pre-dating the SVS cycle) default to the GVS
        branch — is_global_value_set=True."""
        normalized = {
            "FullName": "RegionVS",
            "MasterLabel": "Region",
            "Description": "Sales regions",
            # _source deliberately absent
        }
        presentation = _to_presentation_picklist_value_set(normalized)
        assert presentation["is_global_value_set"] is True
        assert presentation["description"] == "Sales regions"

    def test_to_presentation_picklist_value_set_gvs_source_explicit(
        self,
    ) -> None:
        """`_source='GlobalValueSet'` explicit selects GVS branch:
        is_global_value_set=True, description from payload."""
        normalized = {
            "_source": "GlobalValueSet",
            "FullName": "RegionVS",
            "MasterLabel": "Region",
            "Description": "Sales regions",
        }
        presentation = _to_presentation_picklist_value_set(normalized)
        assert presentation["name"] == "RegionVS"
        assert presentation["label"] == "Region"
        assert presentation["is_restricted"] is True
        assert presentation["is_global_value_set"] is True
        assert presentation["description"] == "Sales regions"

    def test_to_presentation_picklist_value_set_svs_source(self) -> None:
        """`_source='StandardValueSet'` selects SVS branch:
        is_global_value_set=False, description hardcoded None
        (SVS Tooling response has no Description column)."""
        normalized = {
            "_source": "StandardValueSet",
            "FullName": "AccountSource",
            "MasterLabel": "Account Source",
            # Description deliberately absent (matches SVS Tooling
            # response shape).
        }
        presentation = _to_presentation_picklist_value_set(normalized)
        assert presentation["name"] == "AccountSource"
        assert presentation["label"] == "Account Source"
        assert presentation["is_restricted"] is True
        assert presentation["is_global_value_set"] is False
        assert presentation["description"] is None

    def test_to_presentation_picklist_value_set_svs_ignores_description_in_payload(
        self,
    ) -> None:
        """SVS branch hardcodes description=None even if a Description
        key is present (it's not in the documented SVS Tooling
        response; this guards against accidental leak-through if a
        future fetcher synthesizes one)."""
        normalized = {
            "_source": "StandardValueSet",
            "FullName": "CaseOrigin",
            "MasterLabel": "Case Origin",
            "Description": "leak-through guard",
        }
        presentation = _to_presentation_picklist_value_set(normalized)
        assert presentation["description"] is None


class TestToPresentationPicklistValue:
    def test_to_presentation_picklist_value_maps_live_shape(self) -> None:
        """Live Salesforce shape (verified against AccountType etc.):
        {valueName, label, default, isActive, description, ...}.
        Adapter bridges to semantic_text input {value, set_name,
        is_active, is_default}."""
        normalized = {
            "valueName": "Analyst",
            "label": "Analyst",
            "default": False,
            "isActive": True,
            "_parent_external_id": "SVS:AccountType",
            "_sort_order": 0,
        }
        presentation = _to_presentation_picklist_value(normalized)
        assert presentation["value"] == "Analyst"
        assert presentation["set_name"] == "SVS:AccountType"
        assert presentation["is_active"] is True
        assert presentation["is_default"] is False

    def test_to_presentation_picklist_value_treats_none_isactive_as_true(
        self,
    ) -> None:
        """Salesforce returns isActive=None on standard values when
        the flag isn't explicitly set; semantic default is active.
        None → True (matches the detail-table mapper's convention)."""
        normalized = {
            "valueName": "Banking",
            "label": "Banking",
            "isActive": None,  # the live SVS shape
            "default": False,
            "_parent_external_id": "SVS:Industry",
        }
        presentation = _to_presentation_picklist_value(normalized)
        assert presentation["is_active"] is True

    def test_to_presentation_picklist_value_explicit_false_isactive(
        self,
    ) -> None:
        """Explicit False stays False — None-as-True coercion must
        not swallow the genuine inactive case."""
        normalized = {
            "valueName": "DeprecatedValue",
            "label": "Deprecated Value",
            "isActive": False,
            "default": False,
            "_parent_external_id": "MyGVS",
        }
        presentation = _to_presentation_picklist_value(normalized)
        assert presentation["is_active"] is False

    def test_to_presentation_picklist_value_set_name_uses_parent_marker(
        self,
    ) -> None:
        """set_name in semantic_text input comes from the phase-
        injected _parent_external_id marker — including the 'SVS:'
        prefix for StandardValueSet sources, so the rendered text
        reflects the namespaced identifier."""
        gvs_normalized = {
            "valueName": "Banking",
            "_parent_external_id": "MyCustomGVS",
        }
        gvs_pres = _to_presentation_picklist_value(gvs_normalized)
        assert gvs_pres["set_name"] == "MyCustomGVS"

        svs_normalized = {
            "valueName": "Analyst",
            "_parent_external_id": "SVS:AccountType",
        }
        svs_pres = _to_presentation_picklist_value(svs_normalized)
        assert svs_pres["set_name"] == "SVS:AccountType"


class TestToPresentationField:
    def test_to_presentation_field_maps_live_shape(self) -> None:
        """REST describe shape (camelCase, ~56 keys) → semantic_text
        input shape (snake_case, 9 keys) per substrate-1's
        _to_text_field expectations."""
        normalized = {
            "name": "Industry",
            "type": "picklist",
            "label": "Industry",
            "inlineHelpText": "Customer industry classification.",
            "custom": False,
            "nillable": True,
            "referenceTo": [],
            "_parent_object_api_name": "Account",
        }
        presentation = _to_presentation_field(normalized)
        assert presentation["name"] == "Industry"
        assert presentation["object_name"] == "Account"
        assert presentation["type"] == "picklist"
        assert presentation["label"] == "Industry"
        assert presentation["help_text"] == (
            "Customer industry classification."
        )
        assert presentation["is_custom"] is False
        # is_required = NOT nillable → False (nillable=True)
        assert presentation["is_required"] is False
        assert presentation["reference_to"] is None
        # picklist_value_set_name always None this cycle per §10
        assert presentation["picklist_value_set_name"] is None

    def test_to_presentation_field_required_when_not_nillable(
        self,
    ) -> None:
        """Substrate-1's is_required derives from NOT nillable. Verify
        the inversion: nillable=False → is_required=True."""
        normalized = {
            "name": "Name",
            "type": "string",
            "nillable": False,
            "_parent_object_api_name": "Account",
        }
        presentation = _to_presentation_field(normalized)
        assert presentation["is_required"] is True

    def test_to_presentation_field_takes_first_reference_to(self) -> None:
        """Polymorphic referenceTo (Task.WhoId → Contact OR Lead)
        loses secondary targets in the rendered text. Detail column
        also takes first only; HAS_RELATIONSHIP_TO edges carry the
        full graph."""
        normalized = {
            "name": "WhoId",
            "type": "reference",
            "referenceTo": ["Contact", "Lead"],
            "_parent_object_api_name": "Task",
        }
        presentation = _to_presentation_field(normalized)
        assert presentation["reference_to"] == "Contact"

    def test_to_presentation_field_handles_empty_reference_to(
        self,
    ) -> None:
        """Non-reference field with empty list → reference_to=None
        (substrate-1 fixture uses scalar None, not empty list)."""
        normalized = {
            "name": "Industry",
            "referenceTo": [],
            "_parent_object_api_name": "Account",
        }
        presentation = _to_presentation_field(normalized)
        assert presentation["reference_to"] is None


class TestToPresentationRecordType:
    def test_maps_developer_name_to_name(self) -> None:
        """Salesforce Tooling Metadata uses 'developerName' as the
        per-Object RT identifier; semantic_text input expects 'name'.
        The adapter bridges this rename."""
        normalized = {
            "developerName": "PartnerAccount",
            "label": "Partner Account",
            "active": True,
            "description": "Accounts that are channel partners.",
            "_parent_object_api_name": "Account",
        }
        presentation = _to_presentation_record_type(normalized)
        assert presentation["name"] == "PartnerAccount"
        assert presentation["label"] == "Partner Account"
        assert presentation["is_active"] is True
        assert presentation["description"] == (
            "Accounts that are channel partners."
        )

    def test_object_name_comes_from_parent_marker(self) -> None:
        """object_name in semantic_text input is sourced from the
        phase-injected _parent_object_api_name marker (parsed from
        FullName at the phase function); NOT from any field in the
        raw Tooling response."""
        normalized = {
            "developerName": "Trial",
            "_parent_object_api_name": "MyNS__License__c",
        }
        presentation = _to_presentation_record_type(normalized)
        assert presentation["object_name"] == "MyNS__License__c"

    def test_defaults_active_to_true(self) -> None:
        """Tooling Metadata.active is almost always present, but if
        missing the adapter defaults to True. Aligns with the
        is_master=False default — RTs are assumed live unless
        explicitly inactive."""
        normalized = {
            "developerName": "Default",
            "_parent_object_api_name": "Account",
        }
        presentation = _to_presentation_record_type(normalized)
        assert presentation["is_active"] is True
        # Description defaults to None (passes through to
        # _to_text_record_type which substitutes "no description
        # provided")
        assert presentation["description"] is None

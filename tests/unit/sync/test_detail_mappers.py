"""Tests for primeqa.sync.detail_mappers — per-type detail-table
column mappers.

The mapper module is a parallel registry alongside _PRESENTATION_
FUNCTIONS, _extract_external_id, PHASE_REGISTRY. Tests lock the
per-type mapping shapes and verify the get_detail_mapper dispatch
behavior.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from primeqa.sync.detail_mappers import (
    _map_field_details,
    _map_flow_details,
    _map_layout_details,
    _map_object_details,
    _map_permission_set_details,
    _map_picklist_value_details,
    _map_profile_details,
    _map_record_type_details,
    _map_user_details,
    _map_validation_rule_details,
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

    def test_get_detail_mapper_returns_tuple_for_field(self) -> None:
        """Field is registered — returns ('field_details', mapper)."""
        result = get_detail_mapper("Field")
        assert result is not None
        table_name, mapper = result
        assert table_name == "field_details"
        assert mapper is _map_field_details


class TestMapFieldDetails:
    def _normalized(self, **overrides) -> dict:
        base = {
            "name": "Industry",
            "type": "picklist",
            "label": "Industry",
            "custom": False,
            "nillable": True,
            "unique": False,
            "externalId": False,
            "calculated": False,
            "filterable": True,
            "sortable": True,
            "length": 40,
            "precision": 0,
            "scale": 0,
            "referenceTo": [],
            "_parent_object_api_name": "Account",
        }
        base.update(overrides)
        return base

    def test_map_field_details_resolves_parent_object(self) -> None:
        """The _parent_object_api_name marker drives the
        object_entity_id FK resolution."""
        resolver = MagicMock(return_value="obj-account-uuid")
        row = _map_field_details(
            normalized=self._normalized(),
            entity_id="fld-industry-uuid",
            parent_resolver=resolver,
        )
        # First call is the parent Object resolution
        assert resolver.call_args_list[0] == call(
            entity_type="Object", external_id="Account",
        )
        assert row["entity_id"] == "fld-industry-uuid"
        assert row["object_entity_id"] == "obj-account-uuid"
        assert row["field_type"] == "picklist"
        # No referenceTo → null FK column, picklist_value_set
        # always null this cycle
        assert row["references_object_entity_id"] is None
        assert row["picklist_value_set_entity_id"] is None
        # Hot booleans + INTs from normalized
        assert row["is_custom"] is False
        assert row["is_nillable"] is True
        assert row["is_filterable"] is True
        assert row["length"] == 40

    def test_map_field_details_resolves_reference_target(self) -> None:
        """A reference-typed field with referenceTo=['User'] resolves
        references_object_entity_id from the FIRST target. Polymorphic
        refs lose secondary targets at the detail-table level (full
        set is in HAS_RELATIONSHIP_TO edges)."""
        # Resolver returns different ids per (entity_type, external_id)
        def resolver(*, entity_type, external_id):
            return {
                ("Object", "Account"): "obj-account",
                ("Object", "User"): "obj-user",
            }[(entity_type, external_id)]
        row = _map_field_details(
            normalized=self._normalized(
                name="OwnerId", type="reference",
                referenceTo=["User"], nillable=False,
            ),
            entity_id="fld-owner-uuid",
            parent_resolver=resolver,
        )
        assert row["object_entity_id"] == "obj-account"
        assert row["references_object_entity_id"] == "obj-user"
        assert row["field_type"] == "reference"
        assert row["is_nillable"] is False

    def test_map_field_details_polymorphic_takes_first_target_only(
        self,
    ) -> None:
        """Polymorphic ref (Task.WhoId → Contact OR Lead): detail
        column gets only the first target. Edges layer captures the
        full multi-target relationship."""
        def resolver(*, entity_type, external_id):
            return {
                ("Object", "Task"): "obj-task",
                ("Object", "Contact"): "obj-contact",
                ("Object", "Lead"): "obj-lead",
            }[(entity_type, external_id)]
        row = _map_field_details(
            normalized=self._normalized(
                name="WhoId", type="reference",
                referenceTo=["Contact", "Lead"],
                _parent_object_api_name="Task",
            ),
            entity_id="fld-who",
            parent_resolver=resolver,
        )
        # references_object_entity_id takes referenceTo[0] (Contact)
        assert row["references_object_entity_id"] == "obj-contact"

    def test_map_field_details_unresolvable_ref_target_is_null(
        self,
    ) -> None:
        """If reference target Object isn't materialized (filtered
        by Object phase syncability filter), references_object_entity_id
        stays NULL. Column is nullable; this is the right semantic —
        keep the field's detail row, lose the dangling reference."""
        def resolver(*, entity_type, external_id):
            return {
                ("Object", "Account"): "obj-account",
                # Quote not in the synced set
            }.get((entity_type, external_id))
        row = _map_field_details(
            normalized=self._normalized(
                name="QuoteId", type="reference",
                referenceTo=["Quote"],
            ),
            entity_id="fld-quote",
            parent_resolver=resolver,
        )
        assert row["object_entity_id"] == "obj-account"
        assert row["references_object_entity_id"] is None

    def test_map_field_details_raises_on_missing_parent_marker(
        self,
    ) -> None:
        """Missing _parent_object_api_name marker → ValueError naming
        the missing marker. Phase function bug detection."""
        normalized = self._normalized()
        normalized.pop("_parent_object_api_name")
        with pytest.raises(ValueError) as excinfo:
            _map_field_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: "parent",
            )
        assert "_parent_object_api_name" in str(excinfo.value)

    def test_map_field_details_raises_on_unresolvable_parent(
        self,
    ) -> None:
        """Unresolvable parent Object → ValueError naming the parent
        and pointing at ENTITY_ORDER. NOT NULL column means we MUST
        fail rather than write NULL."""
        with pytest.raises(ValueError) as excinfo:
            _map_field_details(
                normalized=self._normalized(
                    _parent_object_api_name="MysteryObject",
                ),
                entity_id="x",
                parent_resolver=lambda **_: None,
            )
        msg = str(excinfo.value)
        assert "MysteryObject" in msg
        assert "ENTITY_ORDER" in msg


class TestMapRecordTypeDetails:
    def _normalized(self, **overrides) -> dict:
        base = {
            "developerName": "PartnerAccount",
            "label": "Partner Account",
            "active": True,
            "_parent_object_api_name": "Account",
        }
        base.update(overrides)
        return base

    def test_map_record_type_details_resolves_parent(self) -> None:
        """The _parent_object_api_name marker drives the
        object_entity_id FK resolution; resolver gets the standard
        (entity_type='Object', external_id=...) tuple."""
        resolver = MagicMock(return_value="obj-account-uuid")
        row = _map_record_type_details(
            normalized=self._normalized(),
            entity_id="rt-partner-uuid",
            parent_resolver=resolver,
        )
        resolver.assert_called_once_with(
            entity_type="Object", external_id="Account",
        )
        assert row["entity_id"] == "rt-partner-uuid"
        assert row["object_entity_id"] == "obj-account-uuid"
        assert row["is_active"] is True
        # developerName != 'Master' → is_master False
        assert row["is_master"] is False

    def test_map_record_type_details_master_when_developer_name_master(
        self,
    ) -> None:
        """Salesforce's implicit Master RT is detected by
        developerName == 'Master'. This is the canonical convention
        for the auto-generated RT every Object has."""
        row = _map_record_type_details(
            normalized=self._normalized(
                developerName="Master", label="Master",
            ),
            entity_id="rt-master",
            parent_resolver=lambda **_: "obj-account",
        )
        assert row["is_master"] is True

    def test_map_record_type_details_is_master_false_for_other_names(
        self,
    ) -> None:
        """Any developerName other than the exact string 'Master'
        yields is_master=False. Even 'master' (lowercase) → False;
        the convention is case-sensitive."""
        for dev_name in ("PartnerAccount", "master", "Default", "X"):
            row = _map_record_type_details(
                normalized=self._normalized(developerName=dev_name),
                entity_id="x",
                parent_resolver=lambda **_: "obj",
            )
            assert row["is_master"] is False, (
                f"developerName={dev_name!r} unexpectedly mapped to "
                f"is_master=True"
            )

    def test_map_record_type_details_raises_on_missing_parent_marker(
        self,
    ) -> None:
        """Missing _parent_object_api_name marker → ValueError. Phase
        function bug detection."""
        normalized = self._normalized()
        normalized.pop("_parent_object_api_name")
        with pytest.raises(ValueError) as excinfo:
            _map_record_type_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: "parent",
            )
        assert "_parent_object_api_name" in str(excinfo.value)

    def test_map_record_type_details_raises_on_unresolvable_parent(
        self,
    ) -> None:
        """Unresolvable parent Object → ValueError naming the parent.
        NOT NULL FK column means we MUST fail rather than write NULL."""
        with pytest.raises(ValueError) as excinfo:
            _map_record_type_details(
                normalized=self._normalized(
                    _parent_object_api_name="GhostObject",
                ),
                entity_id="x",
                parent_resolver=lambda **_: None,
            )
        msg = str(excinfo.value)
        assert "GhostObject" in msg
        assert "ENTITY_ORDER" in msg


class TestGetDetailMapperRecordType:
    def test_get_detail_mapper_returns_tuple_for_record_type(self) -> None:
        """RecordType is registered — returns ('record_type_details',
        mapper)."""
        result = get_detail_mapper("RecordType")
        assert result is not None
        table_name, mapper = result
        assert table_name == "record_type_details"
        assert mapper is _map_record_type_details


class TestMapLayoutDetails:
    def _normalized(self, **overrides) -> dict:
        base = {
            "_parent_object_api_name": "Account",
            "_layout_full_name": "Account-Account Layout",
            "_layout_type": "Standard",
            "_layout_name_resolved": "Account Layout",
            "detailLayoutSections": [],  # not needed by mapper
        }
        base.update(overrides)
        return base

    def test_resolves_parent_object(self) -> None:
        """object_entity_id resolved from _parent_object_api_name."""
        resolver = MagicMock(return_value="obj-account-uuid")
        row = _map_layout_details(
            normalized=self._normalized(),
            entity_id="layout-uuid-1",
            parent_resolver=resolver,
        )
        resolver.assert_called_once_with(
            entity_type="Object", external_id="Account",
        )
        assert row["entity_id"] == "layout-uuid-1"
        assert row["object_entity_id"] == "obj-account-uuid"
        assert row["layout_type"] == "Standard"
        assert row["layout_api_name"] == "Account Layout"
        assert row["is_active"] is True

    def test_raises_on_missing_parent_marker(self) -> None:
        normalized = self._normalized()
        normalized.pop("_parent_object_api_name")
        with pytest.raises(ValueError) as excinfo:
            _map_layout_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: "parent",
            )
        assert "_parent_object_api_name" in str(excinfo.value)

    def test_raises_on_unresolvable_parent(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            _map_layout_details(
                normalized=self._normalized(
                    _parent_object_api_name="GhostObject",
                ),
                entity_id="x",
                parent_resolver=lambda **_: None,
            )
        msg = str(excinfo.value)
        assert "GhostObject" in msg
        assert "ENTITY_ORDER" in msg

    def test_raises_on_missing_layout_type_marker(self) -> None:
        """_layout_type comes from Salesforce Tooling Layout.LayoutType
        (per §15). If missing, phase function bug."""
        normalized = self._normalized()
        normalized.pop("_layout_type")
        with pytest.raises(ValueError) as excinfo:
            _map_layout_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: "obj",
            )
        assert "_layout_type" in str(excinfo.value)

    def test_raises_on_missing_name_resolved_marker(self) -> None:
        """layout_api_name is NOT NULL no-default; mapper requires
        the resolved name from Tooling."""
        normalized = self._normalized()
        normalized.pop("_layout_name_resolved")
        with pytest.raises(ValueError) as excinfo:
            _map_layout_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: "obj",
            )
        assert "_layout_name_resolved" in str(excinfo.value)


class TestGetDetailMapperLayout:
    def test_returns_tuple_for_layout(self) -> None:
        result = get_detail_mapper("Layout")
        assert result is not None
        table_name, mapper = result
        assert table_name == "layout_details"
        assert mapper is _map_layout_details


class TestMapValidationRuleDetails:
    def _normalized(self, **overrides) -> dict:
        base = {
            "ValidationName": "AmountPositive",
            "Active": True,
            "FullName": "Opportunity.AmountPositive",
            "Metadata": {"errorConditionFormula": "Amount <= 0"},
            "_parent_object_api_name": "Opportunity",
        }
        base.update(overrides)
        return base

    def test_resolves_parent_object(self) -> None:
        """object_entity_id resolved from _parent_object_api_name
        marker (same idiom as Field/RT/Layout). The mapper writes
        only 3 columns; AI-enrichment columns stay NULL for Phase 5
        worker."""
        resolver = MagicMock(return_value="obj-opp-uuid")
        row = _map_validation_rule_details(
            normalized=self._normalized(),
            entity_id="vr-uuid-1",
            parent_resolver=resolver,
        )
        resolver.assert_called_once_with(
            entity_type="Object", external_id="Opportunity",
        )
        assert row["entity_id"] == "vr-uuid-1"
        assert row["object_entity_id"] == "obj-opp-uuid"
        assert row["is_active"] is True
        # AI-enrichment columns NOT set by mapper (Phase 5 fills)
        for ai_col in (
            "plain_english_summary", "summary_model",
            "summary_prompt_version", "summary_generated_at",
            "summary_embedding",
        ):
            assert ai_col not in row

    def test_defaults_active_to_true(self) -> None:
        """Missing Active → True default (matches substrate-1's
        is_active=true DB default + the adapter's convention)."""
        normalized = self._normalized()
        normalized.pop("Active")
        row = _map_validation_rule_details(
            normalized=normalized, entity_id="x",
            parent_resolver=lambda **_: "obj",
        )
        assert row["is_active"] is True

    def test_passes_active_false_through(self) -> None:
        """Explicit Active=False stays False — defaulting must not
        swallow inactive rules."""
        row = _map_validation_rule_details(
            normalized=self._normalized(Active=False),
            entity_id="x",
            parent_resolver=lambda **_: "obj",
        )
        assert row["is_active"] is False

    def test_raises_on_missing_parent_marker(self) -> None:
        normalized = self._normalized()
        normalized.pop("_parent_object_api_name")
        with pytest.raises(ValueError) as excinfo:
            _map_validation_rule_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: "obj",
            )
        assert "_parent_object_api_name" in str(excinfo.value)

    def test_raises_on_unresolvable_parent(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            _map_validation_rule_details(
                normalized=self._normalized(
                    _parent_object_api_name="GhostObject",
                ),
                entity_id="x",
                parent_resolver=lambda **_: None,
            )
        msg = str(excinfo.value)
        assert "GhostObject" in msg
        assert "ENTITY_ORDER" in msg


class TestGetDetailMapperValidationRule:
    def test_returns_tuple_for_validation_rule(self) -> None:
        result = get_detail_mapper("ValidationRule")
        assert result is not None
        table_name, mapper = result
        assert table_name == "validation_rule_details"
        assert mapper is _map_validation_rule_details


class TestMapProfileDetails:
    def _normalized(self, **overrides) -> dict:
        base = {
            "Id": "00eF9000000ABC",
            "Name": "Read Only",
            "FullName": "Read Only",
            "Metadata": {
                "custom": False,
                "userLicense": "Salesforce",
            },
        }
        base.update(overrides)
        return base

    def test_maps_three_hot_columns(self) -> None:
        """Profile is org-level (no parent FK to resolve). Mapper
        writes entity_id + is_active=True (Profiles in Tooling are
        always 'active' in the org sense) + is_custom from
        Metadata.custom + user_license_type from Metadata.userLicense."""
        resolver = MagicMock()  # not called for Profile
        row = _map_profile_details(
            normalized=self._normalized(),
            entity_id="prof-uuid-1",
            parent_resolver=resolver,
        )
        # parent_resolver NOT invoked — Profile has no parent FK
        resolver.assert_not_called()
        assert row["entity_id"] == "prof-uuid-1"
        assert row["is_active"] is True
        assert row["is_custom"] is False
        assert row["user_license_type"] == "Salesforce"
        # AI enrichment columns NOT set by mapper (Phase 5 fills)
        for ai_col in (
            "plain_english_summary", "summary_model",
            "summary_prompt_version", "summary_generated_at",
            "summary_embedding",
        ):
            assert ai_col not in row

    def test_is_custom_from_metadata(self) -> None:
        """Standard Salesforce Profiles have Metadata.custom=False;
        user-created Profiles have custom=True. Mapper passes through."""
        normalized = self._normalized()
        normalized["Metadata"]["custom"] = True
        row = _map_profile_details(
            normalized=normalized, entity_id="x",
            parent_resolver=lambda **_: None,
        )
        assert row["is_custom"] is True

    def test_raises_on_missing_user_license(self) -> None:
        """user_license_type is NOT NULL no-default. If Metadata
        omits userLicense, fail loud with a message naming the
        missing field + the Profile name. (Shouldn't happen — every
        Salesforce Profile has a userLicense — but defensive.)"""
        normalized = self._normalized()
        normalized["Metadata"].pop("userLicense")
        with pytest.raises(ValueError) as excinfo:
            _map_profile_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: None,
            )
        assert "userLicense" in str(excinfo.value)
        assert "user_license_type" in str(excinfo.value)

    def test_raises_on_missing_metadata(self) -> None:
        """If Metadata is None entirely (Phase 2 fetch failed
        silently), fail loud — can't construct a valid detail row."""
        with pytest.raises(ValueError):
            _map_profile_details(
                normalized={"Name": "Mystery"},
                entity_id="x",
                parent_resolver=lambda **_: None,
            )

    def test_handles_various_user_license_tiers(self) -> None:
        """Salesforce has many user license tiers — Salesforce,
        Salesforce Platform, Analytics Cloud Integration User, etc.
        Mapper passes through whatever Tooling returns."""
        for license_tier in [
            "Salesforce",
            "Salesforce Platform",
            "Analytics Cloud Integration User",
            "Guest License User",
        ]:
            normalized = self._normalized()
            normalized["Metadata"]["userLicense"] = license_tier
            row = _map_profile_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: None,
            )
            assert row["user_license_type"] == license_tier


class TestGetDetailMapperProfile:
    def test_returns_tuple_for_profile(self) -> None:
        result = get_detail_mapper("Profile")
        assert result is not None
        table_name, mapper = result
        assert table_name == "profile_details"
        assert mapper is _map_profile_details


class TestMapPermissionSetDetails:
    """Maps post-join, post-license-resolution PermissionSet payload
    → permission_set_details row.

    permission_set_details has 3 NOT NULL columns (entity_id,
    is_custom, license_type). PS is org-level — parent_resolver
    not used by this mapper. license_type comes from the phase-
    injected `_license_label` marker (resolved from LicenseId via
    license_id_to_label map; sentinel '(no license)' for null /
    unmapped)."""

    def _normalized(self, **overrides) -> dict:
        base = {
            "Name": "MyCustomPS",
            "Type": "Regular",
            "IsCustom": True,
            "_license_label": "Salesforce Platform",
        }
        base.update(overrides)
        return base

    def test_writes_entity_id_and_required_columns(self) -> None:
        """entity_id, is_custom, license_type all populated per
        the NOT NULL schema constraints."""
        resolver = MagicMock()  # not used for org-level PS
        row = _map_permission_set_details(
            normalized=self._normalized(),
            entity_id="aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa",
            parent_resolver=resolver,
        )
        assert row == {
            "entity_id": "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa",
            "is_custom": True,
            "license_type": "Salesforce Platform",
        }
        resolver.assert_not_called()

    def test_is_custom_defaults_to_false_when_missing(self) -> None:
        """Standard Salesforce PSes don't always carry IsCustom in
        Tooling — default to False (matches PS schema column
        default but enforced at mapper level for safety)."""
        row = _map_permission_set_details(
            normalized=self._normalized(IsCustom=None),
            entity_id="x", parent_resolver=lambda **_: None,
        )
        assert row["is_custom"] is False

    def test_license_label_falls_back_to_sentinel_when_missing(
        self,
    ) -> None:
        """Defensive: if phase_permission_set somehow didn't inject
        _license_label (e.g., LicenseId was null AND mapper called
        bypassing phase), the mapper falls back to the
        '(no license)' sentinel rather than NULL — keeps the NOT
        NULL schema constraint satisfied."""
        row = _map_permission_set_details(
            normalized={"Name": "X", "IsCustom": False},
            entity_id="y", parent_resolver=lambda **_: None,
        )
        assert row["license_type"] == "(no license)"

    def test_license_label_falls_back_for_empty_string(self) -> None:
        """Empty _license_label string → sentinel (treats it the
        same as missing)."""
        row = _map_permission_set_details(
            normalized=self._normalized(_license_label=""),
            entity_id="x", parent_resolver=lambda **_: None,
        )
        assert row["license_type"] == "(no license)"

    def test_handles_various_license_labels(self) -> None:
        """Realistic license labels — pass through verbatim."""
        for label in [
            "Salesforce",
            "Salesforce Platform",
            "Analytics Cloud Integration User",
            "Sales Cloud",
        ]:
            row = _map_permission_set_details(
                normalized=self._normalized(_license_label=label),
                entity_id="x", parent_resolver=lambda **_: None,
            )
            assert row["license_type"] == label


class TestGetDetailMapperPermissionSet:
    def test_returns_tuple_for_permission_set(self) -> None:
        result = get_detail_mapper("PermissionSet")
        assert result is not None
        table_name, mapper = result
        assert table_name == "permission_set_details"
        assert mapper is _map_permission_set_details


class TestMapUserDetails:
    """Maps decorated User payload → user_details row. TWO NOT NULL
    FK-bearing/required columns the mapper sets:
      - profile_entity_id (NOT NULL FK; resolved via parent_resolver
        from _profile_name marker)
      - user_type (NOT NULL varchar(40) no default; from UserType)
    Plus DB-default booleans is_active + is_external (the latter
    derived from UserType against an EXTERNAL_USER_TYPES set)."""

    def _normalized(self, **overrides) -> dict:
        base = {
            "Id": "005U001",
            "Username": "alice@example.com",
            "Name": "Alice",
            "Email": "alice@example.com",
            "IsActive": True,
            "UserType": "Standard",
            "ProfileId": "00eA",
            "_profile_name": "System Administrator",
        }
        base.update(overrides)
        return base

    def test_writes_entity_id_and_required_columns(self) -> None:
        """All required columns populated. parent_resolver invoked
        for Profile (returns the resolved entity_id)."""
        resolver = MagicMock(return_value="prof-uuid-abc")
        row = _map_user_details(
            normalized=self._normalized(),
            entity_id="user-uuid-001",
            parent_resolver=resolver,
        )
        assert row == {
            "entity_id": "user-uuid-001",
            "profile_entity_id": "prof-uuid-abc",
            "is_active": True,
            "is_external": False,
            "user_type": "Standard",
        }
        resolver.assert_called_once_with(
            entity_type="Profile",
            external_id="System Administrator",
        )

    def test_raises_when_profile_name_marker_missing(self) -> None:
        """Defensive: missing _profile_name marker indicates a
        phase function bug (always sets it, even to None on
        unmappable ProfileId; None then triggers the resolver-
        returns-None branch below). Empty/missing marker → raise."""
        normalized = self._normalized()
        normalized.pop("_profile_name")
        with pytest.raises(ValueError) as excinfo:
            _map_user_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: None,
            )
        assert "_profile_name" in str(excinfo.value)

    def test_raises_when_parent_resolver_returns_none(self) -> None:
        """If the Profile entity isn't materialized (ENTITY_ORDER
        violation or filtered Profile), fail loud — user_details.
        profile_entity_id NOT NULL FK requires resolution."""
        with pytest.raises(ValueError) as excinfo:
            _map_user_details(
                normalized=self._normalized(),
                entity_id="x",
                parent_resolver=lambda **_: None,
            )
        assert "Profile" in str(excinfo.value)
        assert "System Administrator" in str(excinfo.value)

    def test_raises_when_user_type_missing(self) -> None:
        """UserType is NOT NULL no-default on user_details. Salesforce
        always returns UserType on User records; missing means
        malformed upstream payload — fail loud."""
        normalized = self._normalized()
        normalized.pop("UserType")
        with pytest.raises(ValueError) as excinfo:
            _map_user_details(
                normalized=normalized, entity_id="x",
                parent_resolver=lambda **_: "prof-uuid",
            )
        assert "UserType" in str(excinfo.value)

    def test_inactive_user(self) -> None:
        """IsActive=False passes through."""
        row = _map_user_details(
            normalized=self._normalized(IsActive=False),
            entity_id="x",
            parent_resolver=lambda **_: "prof-uuid",
        )
        assert row["is_active"] is False

    def test_external_user_type_csn_only(self) -> None:
        """CsnOnly is in the EXTERNAL_USER_TYPES set →
        is_external=True. CsnOnly is Chatter customer portal users."""
        row = _map_user_details(
            normalized=self._normalized(UserType="CsnOnly"),
            entity_id="x",
            parent_resolver=lambda **_: "prof-uuid",
        )
        assert row["is_external"] is True
        assert row["user_type"] == "CsnOnly"

    def test_internal_user_types_not_external(self) -> None:
        """Standard, AutomatedProcess, CloudIntegrationUser are
        internal (is_external=False)."""
        for utype in ("Standard", "AutomatedProcess",
                      "CloudIntegrationUser"):
            row = _map_user_details(
                normalized=self._normalized(UserType=utype),
                entity_id="x",
                parent_resolver=lambda **_: "prof-uuid",
            )
            assert row["is_external"] is False, (
                f"{utype!r} should not be flagged is_external"
            )


class TestGetDetailMapperUser:
    def test_returns_tuple_for_user(self) -> None:
        result = get_detail_mapper("User")
        assert result is not None
        table_name, mapper = result
        assert table_name == "user_details"
        assert mapper is _map_user_details


class TestMapFlowDetails:
    """Maps a phase-decorated flow-VERSION record → flow_details
    row. flow_type is NOT NULL no-default; triggers_on_object_entity_id
    + trigger_type are nullable and populated ONLY for
    record-triggered flows (§21 scope)."""

    def _normalized(self, *, trigger_type=None, obj=None,
                    process_type_meta="AutoLaunchedFlow",
                    process_type_top=None, is_active=True,
                    version_number=3, filter_formula=None) -> dict:
        start: dict = {}
        if trigger_type is not None:
            start["triggerType"] = trigger_type
        if obj is not None:
            start["object"] = obj
        if filter_formula is not None:
            start["filterFormula"] = filter_formula
        metadata: dict = {"start": start}
        if process_type_meta is not None:
            metadata["processType"] = process_type_meta
        payload: dict = {
            "_developer_name": "MyFlow",
            "_is_active": is_active,
            "VersionNumber": version_number,
            "Metadata": metadata,
        }
        if process_type_top is not None:
            payload["ProcessType"] = process_type_top
        return payload

    def test_autolaunched_flow_minimal_row(self) -> None:
        """Autolaunched flow → flow_type set, trigger fields None,
        parent_resolver NOT invoked (no record trigger)."""
        resolver = MagicMock()
        row = _map_flow_details(
            normalized=self._normalized(),
            entity_id="flow-uuid-001",
            parent_resolver=resolver,
        )
        assert row == {
            "entity_id": "flow-uuid-001",
            "triggers_on_object_entity_id": None,
            "flow_type": "AutoLaunchedFlow",
            "trigger_type": None,
            "is_active": True,
            "version_number": 3,
        }
        resolver.assert_not_called()

    def test_record_triggered_flow_resolves_object_fk(self) -> None:
        """Record-triggered flow → triggers_on_object_entity_id
        resolved via parent_resolver(Object, …); trigger_type
        mapped to schema value."""
        resolver = MagicMock(return_value="obj-account-uuid")
        row = _map_flow_details(
            normalized=self._normalized(
                trigger_type="RecordAfterSave", obj="Account",
                process_type_meta="Flow",
            ),
            entity_id="flow-uuid-002",
            parent_resolver=resolver,
        )
        assert row["triggers_on_object_entity_id"] == "obj-account-uuid"
        assert row["trigger_type"] == "AfterSave"
        assert row["flow_type"] == "Flow"
        resolver.assert_called_once_with(
            entity_type="Object", external_id="Account",
        )

    def test_record_triggered_flow_unsyncable_target_leaves_fk_null(
        self,
    ) -> None:
        """Record-triggered flow whose target Object isn't in the
        synced set (parent_resolver returns None) → FK stays NULL,
        trigger_type still populated. Consistent with the edge's
        silent-skip behavior."""
        row = _map_flow_details(
            normalized=self._normalized(
                trigger_type="RecordBeforeSave",
                obj="sfFma__Internal__c",
            ),
            entity_id="x",
            parent_resolver=lambda **_: None,
        )
        assert row["triggers_on_object_entity_id"] is None
        assert row["trigger_type"] == "BeforeSave"

    def test_platform_event_flow_no_trigger_resolution(self) -> None:
        """PlatformEvent flow → trigger fields None, resolver NOT
        called even though Metadata.start.object is set (§21
        scope: PlatformEvent is out of TRIGGERS_ON scope)."""
        resolver = MagicMock()
        row = _map_flow_details(
            normalized=self._normalized(
                trigger_type="PlatformEvent",
                obj="MHolt__Org_Expiry_Notification__e",
            ),
            entity_id="x",
            parent_resolver=resolver,
        )
        assert row["triggers_on_object_entity_id"] is None
        assert row["trigger_type"] is None
        resolver.assert_not_called()

    def test_flow_type_falls_back_to_top_level_process_type(
        self,
    ) -> None:
        """Metadata.processType absent → fall back to top-level
        ProcessType column."""
        row = _map_flow_details(
            normalized=self._normalized(
                process_type_meta=None,
                process_type_top="AutoLaunchedFlow",
            ),
            entity_id="x",
            parent_resolver=lambda **_: None,
        )
        assert row["flow_type"] == "AutoLaunchedFlow"

    def test_raises_when_flow_type_missing(self) -> None:
        """flow_type is NOT NULL no-default. Neither
        Metadata.processType nor top-level ProcessType present →
        fail loud."""
        with pytest.raises(ValueError) as excinfo:
            _map_flow_details(
                normalized=self._normalized(
                    process_type_meta=None, process_type_top=None,
                ),
                entity_id="x",
                parent_resolver=lambda **_: None,
            )
        assert "flow_type" in str(excinfo.value)

    def test_is_active_false_for_deactivated_flow(self) -> None:
        """_is_active=False (FlowDefinition with no ActiveVersionId
        → phase_flow fell back to LatestVersionId) passes through."""
        row = _map_flow_details(
            normalized=self._normalized(is_active=False),
            entity_id="x",
            parent_resolver=lambda **_: None,
        )
        assert row["is_active"] is False


class TestGetDetailMapperFlow:
    def test_returns_tuple_for_flow(self) -> None:
        result = get_detail_mapper("Flow")
        assert result is not None
        table_name, mapper = result
        assert table_name == "flow_details"
        assert mapper is _map_flow_details

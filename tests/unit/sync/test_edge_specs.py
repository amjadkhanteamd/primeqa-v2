"""Tests for primeqa.sync.edge_specs — per-source-entity-type edge
specifications.

This is the third parallel registry after _PRESENTATION_FUNCTIONS
and _DETAIL_TABLE_MAPPERS. Tests lock the Field-source extractor
shapes and the get_edge_specs dispatch behavior.
"""
from __future__ import annotations

from primeqa.sync.edge_specs import (
    EdgeSpec,
    _field_belongs_to_targets,
    _field_has_relationship_to_targets,
    _layout_belongs_to_targets,
    _layout_includes_field_properties,
    _layout_includes_field_targets,
    _permission_set_grants_field_access_properties,
    _permission_set_grants_field_access_targets,
    _permission_set_grants_object_access_properties,
    _permission_set_grants_object_access_targets,
    _permission_set_inherits_permission_set_targets,
    _profile_grants_field_access_properties,
    _profile_grants_field_access_targets,
    _profile_grants_object_access_properties,
    _profile_grants_object_access_targets,
    _record_type_belongs_to_targets,
    _validation_rule_applies_to_targets,
    _validation_rule_belongs_to_targets,
    get_edge_specs,
)


class TestFieldBelongsToTargets:
    def test_returns_parent_object_when_marker_present(self) -> None:
        """The phase-injected _parent_object_api_name marker is the
        sole source of the parent Object reference."""
        targets = _field_belongs_to_targets({
            "name": "Industry",
            "_parent_object_api_name": "Account",
        })
        assert targets == ["Account"]

    def test_returns_empty_when_marker_missing(self) -> None:
        """Defensive: missing marker → empty list (no edge written).
        Materialize layer skips empty-target edges silently — the
        actual failure point if this happens is on the entity-side
        external_id construction, which raises a louder error."""
        targets = _field_belongs_to_targets({"name": "Industry"})
        assert targets == []


class TestFieldHasRelationshipToTargets:
    def test_returns_empty_for_non_reference_field(self) -> None:
        """Non-reference fields (string, picklist, number, ...) have
        empty referenceTo → no HAS_RELATIONSHIP_TO edges."""
        targets = _field_has_relationship_to_targets({
            "name": "Industry",
            "type": "picklist",
            "referenceTo": [],
        })
        assert targets == []

    def test_returns_single_target_for_standard_reference(self) -> None:
        """Standard reference field (e.g., Account.OwnerId → User)
        has exactly one entry in referenceTo."""
        targets = _field_has_relationship_to_targets({
            "name": "OwnerId",
            "type": "reference",
            "referenceTo": ["User"],
        })
        assert targets == ["User"]

    def test_returns_multiple_targets_for_polymorphic_reference(
        self,
    ) -> None:
        """Polymorphic reference (e.g., Task.WhoId → Contact OR Lead)
        produces one HAS_RELATIONSHIP_TO edge per target. The full
        polymorphic graph lives in edges; the detail-table column
        picks up only the first target."""
        targets = _field_has_relationship_to_targets({
            "name": "WhoId",
            "type": "reference",
            "referenceTo": ["Contact", "Lead"],
            "polymorphicForeignKey": True,
        })
        assert targets == ["Contact", "Lead"]

    def test_returns_empty_when_reference_to_missing(self) -> None:
        """A field with no referenceTo key at all (post-normalize on
        some odd shapes) → empty list. Robust against partial
        payloads."""
        assert _field_has_relationship_to_targets({"name": "x"}) == []


class TestRecordTypeBelongsToTargets:
    def test_returns_parent_object_when_marker_present(self) -> None:
        """The phase-injected _parent_object_api_name marker is the
        sole source of the parent Object reference."""
        targets = _record_type_belongs_to_targets({
            "developerName": "PartnerAccount",
            "_parent_object_api_name": "Account",
        })
        assert targets == ["Account"]

    def test_returns_empty_when_marker_missing(self) -> None:
        """Defensive: missing marker → empty list. Materialize layer
        skips empty-target edges silently — louder failure happens at
        external_id construction time (where FullName is required)."""
        targets = _record_type_belongs_to_targets({
            "developerName": "PartnerAccount",
        })
        assert targets == []


class TestGetEdgeSpecs:
    def test_returns_two_specs_for_field(self) -> None:
        """Field has BELONGS_TO + HAS_RELATIONSHIP_TO this cycle.
        HAS_PICKLIST_VALUES deferred per corrections-log §10."""
        specs = get_edge_specs("Field")
        assert len(specs) == 2
        edge_types = {spec.edge_type for spec in specs}
        assert edge_types == {"BELONGS_TO", "HAS_RELATIONSHIP_TO"}

    def test_field_specs_target_object_for_both_edges(self) -> None:
        """Both Field edges this cycle point at Object — BELONGS_TO
        to parent, HAS_RELATIONSHIP_TO to reference target."""
        specs = get_edge_specs("Field")
        for spec in specs:
            assert spec.target_entity_type == "Object"

    def test_returns_one_spec_for_record_type(self) -> None:
        """RecordType has BELONGS_TO only this cycle.
        CONSTRAINS_PICKLIST_VALUES deferred per corrections-log §14
        (substrate-1 registry-vs-derivation contradiction) and §10
        (fetch_custom_field_metadata missing)."""
        specs = get_edge_specs("RecordType")
        assert len(specs) == 1
        spec = specs[0]
        assert spec.edge_type == "BELONGS_TO"
        assert spec.target_entity_type == "Object"

    def test_returns_two_specs_for_layout(self) -> None:
        """Layout has BELONGS_TO + INCLUDES_FIELD this cycle.
        ASSIGNED_TO_PROFILE_RECORDTYPE deferred per §16 (Profile
        entities don't exist yet)."""
        specs = get_edge_specs("Layout")
        assert len(specs) == 2
        edge_types = {spec.edge_type for spec in specs}
        assert edge_types == {"BELONGS_TO", "INCLUDES_FIELD"}

    def test_returns_two_specs_for_validation_rule(self) -> None:
        """ValidationRule has BELONGS_TO + APPLIES_TO this cycle
        (both → Object, both property-less). REFERENCES → Field
        deferred per corrections-log §17 (formula parser unbuilt)."""
        specs = get_edge_specs("ValidationRule")
        assert len(specs) == 2
        edge_types = {spec.edge_type for spec in specs}
        assert edge_types == {"BELONGS_TO", "APPLIES_TO"}
        # Both target Object
        for spec in specs:
            assert spec.target_entity_type == "Object"
        # Both property-less (REFERENCES is the only property-bearing
        # VR-source edge and it's deferred)
        for spec in specs:
            assert spec.extract_properties is None

    def test_returns_two_specs_for_profile(self) -> None:
        """Profile has GRANTS_OBJECT_ACCESS + GRANTS_FIELD_ACCESS
        (both property-bearing, both PERMISSION-category).
        HAS_PROFILE (User-source) waits for User phase;
        ASSIGNED_TO_PROFILE_RECORDTYPE (Layout-source) deferred §16."""
        specs = get_edge_specs("Profile")
        assert len(specs) == 2
        by_type = {s.edge_type: s for s in specs}
        assert set(by_type.keys()) == {
            "GRANTS_OBJECT_ACCESS", "GRANTS_FIELD_ACCESS",
        }
        assert by_type["GRANTS_OBJECT_ACCESS"].target_entity_type == "Object"
        assert by_type["GRANTS_FIELD_ACCESS"].target_entity_type == "Field"
        # Both property-bearing — extract_properties wired
        for spec in specs:
            assert spec.extract_properties is not None

    def test_layout_includes_field_spec_has_property_extractor(
        self,
    ) -> None:
        """INCLUDES_FIELD is property-bearing — extract_properties
        is wired. BELONGS_TO is property-less — extract_properties
        is None."""
        specs = {s.edge_type: s for s in get_edge_specs("Layout")}
        assert specs["BELONGS_TO"].extract_properties is None
        assert specs["INCLUDES_FIELD"].extract_properties is not None

    def test_returns_empty_for_unregistered_entity_type(self) -> None:
        """Entity types without edges (e.g., Object source, or types
        whose phases haven't shipped yet) get []. Materialize layer
        uses this as a feature flag — empty list means skip the
        edge-write subsystem entirely."""
        assert get_edge_specs("Object") == []
        assert get_edge_specs("PicklistValue") == []
        assert get_edge_specs("NotARealEntityType") == []

    def test_get_edge_specs_returns_edge_spec_instances(self) -> None:
        """Type lock: specs are EdgeSpec dataclasses (not raw dicts or
        tuples) so the materialize layer can rely on attribute access
        (.target_entity_type, .edge_type, .extract_target_external_ids).
        """
        specs = get_edge_specs("Field")
        for spec in specs:
            assert isinstance(spec, EdgeSpec)


# ----------------------------------------------------------------------
# Layout edge spec extractors
# ----------------------------------------------------------------------


class TestLayoutBelongsToTargets:
    def test_returns_parent_object_when_marker_present(self) -> None:
        targets = _layout_belongs_to_targets({
            "_parent_object_api_name": "Account",
        })
        assert targets == ["Account"]

    def test_returns_empty_when_marker_missing(self) -> None:
        assert _layout_belongs_to_targets({}) == []


class TestLayoutIncludesFieldTargets:
    def _layout(self, parent="Account") -> dict:
        """Build a minimal layout with one Field component."""
        return {
            "_parent_object_api_name": parent,
            "detailLayoutSections": [
                {
                    "heading": "Account Information",
                    "layoutRows": [
                        {
                            "layoutItems": [
                                {
                                    "placeholder": False,
                                    "layoutComponents": [
                                        {"type": "Field",
                                         "value": "Phone"},
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }

    def test_returns_composite_external_ids(self) -> None:
        """Field references in payload are bare (e.g., 'Phone');
        extractor composes parent_object + field_name to match
        Field external_id pattern."""
        targets = _layout_includes_field_targets(self._layout())
        assert targets == ["Account.Phone"]

    def test_empty_when_parent_marker_missing(self) -> None:
        layout = self._layout()
        layout.pop("_parent_object_api_name")
        assert _layout_includes_field_targets(layout) == []

    def test_skips_placeholder_items(self) -> None:
        """Layout cells with placeholder=True have no field; skipped."""
        layout = self._layout()
        # Add a placeholder item
        layout["detailLayoutSections"][0]["layoutRows"][0][
            "layoutItems"
        ].append({"placeholder": True, "layoutComponents": []})
        targets = _layout_includes_field_targets(layout)
        # Only the Phone field — placeholder skipped
        assert targets == ["Account.Phone"]

    def test_skips_non_field_components(self) -> None:
        """layoutComponents with type != 'Field' (e.g., 'Separator',
        'EmptySpace') are not Field-references; skipped."""
        layout = self._layout()
        # Add a Separator component
        layout["detailLayoutSections"][0]["layoutRows"][0][
            "layoutItems"
        ][0]["layoutComponents"].append({"type": "Separator"})
        targets = _layout_includes_field_targets(layout)
        # Only the Field component survives
        assert targets == ["Account.Phone"]

    def test_handles_compound_field_with_multiple_components(self) -> None:
        """A compound name field (FirstName+LastName+Salutation
        shown together) yields multiple components in one item —
        each component becomes its own edge target."""
        layout = self._layout()
        layout["detailLayoutSections"][0]["layoutRows"][0][
            "layoutItems"
        ][0]["layoutComponents"] = [
            {"type": "Field", "value": "Salutation"},
            {"type": "Field", "value": "FirstName"},
            {"type": "Field", "value": "LastName"},
        ]
        targets = _layout_includes_field_targets(layout)
        assert targets == [
            "Account.Salutation",
            "Account.FirstName",
            "Account.LastName",
        ]

    def test_empty_layout_no_sections(self) -> None:
        """Layout with no detailLayoutSections (e.g., quick-action-
        only layouts) yields no targets."""
        assert _layout_includes_field_targets({
            "_parent_object_api_name": "Account",
            "detailLayoutSections": [],
        }) == []


class TestLayoutIncludesFieldProperties:
    def _layout(self) -> dict:
        return {
            "_parent_object_api_name": "Account",
            "detailLayoutSections": [
                {
                    "heading": "Account Information",
                    "layoutRows": [
                        {
                            "layoutItems": [
                                {
                                    "placeholder": False,
                                    "required": False,
                                    "editableForUpdate": True,
                                    "uiBehavior": "Edit",
                                    "layoutComponents": [
                                        {"type": "Field",
                                         "value": "Phone"},
                                    ],
                                },
                                {
                                    "placeholder": False,
                                    "required": True,
                                    "editableForUpdate": False,
                                    "uiBehavior": "ReadOnly",
                                    "layoutComponents": [
                                        {"type": "Field",
                                         "value": "Name"},
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }

    def test_returns_section_and_position(self) -> None:
        """Properties carry section_name, section_order, row, column
        from the item's position in the nested structure."""
        props = _layout_includes_field_properties(
            self._layout(), "Account.Phone",
        )
        assert props["section_name"] == "Account Information"
        assert props["section_order"] == 0
        assert props["row"] == 0
        assert props["column"] == 0

    def test_returns_required_and_readonly_flags(self) -> None:
        """required and editableForUpdate fields map to is_required
        and is_readonly. The Name field in the fixture is required
        + read-only."""
        props = _layout_includes_field_properties(
            self._layout(), "Account.Name",
        )
        assert props["is_required"] is True
        assert props["is_readonly"] is True
        assert props["column"] == 1  # Second item in row

    def test_falls_back_to_unnamed_for_blank_section_heading(self) -> None:
        """IncludesFieldProperties.section_name requires
        min_length=1. If a section has an empty heading
        (placeholder/system section), we substitute '(unnamed)' to
        satisfy validation."""
        layout = self._layout()
        layout["detailLayoutSections"][0]["heading"] = ""
        props = _layout_includes_field_properties(
            layout, "Account.Phone",
        )
        assert props["section_name"] == "(unnamed)"

    def test_returns_empty_dict_when_target_not_found(self) -> None:
        """If the target external_id doesn't match any field in the
        layout (defensive — shouldn't happen if _targets and
        _properties are coherent), returns {}."""
        props = _layout_includes_field_properties(
            self._layout(), "Account.NonExistentField",
        )
        assert props == {}

    def test_ui_behavior_required_implies_is_required(self) -> None:
        """uiBehavior='Required' is an alternative to item.required.
        Either triggers is_required=True."""
        layout = self._layout()
        item = layout["detailLayoutSections"][0]["layoutRows"][0][
            "layoutItems"
        ][0]
        # Phone: was not required; flip uiBehavior to Required
        item["required"] = False
        item["uiBehavior"] = "Required"
        props = _layout_includes_field_properties(
            layout, "Account.Phone",
        )
        assert props["is_required"] is True


# ----------------------------------------------------------------------
# ValidationRule edge spec extractors
# ----------------------------------------------------------------------


class TestValidationRuleBelongsToTargets:
    def test_returns_parent_object_when_marker_present(self) -> None:
        targets = _validation_rule_belongs_to_targets({
            "ValidationName": "AmountPositive",
            "_parent_object_api_name": "Account",
        })
        assert targets == ["Account"]

    def test_returns_empty_when_marker_missing(self) -> None:
        targets = _validation_rule_belongs_to_targets({
            "ValidationName": "X",
        })
        assert targets == []


class TestValidationRuleAppliesToTargets:
    def test_returns_parent_object_when_marker_present(self) -> None:
        """APPLIES_TO targets the same Object as BELONGS_TO; the
        semantic distinction is in the edge_type, not the target."""
        targets = _validation_rule_applies_to_targets({
            "ValidationName": "AmountPositive",
            "_parent_object_api_name": "Account",
        })
        assert targets == ["Account"]

    def test_returns_empty_when_marker_missing(self) -> None:
        targets = _validation_rule_applies_to_targets({})
        assert targets == []


# ----------------------------------------------------------------------
# Profile edge spec extractors (GRANTS_OBJECT_ACCESS + GRANTS_FIELD_ACCESS)
# ----------------------------------------------------------------------


class TestProfileGrantsObjectAccessTargets:
    def _profile(self, op_list=()) -> dict:
        return {"Metadata": {"objectPermissions": list(op_list)}}

    def test_extracts_object_names(self) -> None:
        """Each objectPermissions[].object is an Object api_name —
        matches Object phase's external_id directly."""
        targets = _profile_grants_object_access_targets(self._profile([
            {"object": "Account", "allowRead": True},
            {"object": "Contact", "allowRead": True, "allowEdit": True},
            {"object": "MyNS__Custom__c", "allowRead": True},
        ]))
        assert targets == ["Account", "Contact", "MyNS__Custom__c"]

    def test_returns_empty_when_no_object_permissions(self) -> None:
        assert _profile_grants_object_access_targets(self._profile([])) == []

    def test_returns_empty_when_metadata_missing(self) -> None:
        """Defensive: missing Metadata → empty (no edges to write)."""
        assert _profile_grants_object_access_targets({}) == []

    def test_skips_entries_missing_object_field(self) -> None:
        """Salesforce-side data integrity: an objectPermissions entry
        without 'object' is malformed; skip it rather than crash."""
        targets = _profile_grants_object_access_targets(self._profile([
            {"object": "Account", "allowRead": True},
            {"allowRead": False},  # missing 'object'
        ]))
        assert targets == ["Account"]


class TestProfileGrantsObjectAccessProperties:
    def _profile_with(self, op_list) -> dict:
        return {"Metadata": {"objectPermissions": list(op_list)}}

    def test_maps_all_six_access_flags(self) -> None:
        """Six Salesforce fields → six GrantsObjectAccessProperties
        flags. allowCreate→can_create, allowRead→can_read,
        allowEdit→can_edit, allowDelete→can_delete,
        viewAllRecords→can_view_all, modifyAllRecords→can_modify_all."""
        op = {
            "object": "Account",
            "allowCreate": True,
            "allowRead": True,
            "allowEdit": True,
            "allowDelete": False,
            "viewAllRecords": True,
            "modifyAllRecords": False,
        }
        props = _profile_grants_object_access_properties(
            self._profile_with([op]), "Account",
        )
        assert props == {
            "can_create": True,
            "can_read": True,
            "can_edit": True,
            "can_delete": False,
            "can_view_all": True,
            "can_modify_all": False,
        }

    def test_drops_extra_salesforce_fields(self) -> None:
        """Extra Salesforce fields (viewAllFields, customizeSetup,
        deleteSetup, viewSetup) are NOT in the schema; mapper must
        drop them — otherwise validate_edge_properties would raise
        (extra='forbid')."""
        op = {
            "object": "Account",
            "allowRead": True,
            "viewAllFields": True,
            "customizeSetup": False,
            "deleteSetup": None,
            "viewSetup": True,
        }
        props = _profile_grants_object_access_properties(
            self._profile_with([op]), "Account",
        )
        assert "viewAllFields" not in props
        assert "customizeSetup" not in props
        # Only the 6 schema fields present
        assert set(props.keys()) == {
            "can_create", "can_read", "can_edit", "can_delete",
            "can_view_all", "can_modify_all",
        }

    def test_defaults_missing_flags_to_false(self) -> None:
        """Salesforce sometimes omits a flag when its value would be
        False. Mapper must default missing keys to False (matches
        the schema's default)."""
        op = {"object": "Account", "allowRead": True}
        props = _profile_grants_object_access_properties(
            self._profile_with([op]), "Account",
        )
        assert props["can_read"] is True
        assert props["can_create"] is False
        assert props["can_edit"] is False
        assert props["can_delete"] is False
        assert props["can_view_all"] is False
        assert props["can_modify_all"] is False

    def test_returns_empty_when_target_not_found(self) -> None:
        """Defensive: extractor + properties drift would let the
        materialize layer ask for a target not in the array. Returns
        {} rather than raising — caller's mismatch detection
        responsibility."""
        props = _profile_grants_object_access_properties(
            self._profile_with([{"object": "Account"}]), "Contact",
        )
        assert props == {}


class TestProfileGrantsFieldAccessTargets:
    def _profile(self, fp_list=()) -> dict:
        return {"Metadata": {"fieldPermissions": list(fp_list)}}

    def test_extracts_composite_field_external_ids(self) -> None:
        """fieldPermissions[].field is already in the composite
        '{Object}.{Field}' format that Field phase uses."""
        targets = _profile_grants_field_access_targets(self._profile([
            {"field": "Account.AccountSource", "readable": True},
            {"field": "Account.Industry", "readable": True,
             "editable": True},
            {"field": "Contact.Email", "readable": False},
        ]))
        assert targets == [
            "Account.AccountSource", "Account.Industry",
            "Contact.Email",
        ]

    def test_returns_empty_when_no_field_permissions(self) -> None:
        assert _profile_grants_field_access_targets(
            self._profile([])
        ) == []


class TestProfileGrantsFieldAccessProperties:
    def _profile_with(self, fp_list) -> dict:
        return {"Metadata": {"fieldPermissions": list(fp_list)}}

    def test_maps_readable_and_editable(self) -> None:
        """readable → can_read, editable → can_edit. Field-level
        permissions have ONLY these two flags (no create/delete/
        view-all/modify-all at field level per Salesforce model)."""
        fp = {
            "field": "Account.Industry",
            "readable": True,
            "editable": False,
        }
        props = _profile_grants_field_access_properties(
            self._profile_with([fp]), "Account.Industry",
        )
        assert props == {"can_read": True, "can_edit": False}

    def test_only_two_schema_fields(self) -> None:
        """Schema is exactly 2 fields. Mapper must not invent
        can_create/etc on field perms (those would violate
        GrantsFieldAccessProperties' extra='forbid')."""
        props = _profile_grants_field_access_properties(
            self._profile_with([
                {"field": "Account.Name", "readable": True,
                 "editable": True},
            ]),
            "Account.Name",
        )
        assert set(props.keys()) == {"can_read", "can_edit"}

    def test_defaults_to_false(self) -> None:
        """Missing readable/editable → False. Same convention as
        object-perm flags."""
        props = _profile_grants_field_access_properties(
            self._profile_with([{"field": "Account.Name"}]),
            "Account.Name",
        )
        assert props == {"can_read": False, "can_edit": False}


# ----------------------------------------------------------------------
# PermissionSet edge spec extractors (GRANTS_OBJECT_ACCESS +
# GRANTS_FIELD_ACCESS via PS-shape input + INHERITS_PERMISSION_SET
# from _member_ps_names marker)
# ----------------------------------------------------------------------


class TestPermissionSetGrantsObjectAccessTargets:
    def _ps(self, op_list=()) -> dict:
        """Build a normalized PermissionSet with objectPermissions
        list in PS-shape (Data API column names)."""
        return {
            "Name": "TestPS",
            "Type": "Regular",
            "objectPermissions": list(op_list),
        }

    def test_extracts_sobject_type_from_each_entry(self) -> None:
        """Each entry's `SobjectType` is the Object api_name —
        different field name than Profile's `object` (lowercase)."""
        targets = _permission_set_grants_object_access_targets(self._ps([
            {"SobjectType": "Account", "PermissionsRead": True},
            {"SobjectType": "Contact", "PermissionsRead": True},
        ]))
        assert targets == ["Account", "Contact"]

    def test_empty_perms_returns_empty(self) -> None:
        assert _permission_set_grants_object_access_targets(
            self._ps([])
        ) == []

    def test_missing_object_permissions_key_returns_empty(self) -> None:
        assert _permission_set_grants_object_access_targets({}) == []

    def test_skips_entries_with_missing_sobject_type(self) -> None:
        """Defensive: entries without SobjectType (malformed payload)
        are dropped silently — same pattern as Profile extractor."""
        targets = _permission_set_grants_object_access_targets(self._ps([
            {"SobjectType": "Account", "PermissionsRead": True},
            {"PermissionsRead": True},                   # no SobjectType
            {"SobjectType": "", "PermissionsRead": True},  # empty
        ]))
        assert targets == ["Account"]


class TestPermissionSetGrantsObjectAccessProperties:
    def _ps_with(self, op_list) -> dict:
        return {
            "objectPermissions": op_list,
        }

    def test_maps_all_six_permissions_flags(self) -> None:
        """PS-shape (PermissionsCreate/Read/Edit/Delete/
        ViewAllRecords/ModifyAllRecords) → schema-shape (can_*).
        Distinct field-naming from Profile (allowCreate etc) but
        same schema output."""
        op = {
            "SobjectType": "Account",
            "PermissionsCreate": True,
            "PermissionsRead": True,
            "PermissionsEdit": True,
            "PermissionsDelete": False,
            "PermissionsViewAllRecords": True,
            "PermissionsModifyAllRecords": False,
        }
        props = _permission_set_grants_object_access_properties(
            self._ps_with([op]), "Account",
        )
        assert props == {
            "can_create": True,
            "can_read": True,
            "can_edit": True,
            "can_delete": False,
            "can_view_all": True,
            "can_modify_all": False,
        }

    def test_defaults_to_false_for_missing_flags(self) -> None:
        """Sparse PS payload missing some Permissions* keys —
        each missing flag defaults to False."""
        op = {"SobjectType": "Account"}
        props = _permission_set_grants_object_access_properties(
            self._ps_with([op]), "Account",
        )
        assert props == {
            "can_create": False,
            "can_read": False,
            "can_edit": False,
            "can_delete": False,
            "can_view_all": False,
            "can_modify_all": False,
        }

    def test_returns_empty_when_target_not_found(self) -> None:
        """No matching SobjectType → {}. Defensive — should not
        happen if _targets and _properties are coherent."""
        props = _permission_set_grants_object_access_properties(
            self._ps_with([{"SobjectType": "Account"}]), "Contact",
        )
        assert props == {}


class TestPermissionSetGrantsFieldAccessTargets:
    def _ps(self, fp_list=()) -> dict:
        return {
            "fieldPermissions": list(fp_list),
        }

    def test_extracts_capitalized_field_key(self) -> None:
        """PS Data API column is `Field` (capitalized); Profile
        Metadata uses lowercase `field`. Composite "Object.Field"
        format same as Field phase external_id."""
        targets = _permission_set_grants_field_access_targets(self._ps([
            {"Field": "Account.Industry"},
            {"Field": "Contact.Phone"},
        ]))
        assert targets == ["Account.Industry", "Contact.Phone"]

    def test_empty_perms_returns_empty(self) -> None:
        assert _permission_set_grants_field_access_targets(
            self._ps([])
        ) == []


class TestPermissionSetGrantsFieldAccessProperties:
    def _ps_with(self, fp_list) -> dict:
        return {"fieldPermissions": fp_list}

    def test_maps_read_and_edit(self) -> None:
        """PS-shape (PermissionsRead/PermissionsEdit) →
        schema-shape (can_read/can_edit). Field-level perms have
        only these two; no create/delete/view_all at field level."""
        fp = {
            "Field": "Account.Industry",
            "PermissionsRead": True,
            "PermissionsEdit": False,
        }
        props = _permission_set_grants_field_access_properties(
            self._ps_with([fp]), "Account.Industry",
        )
        assert props == {"can_read": True, "can_edit": False}

    def test_defaults_to_false(self) -> None:
        props = _permission_set_grants_field_access_properties(
            self._ps_with([{"Field": "Account.Name"}]), "Account.Name",
        )
        assert props == {"can_read": False, "can_edit": False}


class TestPermissionSetInheritsPermissionSetTargets:
    """INHERITS_PERMISSION_SET: PSG → member PS. Source is a PS
    decorated with the `_member_ps_names` marker by phase_permission_set
    (only Type='Group' PSes get the marker)."""

    def test_reads_member_ps_names_marker(self) -> None:
        """Targets come from _member_ps_names list — the phase-injected
        marker built by joining PermissionSetGroupComponent rows."""
        normalized = {
            "Name": "MyPSG",
            "Type": "Group",
            "_member_ps_names": ["MemberA", "MemberB", "MemberC"],
        }
        targets = _permission_set_inherits_permission_set_targets(
            normalized,
        )
        assert targets == ["MemberA", "MemberB", "MemberC"]

    def test_returns_empty_when_marker_missing(self) -> None:
        """Non-Group PSes have no marker → no INHERITS edges. The
        extractor doesn't filter Type itself; the absence of the
        marker is the filter."""
        normalized = {"Name": "Regular PS", "Type": "Regular"}
        assert _permission_set_inherits_permission_set_targets(
            normalized,
        ) == []

    def test_empty_member_list_returns_empty(self) -> None:
        """Defensive: marker present but list empty (PSG with no
        components yet) → no edges."""
        normalized = {
            "Name": "EmptyPSG",
            "Type": "Group",
            "_member_ps_names": [],
        }
        assert _permission_set_inherits_permission_set_targets(
            normalized,
        ) == []


class TestGetEdgeSpecsPermissionSet:
    def test_returns_three_specs_for_permission_set(self) -> None:
        """PermissionSet registers exactly 3 edge specs:
        GRANTS_OBJECT_ACCESS, GRANTS_FIELD_ACCESS (both property-
        bearing) + INHERITS_PERMISSION_SET (property-less)."""
        specs = get_edge_specs("PermissionSet")
        assert len(specs) == 3
        edge_types = {s.edge_type for s in specs}
        assert edge_types == {
            "GRANTS_OBJECT_ACCESS",
            "GRANTS_FIELD_ACCESS",
            "INHERITS_PERMISSION_SET",
        }
        # GRANTS_* are property-bearing
        goa = next(s for s in specs
                  if s.edge_type == "GRANTS_OBJECT_ACCESS")
        gfa = next(s for s in specs
                  if s.edge_type == "GRANTS_FIELD_ACCESS")
        assert goa.extract_properties is not None
        assert gfa.extract_properties is not None
        # INHERITS_PERMISSION_SET is property-less
        inh = next(s for s in specs
                   if s.edge_type == "INHERITS_PERMISSION_SET")
        assert inh.extract_properties is None
        # Target types
        assert goa.target_entity_type == "Object"
        assert gfa.target_entity_type == "Field"
        assert inh.target_entity_type == "PermissionSet"

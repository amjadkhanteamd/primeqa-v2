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
    _record_type_belongs_to_targets,
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

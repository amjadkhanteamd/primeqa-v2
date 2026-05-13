"""Per-entity-type edge write specifications.

Edge identity for property-less edges (BELONGS_TO,
HAS_RELATIONSHIP_TO, HAS_PICKLIST_VALUES, HAS_PROFILE, etc. — per
TIER_1_EDGES with properties_schema=None) is the triple
(source_entity_id, target_entity_id, edge_type). No properties hash
is needed because there are no properties: substrate-1's
validate_edge_properties is explicit that None-schema edge types
"accept ONLY the empty dict" (primeqa/semantic/edges.py:411).

When future edge types with property schemas land (INCLUDES_FIELD
with layout placement, GRANTS_OBJECT_ACCESS with permission flags,
etc.), extend EdgeSpec with a properties extractor and add a
hash-based bucketing path in materialize.py — see §11 of the
corrections-log for the supersession-semantics rationale.

This is the third parallel registry alongside
_PRESENTATION_FUNCTIONS (presentation.py) and _DETAIL_TABLE_MAPPERS
(detail_mappers.py). All three follow the same discipline: a single
dict keyed by entity_type, a lookup helper that returns the registered
value or a safe default for unregistered types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class EdgeSpec:
    """Declarative spec for one edge type written from a source entity.

    Attributes:
        target_entity_type: which entity_type the edge points at
            (e.g., 'Object' for Field.BELONGS_TO)
        edge_type: the TIER_1_EDGES key (e.g., 'BELONGS_TO',
            'HAS_RELATIONSHIP_TO', 'INCLUDES_FIELD')
        extract_target_external_ids: callable that receives the source
            entity's normalized payload and returns a list of target
            external_ids. Returning an empty list means "this source
            entity has no edges of this type" — common for spec-by-
            spec conditional edges (e.g., HAS_RELATIONSHIP_TO only
            applies to reference-typed Fields, which is encoded by
            the extractor returning [] for non-reference fields).
        extract_properties: optional callable for property-bearing
            edges. Receives (normalized, target_external_id) and
            returns the properties dict for that source→target edge.
            None for property-less edges (BELONGS_TO,
            HAS_RELATIONSHIP_TO, HAS_PICKLIST_VALUES, HAS_PROFILE) —
            the materialize layer treats absence as "properties = {}"
            and routes through the identity-based supersession path.
            Required for edges whose TIER_1_EDGES entry has a non-
            None properties_schema (INCLUDES_FIELD,
            GRANTS_OBJECT_ACCESS, etc.) — these route through the
            hash-based supersession path.
    """
    target_entity_type: str
    edge_type: str
    extract_target_external_ids: Callable[[dict], list[str]]
    extract_properties: Optional[Callable[[dict, str], dict]] = None


# ----------------------------------------------------------------------
# Field edge spec extractors
# ----------------------------------------------------------------------


def _field_belongs_to_targets(normalized: dict) -> list[str]:
    """Every Field belongs to exactly one Object — the parent.

    Parent Object is supplied via the `_parent_object_api_name`
    marker that `phase_field` injects on each field raw payload
    before normalization. The marker survives `_strip_volatile`
    (not in _VOLATILE_KEYS) and lands in the normalized payload.

    Returns a single-element list or empty (defensive — never
    expected in normal sync flow but guards against a missing
    marker from a future phase function bug).
    """
    parent = normalized.get("_parent_object_api_name")
    return [parent] if parent else []


def _field_has_relationship_to_targets(normalized: dict) -> list[str]:
    """Field → Object when field is a reference (lookup or master-
    detail) type.

    Salesforce sets `referenceTo` to a list of target Object API
    names. Polymorphic references (e.g., Task.WhoId → Contact OR
    Lead) carry multiple entries → multiple HAS_RELATIONSHIP_TO
    edges, one per target. Non-reference fields have an empty
    referenceTo (or no key at all post-normalize) → no edges.
    """
    ref_to = normalized.get("referenceTo") or []
    # Substrate-1's _normalize_field sorts referenceTo strings, so
    # the order here is deterministic across syncs.
    return list(ref_to)


# ----------------------------------------------------------------------
# RecordType edge spec extractors
# ----------------------------------------------------------------------


def _record_type_belongs_to_targets(normalized: dict) -> list[str]:
    """Every RecordType belongs to exactly one Object — the parent.

    Parent Object is supplied via the `_parent_object_api_name`
    marker that `phase_record_type` injects on each raw RT payload
    before normalization. The marker survives `_strip_volatile`
    and lands in the normalized payload.

    Returns a single-element list or empty (defensive — never
    expected in normal sync flow but guards against a missing
    marker from a phase function bug).
    """
    parent = normalized.get("_parent_object_api_name")
    return [parent] if parent else []


# ----------------------------------------------------------------------
# Layout edge spec extractors
# ----------------------------------------------------------------------


def _layout_belongs_to_targets(normalized: dict) -> list[str]:
    """Every Layout belongs to exactly one Object — the parent.

    Same pattern as Field/RecordType: parent Object is supplied via
    the `_parent_object_api_name` marker that phase_layout injects.
    """
    parent = normalized.get("_parent_object_api_name")
    return [parent] if parent else []


def _layout_includes_field_targets(normalized: dict) -> list[str]:
    """Extract Field external_ids referenced by this Layout.

    Salesforce REST `/sobjects/{name}/describe/layouts` returns
    layouts as a nested structure:
      layouts[i]
        .detailLayoutSections[j]
          .layoutRows[k]
            .layoutItems[l]
              .layoutComponents[m]
                .type == 'Field'
                .value == field API name (bare, e.g., 'Phone')

    To match the Field phase's composite external_id pattern
    ({Object}.{name}), we compose target external_ids using the
    parent Object marker. Skips:
    - items with `placeholder: true` (empty layout cells; no field)
    - components with `type != 'Field'` (e.g., 'Separator',
      'EmptySpace', custom-component blocks)
    - components without a `value` (defensive)

    A layoutItem can have MULTIPLE components (compound name fields
    showing FirstName + LastName + Salutation, address fields, etc.).
    Each component yields one INCLUDES_FIELD edge.

    Returns a flat list of composite Field external_ids ready for
    target lookup via parent_resolver.
    """
    parent_object = normalized.get("_parent_object_api_name")
    if not parent_object:
        return []

    targets: list[str] = []
    sections = normalized.get("detailLayoutSections") or []
    for section in sections:
        if not isinstance(section, dict):
            continue
        for row in section.get("layoutRows") or []:
            if not isinstance(row, dict):
                continue
            for item in row.get("layoutItems") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("placeholder"):
                    continue
                for component in item.get("layoutComponents") or []:
                    if not isinstance(component, dict):
                        continue
                    if component.get("type") != "Field":
                        continue
                    field_name = component.get("value")
                    if field_name:
                        targets.append(f"{parent_object}.{field_name}")
    return targets


def _layout_includes_field_properties(
    normalized: dict, target_external_id: str,
) -> dict:
    """Locate the layoutItem matching target_external_id and return
    its IncludesFieldProperties.

    Iterates the same nested structure as
    _layout_includes_field_targets but captures section/row/column
    position + flags. Returns the property dict matching
    substrate-1's IncludesFieldProperties schema:
      section_name: section.heading (must be non-empty per schema's
                    min_length=1 — falls back to "(unnamed)" if
                    blank to satisfy validation)
      section_order: index in detailLayoutSections
      row: index in layoutRows
      column: index in layoutItems within a row
      is_required: item.required OR uiBehavior=='Required'
      is_readonly: NOT item.editableForUpdate OR uiBehavior=='ReadOnly'

    Returns {} if target isn't found (shouldn't happen if _targets
    and _properties are coherent; defensive guard for unexpected
    payload shapes).

    For compound fields (multiple components per item), all
    components share the same item position. Each component's edge
    gets identical properties — the section/row/column tracks the
    item, not the per-component sub-position.
    """
    parent_object = normalized.get("_parent_object_api_name")
    # target_external_id is "{parent_object}.{field_name}"; we just
    # need the field_name half for matching.
    target_field = target_external_id.split(".", 1)[-1]

    sections = normalized.get("detailLayoutSections") or []
    for section_idx, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        heading = section.get("heading") or "(unnamed)"
        # Schema requires min_length=1; ensure we never pass empty.
        if not heading:
            heading = "(unnamed)"
        for row_idx, row in enumerate(section.get("layoutRows") or []):
            if not isinstance(row, dict):
                continue
            for col_idx, item in enumerate(row.get("layoutItems") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("placeholder"):
                    continue
                for component in item.get("layoutComponents") or []:
                    if not isinstance(component, dict):
                        continue
                    if component.get("type") != "Field":
                        continue
                    if component.get("value") != target_field:
                        continue
                    ui_behavior = item.get("uiBehavior")
                    is_required = (
                        bool(item.get("required", False))
                        or ui_behavior == "Required"
                    )
                    # editableForUpdate=True means writable; absent
                    # defaults to True. is_readonly = NOT writable.
                    is_readonly = (
                        not bool(item.get("editableForUpdate", True))
                        or ui_behavior == "ReadOnly"
                    )
                    # Substrate-1's schema caps column at 0-3 (4-column
                    # max). Clamp defensively if a layout exceeds it
                    # (should never happen in practice).
                    return {
                        "section_name": heading,
                        "section_order": section_idx,
                        "row": row_idx,
                        "column": min(col_idx, 3),
                        "is_required": is_required,
                        "is_readonly": is_readonly,
                    }
    return {}


# ----------------------------------------------------------------------
# ValidationRule edge spec extractors
# ----------------------------------------------------------------------


def _validation_rule_belongs_to_targets(normalized: dict) -> list[str]:
    """Every VR belongs to one Object (STRUCTURAL containment)."""
    parent = normalized.get("_parent_object_api_name")
    return [parent] if parent else []


def _validation_rule_applies_to_targets(normalized: dict) -> list[str]:
    """Every VR applies to one Object (BEHAVIOR relationship).

    Same target as BELONGS_TO for VRs (the parent Object), but distinct
    edge_type captures the BEHAVIOR category. The active-uniqueness
    partial index keys on (source, target, edge_type) — different
    edge_types coexist for the same (source, target) pair.

    Distinct semantic: BELONGS_TO is structural containment ("this VR
    is OWNED by this Object"); APPLIES_TO is behavioral ("when records
    of this Object change, this rule fires"). Traversal queries that
    care about behavior-only impact filter to APPLIES_TO; containment
    queries filter to BELONGS_TO.
    """
    parent = normalized.get("_parent_object_api_name")
    return [parent] if parent else []


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


_EDGE_SPECS: dict[str, list[EdgeSpec]] = {
    "Field": [
        EdgeSpec(
            target_entity_type="Object",
            edge_type="BELONGS_TO",
            extract_target_external_ids=_field_belongs_to_targets,
        ),
        EdgeSpec(
            target_entity_type="Object",
            edge_type="HAS_RELATIONSHIP_TO",
            extract_target_external_ids=_field_has_relationship_to_targets,
        ),
        # HAS_PICKLIST_VALUES deferred — requires Tooling-API
        # fetch_custom_field_metadata (REST describe doesn't expose
        # GVS/SVS references). Documented in corrections-log §10.
    ],
    "RecordType": [
        EdgeSpec(
            target_entity_type="Object",
            edge_type="BELONGS_TO",
            extract_target_external_ids=_record_type_belongs_to_targets,
        ),
        # CONSTRAINS_PICKLIST_VALUES deferred — both substrate-1's
        # registry-vs-derivation contradiction (§14) and the §10
        # GVS/SVS detection block need to be resolved first. Will
        # land in a future cycle that includes
        # fetch_custom_field_metadata + record_type_picklist_value_
        # grants junction-table writes.
    ],
    "Layout": [
        EdgeSpec(
            target_entity_type="Object",
            edge_type="BELONGS_TO",
            extract_target_external_ids=_layout_belongs_to_targets,
        ),
        EdgeSpec(
            target_entity_type="Field",
            edge_type="INCLUDES_FIELD",
            extract_target_external_ids=_layout_includes_field_targets,
            extract_properties=_layout_includes_field_properties,
        ),
        # ASSIGNED_TO_PROFILE_RECORDTYPE deferred per corrections-log
        # §16 — Profile entities don't exist yet (Profile phase
        # pending); pre-wiring would create code paths that silently
        # skip every edge write (resolver returns None for all
        # targets). Wire when Profile phase lands.
    ],
    "ValidationRule": [
        EdgeSpec(
            target_entity_type="Object",
            edge_type="BELONGS_TO",
            extract_target_external_ids=_validation_rule_belongs_to_targets,
        ),
        EdgeSpec(
            target_entity_type="Object",
            edge_type="APPLIES_TO",
            extract_target_external_ids=_validation_rule_applies_to_targets,
        ),
        # REFERENCES → Field deferred per corrections-log §17.
        # Requires Salesforce formula parser (PRIORVALUE/ISCHANGED/
        # ISNEW tokenization + field-name disambiguation) +
        # validation_rule_field_refs junction-table writer. Same
        # deferral pattern as CONSTRAINS_PICKLIST_VALUES (§14):
        # unbuilt infrastructure block.
    ],
    # Other entity types add their specs here as their phase cycles land.
}


def get_edge_specs(entity_type: str) -> list[EdgeSpec]:
    """Return edge specs for this entity_type; empty list if none.

    Empty-list-on-miss (rather than KeyError) is the right default:
    the materialize layer uses this as a feature flag — "should I
    bother running the edge-write pipeline for this entity type?".
    Entity types with no edges (e.g., User in some configurations)
    get [] and the materialize layer skips the whole subsystem.
    """
    return _EDGE_SPECS.get(entity_type, [])

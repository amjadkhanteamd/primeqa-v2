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
# Profile edge spec extractors
# ----------------------------------------------------------------------
#
# Profile permissions come from Tooling Profile.Metadata sub-arrays:
#   objectPermissions[]:  per-Object CRUD + view-all + modify-all flags
#   fieldPermissions[]:   per-Field read + edit flags
#
# Extractors are paired — _targets and _properties walk the same array
# and key by the SAME index. The _properties extractor receives the
# target_external_id from the materialize layer and finds the matching
# array entry by content (not position) — robust against re-ordering
# that substrate-1's _normalize_profile does via _sort_list_of_dicts.


def _profile_grants_object_access_targets(normalized: dict) -> list[str]:
    """Extract Object external_ids from Profile.Metadata.objectPermissions.

    Each entry's `object` field IS the Object api_name (e.g.,
    'Account', 'sfFma__Internal__c'). Matches the Object phase's
    external_id format directly — no composition needed.

    Targets pointing at unsyncable Objects (managed-package
    internals, etc.) are filtered out by the materialize layer's
    `if target_id is None: continue` guard during target
    resolution. No phase-level filter needed.
    """
    metadata = normalized.get("Metadata") or {}
    perms = metadata.get("objectPermissions") or []
    targets: list[str] = []
    for p in perms:
        if not isinstance(p, dict):
            continue
        obj_name = p.get("object")
        if obj_name:
            targets.append(obj_name)
    return targets


def _profile_grants_object_access_properties(
    normalized: dict, target_external_id: str,
) -> dict:
    """Locate the objectPermissions entry for target_external_id
    and return GrantsObjectAccessProperties.

    Mapping (Salesforce field → schema property):
      allowCreate       → can_create
      allowRead         → can_read
      allowEdit         → can_edit
      allowDelete       → can_delete
      viewAllRecords    → can_view_all
      modifyAllRecords  → can_modify_all

    Other fields in the Salesforce payload (viewAllFields,
    customizeSetup, deleteSetup, viewSetup) are NOT in the schema
    — they're either record-level concepts not modeled here or
    legacy Setup-only flags. Dropped.

    Returns {} if target not found (shouldn't happen if _targets
    and _properties are coherent).
    """
    metadata = normalized.get("Metadata") or {}
    for p in metadata.get("objectPermissions") or []:
        if not isinstance(p, dict):
            continue
        if p.get("object") != target_external_id:
            continue
        return {
            "can_create": bool(p.get("allowCreate", False)),
            "can_read": bool(p.get("allowRead", False)),
            "can_edit": bool(p.get("allowEdit", False)),
            "can_delete": bool(p.get("allowDelete", False)),
            "can_view_all": bool(p.get("viewAllRecords", False)),
            "can_modify_all": bool(p.get("modifyAllRecords", False)),
        }
    return {}


def _profile_grants_field_access_targets(normalized: dict) -> list[str]:
    """Extract Field external_ids from Profile.Metadata.fieldPermissions.

    Each entry's `field` field is already in the composite
    "{Object}.{Field}" format that Field phase uses for external_ids.
    No composition needed.

    Volume: light Profile ~600 field perms; heavy Profile (System
    Administrator) several thousand. Skipped targets (fields on
    unsyncable Objects) filtered by materialize layer's target-
    resolver guard.
    """
    metadata = normalized.get("Metadata") or {}
    perms = metadata.get("fieldPermissions") or []
    targets: list[str] = []
    for p in perms:
        if not isinstance(p, dict):
            continue
        field_ref = p.get("field")
        if field_ref:
            targets.append(field_ref)
    return targets


def _profile_grants_field_access_properties(
    normalized: dict, target_external_id: str,
) -> dict:
    """Locate the fieldPermissions entry for target_external_id and
    return GrantsFieldAccessProperties.

    Mapping (Salesforce field → schema property):
      readable  → can_read
      editable  → can_edit

    Per substrate-1: field-level permissions are simpler than
    object-level — only read + edit. Salesforce doesn't have
    field-level create/delete/view_all/modify_all; those operate
    at the object level.
    """
    metadata = normalized.get("Metadata") or {}
    for p in metadata.get("fieldPermissions") or []:
        if not isinstance(p, dict):
            continue
        if p.get("field") != target_external_id:
            continue
        return {
            "can_read": bool(p.get("readable", False)),
            "can_edit": bool(p.get("editable", False)),
        }
    return {}


# ----------------------------------------------------------------------
# PermissionSet edge spec extractors
# ----------------------------------------------------------------------
#
# PermissionSet differs from Profile in that the same edge types
# (GRANTS_OBJECT_ACCESS, GRANTS_FIELD_ACCESS) draw from a DIFFERENT
# raw shape:
#   Profile.Metadata.objectPermissions[].object        + allow* flags
#   PermissionSet.objectPermissions[].SobjectType     + Permissions* flags
# (the sync layer joins ObjectPermissions / FieldPermissions to each
# PS by ParentId, so the per-PS shape ends up looking similar to
# Profile but with different field-naming).
#
# Adding a third edge type for PSG support:
#   INHERITS_PERMISSION_SET: source=PermissionSet (Group),
#   target=PermissionSet (member). Property-less. Walks
#   `_member_ps_names` marker on the normalized payload (phase
#   function decorates PSGs with this list after joining
#   PermissionSetGroupComponent rows to their member PSes).


def _permission_set_grants_object_access_targets(normalized: dict) -> list[str]:
    """Extract Object external_ids (sObject api names) from a
    PermissionSet's objectPermissions list.

    Each entry's `SobjectType` field IS the Object api_name (per
    PermissionSet's Data-API ObjectPermissions shape — different
    field name than Profile's Metadata.objectPermissions which
    uses `object`). Same downstream meaning: the Object api_name
    to look up against synced entities.
    """
    perms = normalized.get("objectPermissions") or []
    targets: list[str] = []
    for p in perms:
        if not isinstance(p, dict):
            continue
        obj_name = p.get("SobjectType")
        if obj_name:
            targets.append(obj_name)
    return targets


def _permission_set_grants_object_access_properties(
    normalized: dict, target_external_id: str,
) -> dict:
    """Locate the objectPermissions entry for target_external_id and
    return GrantsObjectAccessProperties.

    Mapping (Salesforce PS field → schema property):
      PermissionsCreate              → can_create
      PermissionsRead                → can_read
      PermissionsEdit                → can_edit
      PermissionsDelete              → can_delete
      PermissionsViewAllRecords      → can_view_all
      PermissionsModifyAllRecords    → can_modify_all

    Different field-naming than Profile (PermissionsCreate vs
    allowCreate); same schema output. The schema lives in
    primeqa/semantic/edges.GrantsObjectAccessProperties.
    """
    for p in normalized.get("objectPermissions") or []:
        if not isinstance(p, dict):
            continue
        if p.get("SobjectType") != target_external_id:
            continue
        return {
            "can_create": bool(p.get("PermissionsCreate", False)),
            "can_read": bool(p.get("PermissionsRead", False)),
            "can_edit": bool(p.get("PermissionsEdit", False)),
            "can_delete": bool(p.get("PermissionsDelete", False)),
            "can_view_all": bool(p.get("PermissionsViewAllRecords", False)),
            "can_modify_all": bool(p.get("PermissionsModifyAllRecords", False)),
        }
    return {}


def _permission_set_grants_field_access_targets(normalized: dict) -> list[str]:
    """Extract Field external_ids from a PermissionSet's
    fieldPermissions list.

    Each entry's `Field` field is the composite '{Object}.{Field}'
    api name — matches Field phase's external_id format. Same shape
    as Profile.Metadata.fieldPermissions[].field; the Data-API
    column is capitalized 'Field' rather than 'field', so we read
    accordingly.
    """
    perms = normalized.get("fieldPermissions") or []
    targets: list[str] = []
    for p in perms:
        if not isinstance(p, dict):
            continue
        field_ref = p.get("Field")
        if field_ref:
            targets.append(field_ref)
    return targets


def _permission_set_grants_field_access_properties(
    normalized: dict, target_external_id: str,
) -> dict:
    """Locate the fieldPermissions entry for target_external_id and
    return GrantsFieldAccessProperties.

    Mapping (Salesforce PS field → schema property):
      PermissionsRead   → can_read
      PermissionsEdit   → can_edit

    Field-level permissions are simpler than object-level (no
    create/delete/view_all/modify_all at the field level).
    """
    for p in normalized.get("fieldPermissions") or []:
        if not isinstance(p, dict):
            continue
        if p.get("Field") != target_external_id:
            continue
        return {
            "can_read": bool(p.get("PermissionsRead", False)),
            "can_edit": bool(p.get("PermissionsEdit", False)),
        }
    return {}


def _permission_set_inherits_permission_set_targets(
    normalized: dict,
) -> list[str]:
    """Extract member PermissionSet Names from a PSG's
    `_member_ps_names` marker.

    INHERITS_PERMISSION_SET represents PermissionSetGroup → member
    PermissionSet membership (per DECISIONS_LOG D-019). The sync
    layer's phase_permission_set decorates each Type='Group' PS
    with `_member_ps_names: list[str]` by joining
    PermissionSetGroupComponent rows to permission_sets and looking
    up Id → Name for each component's PermissionSetId.

    Non-PSGs have no `_member_ps_names` marker → returns [] →
    no INHERITS edges. The edge spec doesn't need to filter on
    Type; the absence of the marker is the filter.
    """
    member_names = normalized.get("_member_ps_names") or []
    return list(member_names)


# ----------------------------------------------------------------------
# User edge spec extractors (HAS_PROFILE property-less +
# HAS_PERMISSION_SET property-bearing with HasPermissionSetProperties)
# ----------------------------------------------------------------------
#
# User source draws from phase-decorated markers:
#   _profile_name      — single Profile Name resolved via the
#                        fetcher's profile_id_to_name map and
#                        decorated by phase_user
#   _ps_assignments    — list of {permission_set_name, assigned_at,
#                        expiration_date, assigned_by_user_entity_id?}
#                        built by phase_user from PermissionSetAssignment
#                        rows (with a two-pass step that resolves
#                        CreatedById → assigned_by_user_entity_id)
#
# HAS_PERMISSION_SET is property-bearing per TIER_1_EDGES
# (HasPermissionSetProperties: assigned_at, assigned_by_user_entity_id,
# expiration_date — all Optional).


def _user_has_profile_targets(normalized: dict) -> list[str]:
    """Every User has at most one Profile.

    The phase function resolves User.ProfileId → Profile.Name via
    the fetcher's profile_id_to_name map and injects the resolved
    name as `_profile_name` on each User payload. The extractor
    returns that name (or [] when missing — defensive against a
    phase-function bug or a User whose ProfileId doesn't appear
    in the fetcher's Profile catalog).
    """
    profile_name = normalized.get("_profile_name")
    return [profile_name] if profile_name else []


def _user_has_permission_set_targets(normalized: dict) -> list[str]:
    """Users may have N direct PermissionSet assignments.

    phase_user reads PermissionSetAssignment rows from the
    fetcher (filtered to PermissionSetGroupId IS NULL — direct
    assignments only; PSG-derived memberships flow through
    INHERITS_PERMISSION_SET from the PermissionSet cycle), builds
    a per-User _ps_assignments list, and the extractor returns
    each entry's permission_set_name as a target external_id.
    Targets pointing at PermissionSets the org filters out
    (Type='Profile' shadows etc.) silently skip at the materialize
    layer's parent_resolver guard.
    """
    assignments = normalized.get("_ps_assignments") or []
    targets: list[str] = []
    for a in assignments:
        if not isinstance(a, dict):
            continue
        name = a.get("permission_set_name")
        if name:
            targets.append(name)
    return targets


def _user_has_permission_set_properties(
    normalized: dict, target_external_id: str,
) -> dict:
    """Locate the matching assignment by PS Name; return
    HasPermissionSetProperties fields.

    Mapping (PSA shape → schema property):
      CreatedDate           → assigned_at         (date)
      ExpirationDate        → expiration_date     (date)
      CreatedById           → assigned_by_user_entity_id  (UUID)
                              (RESOLVED by phase_user's pass 2 from
                              the fresh User-Id→entity_id map; this
                              extractor reads the resolved value
                              directly. Cleaner than threading the
                              map into the extractor signature.)

    Returns {} if target not found (shouldn't happen if _targets
    and _properties are coherent; defensive). Returns only the
    keys whose values are non-None to keep the property dict tight
    — Pydantic schema defaults Optional fields to None on absence,
    same observable shape on the edges.properties JSONB column.
    """
    assignments = normalized.get("_ps_assignments") or []
    for a in assignments:
        if not isinstance(a, dict):
            continue
        if a.get("permission_set_name") != target_external_id:
            continue
        props: dict = {}
        assigned_at = a.get("assigned_at")
        if assigned_at is not None:
            props["assigned_at"] = assigned_at
        assigner = a.get("assigned_by_user_entity_id")
        if assigner is not None:
            props["assigned_by_user_entity_id"] = assigner
        exp = a.get("expiration_date")
        if exp is not None:
            props["expiration_date"] = exp
        return props
    return {}


# ----------------------------------------------------------------------
# Flow edge spec extractors (TRIGGERS_ON — record-triggered flows only)
# ----------------------------------------------------------------------
#
# TRIGGERS_ON is property-bearing (TriggersOnProperties: trigger_type
# required + condition_text optional). Substrate-1's schema scopes
# trigger_type to the four RECORD-trigger types only; autolaunched,
# screen, platform-event, and scheduled flows produce no TRIGGERS_ON
# edge (per corrections-log §21 — a scope clarification, not a
# deferral). The extractors gate on _RECORD_TRIGGER_TYPE_MAP so a
# non-record trigger_type never reaches the Pydantic schema's
# validator.

# Salesforce raw Metadata.start.triggerType → TriggersOnProperties
# schema value. The schema's trigger_type_known validator allows
# exactly these four targets.
_RECORD_TRIGGER_TYPE_MAP = {
    "RecordBeforeSave": "BeforeSave",
    "RecordAfterSave": "AfterSave",
    "RecordBeforeDelete": "BeforeDelete",
    "RecordAfterDelete": "AfterDelete",
}


def _flow_triggers_on_targets(normalized: dict) -> list[str]:
    """Extract the record-trigger Object target for a Flow.

    A record-triggered Flow's Metadata.start carries:
      triggerType  — 'RecordBeforeSave' / 'RecordAfterSave' /
                     'RecordBeforeDelete' / 'RecordAfterDelete'
      object       — the SObject api name the flow triggers on

    Returns [object_api_name] when the Flow is record-triggered;
    [] otherwise. Autolaunched / screen flows have no
    Metadata.start.triggerType in the record-trigger set;
    platform-event flows carry triggerType='PlatformEvent'
    (object is a `__e` Platform Event, not a syncable Object);
    scheduled flows carry triggerType='Scheduled' (no object).
    All of those correctly produce no TRIGGERS_ON edge — they're
    out of scope per substrate-1's TriggersOnProperties design
    (corrections-log §21), not silently skipped.

    Defensive against missing Metadata / start (returns []).
    """
    start = (normalized.get("Metadata") or {}).get("start") or {}
    trigger_type_raw = start.get("triggerType")
    if trigger_type_raw not in _RECORD_TRIGGER_TYPE_MAP:
        return []
    target_object = start.get("object")
    return [target_object] if target_object else []


def _flow_triggers_on_properties(
    normalized: dict, target_external_id: str,
) -> dict:
    """Return TriggersOnProperties for a record-triggered Flow.

    Only reached when _flow_triggers_on_targets returned a target
    — i.e., Metadata.start.triggerType IS one of the four
    record-trigger types — so the mapped trigger_type is always
    a valid schema value here.

    Mapping (Salesforce raw → schema):
      Metadata.start.triggerType    → trigger_type
        (via _RECORD_TRIGGER_TYPE_MAP: RecordBeforeSave→BeforeSave,
         RecordAfterSave→AfterSave, RecordBeforeDelete→BeforeDelete,
         RecordAfterDelete→AfterDelete)
      Metadata.start.filterFormula  → condition_text (optional;
        the flow's entry-condition formula text. Tier 1 stores it
        raw; Tier 2 parses it. Omitted from the returned dict when
        absent — Pydantic defaults the Optional field to None.)
    """
    start = (normalized.get("Metadata") or {}).get("start") or {}
    trigger_type_raw = start.get("triggerType")
    trigger_type = _RECORD_TRIGGER_TYPE_MAP.get(trigger_type_raw)
    result: dict = {"trigger_type": trigger_type}
    condition_text = start.get("filterFormula")
    if condition_text:
        result["condition_text"] = condition_text
    return result


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
    "Profile": [
        EdgeSpec(
            target_entity_type="Object",
            edge_type="GRANTS_OBJECT_ACCESS",
            extract_target_external_ids=_profile_grants_object_access_targets,
            extract_properties=_profile_grants_object_access_properties,
        ),
        EdgeSpec(
            target_entity_type="Field",
            edge_type="GRANTS_FIELD_ACCESS",
            extract_target_external_ids=_profile_grants_field_access_targets,
            extract_properties=_profile_grants_field_access_properties,
        ),
    ],
    "PermissionSet": [
        EdgeSpec(
            target_entity_type="Object",
            edge_type="GRANTS_OBJECT_ACCESS",
            extract_target_external_ids=_permission_set_grants_object_access_targets,
            extract_properties=_permission_set_grants_object_access_properties,
        ),
        EdgeSpec(
            target_entity_type="Field",
            edge_type="GRANTS_FIELD_ACCESS",
            extract_target_external_ids=_permission_set_grants_field_access_targets,
            extract_properties=_permission_set_grants_field_access_properties,
        ),
        EdgeSpec(
            target_entity_type="PermissionSet",
            edge_type="INHERITS_PERMISSION_SET",
            extract_target_external_ids=_permission_set_inherits_permission_set_targets,
            # property-less (no extract_properties — TIER_1_EDGES
            # registers properties_schema=None for this edge type)
        ),
    ],
    "User": [
        EdgeSpec(
            target_entity_type="Profile",
            edge_type="HAS_PROFILE",
            extract_target_external_ids=_user_has_profile_targets,
            # property-less per TIER_1_EDGES (derived_from_column=True
            # on Profile attribution — captured here as an explicit
            # edge to support traversal queries)
        ),
        EdgeSpec(
            target_entity_type="PermissionSet",
            edge_type="HAS_PERMISSION_SET",
            extract_target_external_ids=_user_has_permission_set_targets,
            extract_properties=_user_has_permission_set_properties,
        ),
    ],
    "Flow": [
        EdgeSpec(
            target_entity_type="Object",
            edge_type="TRIGGERS_ON",
            extract_target_external_ids=_flow_triggers_on_targets,
            extract_properties=_flow_triggers_on_properties,
        ),
        # TRIGGERS_ON only — scoped to record-triggered flows per
        # substrate-1's TriggersOnProperties design (corrections-
        # log §21). Platform-event / scheduled / autolaunched /
        # screen flows produce no edge.
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

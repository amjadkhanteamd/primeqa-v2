"""Substrate 1 — Tier 1 edge type registry and property schemas.

Per D-019: 14 edge types across 4 categories (STRUCTURAL, CONFIG, PERMISSION,
BEHAVIOR). 6 of the 14 carry typed properties; this module defines the
Pydantic v2 schemas for those properties and the central TIER_1_EDGES
registry that maps edge_type -> metadata.

Per D-016 discipline: JSONB validation lives at the application layer.
The DB-level CHECK constraint on edges.properties only enforces
`jsonb_typeof = 'object'`. Anything stricter — required fields, value
ranges, type narrowing — runs through Pydantic before the dict touches
SQL. Use validate_edge_properties(edge_type, props_dict) at the write
boundary; it returns a validated dict ready for INSERT, or raises
pydantic.ValidationError with field-by-field messages.

Per D-017: 8 of 14 edges are derived from columns (auto-generated alongside
their source rows by the sync engine). The `derived_from_column` flag in
each registry entry tells the sync engine which writer path to take.
Phase 2 sync engine reads this; Phase 1 just records it.

The 14 edges:

  STRUCTURAL (2):
    BELONGS_TO                  derived
    HAS_RELATIONSHIP_TO         derived

  CONFIG (4):
    INCLUDES_FIELD              independent, properties: layout placement
    ASSIGNED_TO_PROFILE_RECORDTYPE   independent, properties: rt + default
    CONSTRAINS_PICKLIST_VALUES  derived
    HAS_PICKLIST_VALUES         derived

  PERMISSION (5):
    GRANTS_OBJECT_ACCESS        independent, properties: 6 access flags
    GRANTS_FIELD_ACCESS         independent, properties: read/edit
    INHERITS_PERMISSION_SET     independent
    HAS_PROFILE                 derived
    HAS_PERMISSION_SET          independent, properties: assignment metadata

  BEHAVIOR (3):
    TRIGGERS_ON                 derived, properties: trigger_type + condition
    APPLIES_TO                  derived
    REFERENCES                  derived, properties: ref_type + flags
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Type
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ----------------------------------------------------------------------
# Common base — strict, frozen, no extras
# ----------------------------------------------------------------------

class _EdgeProperties(BaseModel):
    """Common config for all edge property schemas.

    - frozen: properties are immutable after construction. Edge dicts
      are write-once on the way to JSONB.
    - extra='forbid': reject unknown fields. If a writer adds a typo'd key
      ('is_requried') we want it to fail loudly, not silently land in JSONB.
    - strict not set globally because Pydantic v2 strict mode rejects
      JSON-typical coercions we want (e.g., int from numeric str). Per-field
      strictness is applied where it matters via Field(...) constraints.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")


# ----------------------------------------------------------------------
# CONFIG.INCLUDES_FIELD — Layout -> Field
# ----------------------------------------------------------------------

class IncludesFieldProperties(_EdgeProperties):
    """Properties for Layout INCLUDES_FIELD Field.

    Per D-017: layouts model field placement structurally (section + grid),
    not as opaque JSON. section_name is the human-displayed group label;
    section_order is its position within the layout; row/column position
    the field within the section's grid.

    Field placement constraints follow Salesforce's own model: standard
    Salesforce layouts have 1-3 columns and unbounded rows; we widen
    column to 0-3 to allow 4-column compact layouts that some orgs use.
    """
    section_name: str = Field(..., min_length=1, max_length=255)
    section_order: int = Field(..., ge=0)
    row: int = Field(..., ge=0)
    column: int = Field(..., ge=0, le=3)
    is_required: bool = False
    is_readonly: bool = False


# ----------------------------------------------------------------------
# CONFIG.ASSIGNED_TO_PROFILE_RECORDTYPE — Layout -> Profile
# ----------------------------------------------------------------------

class AssignedToProfileRecordtypeProperties(_EdgeProperties):
    """Properties for Layout ASSIGNED_TO_PROFILE_RECORDTYPE Profile.

    A layout is assigned to a (Profile, RecordType) pair, so the edge
    targets the Profile and the RecordType is carried as a property
    (record_type_entity_id). is_default flags the layout that this
    Profile sees when no explicit record type is selected.
    """
    record_type_entity_id: UUID
    is_default: bool = False


# ----------------------------------------------------------------------
# PERMISSION.GRANTS_OBJECT_ACCESS — Profile or PermissionSet -> Object
# ----------------------------------------------------------------------

class GrantsObjectAccessProperties(_EdgeProperties):
    """Properties for Profile/PermissionSet GRANTS_OBJECT_ACCESS Object.

    Per D-020: object-level permissions stored as one edge per
    (Profile/PermissionSet, Object) with all 6 access flags as properties.
    Not separate edges per access type.

    can_view_all and can_modify_all are stronger than can_read/can_edit:
    they bypass sharing rules. We don't enforce that view_all implies
    can_read at the schema level — Salesforce semantics allow weird
    combinations and the model should reflect what's there, not what's
    consistent.
    """
    can_create: bool = False
    can_read: bool = False
    can_edit: bool = False
    can_delete: bool = False
    can_view_all: bool = False
    can_modify_all: bool = False


# ----------------------------------------------------------------------
# PERMISSION.GRANTS_FIELD_ACCESS — Profile or PermissionSet -> Field
# ----------------------------------------------------------------------

class GrantsFieldAccessProperties(_EdgeProperties):
    """Properties for Profile/PermissionSet GRANTS_FIELD_ACCESS Field.

    Field-level permissions are simpler than object-level — only read
    and edit. (Salesforce does not have field-level create/delete/view_all/
    modify_all; those operate at the object level.)

    Per D-020: ~250K edges of this type for a typical org. The
    effective_field_permissions materialized view (Phase 2) aggregates
    Profile + PermissionSets per User to produce a per-(User, Field)
    answer.
    """
    can_read: bool = False
    can_edit: bool = False


# ----------------------------------------------------------------------
# PERMISSION.HAS_PERMISSION_SET — User -> PermissionSet
# ----------------------------------------------------------------------

class HasPermissionSetProperties(_EdgeProperties):
    """Properties for User HAS_PERMISSION_SET PermissionSet.

    Captures assignment metadata: when, by whom, and (for time-bounded
    PermissionSetLicenseAssignments and ExpirationDate-bearing assignments)
    when access expires.

    expiration_date is None for indefinite assignments; the materialized
    view excludes expired ones at refresh time.
    """
    assigned_at: Optional[date] = None
    assigned_by_user_entity_id: Optional[UUID] = None
    expiration_date: Optional[date] = None


# ----------------------------------------------------------------------
# BEHAVIOR.TRIGGERS_ON — Flow -> Object
# ----------------------------------------------------------------------

class TriggersOnProperties(_EdgeProperties):
    """Properties for Flow TRIGGERS_ON Object.

    trigger_type follows Salesforce's record-trigger taxonomy. condition_text
    is the entry-condition formula text (Tier 1 stores it raw; Tier 2 will
    parse it into a structured form).

    Salesforce trigger_type values: BeforeSave, AfterSave (record-triggered
    flows). Autolaunched and screen flows have no trigger_type — they don't
    produce TRIGGERS_ON edges in the first place. Process-builder flows
    map onto the same value space.
    """
    trigger_type: str = Field(..., min_length=1, max_length=40)
    condition_text: Optional[str] = None

    @field_validator("trigger_type")
    @classmethod
    def trigger_type_known(cls, v: str) -> str:
        # Sanity: known Salesforce trigger types. Reject typos at the
        # boundary. Add new values as Salesforce introduces them.
        allowed = {"BeforeSave", "AfterSave", "BeforeDelete", "AfterDelete"}
        if v not in allowed:
            raise ValueError(
                f"trigger_type {v!r} not recognized. Known: {sorted(allowed)}"
            )
        return v


# ----------------------------------------------------------------------
# BEHAVIOR.REFERENCES — ValidationRule -> Field
# ----------------------------------------------------------------------

class ReferencesProperties(_EdgeProperties):
    """Properties for ValidationRule REFERENCES Field.

    A validation rule's formula references fields. Tier 1 records each
    reference as an edge so impact analysis ('what tests are affected
    if Field X changes') can traverse from Field -> ValidationRule
    via REFERENCES (inbound).

    reference_type distinguishes how the field is used: 'read' (formula
    reads the value), 'priorvalue' (PRIORVALUE() function), 'ischanged'
    (ISCHANGED() function), 'isnew' (ISNEW() function — actually
    referenceless but listed for symmetry; emit 'isnew' edges from
    NEW.* references in the formula text).

    The is_priorvalue / is_ischanged / is_isnew booleans are convenient
    redundant flags that let traversal queries filter without parsing
    reference_type. They are mutually exclusive: at most one is True.
    """
    reference_type: str = Field(..., min_length=1, max_length=20)
    is_priorvalue: bool = False
    is_ischanged: bool = False
    is_isnew: bool = False

    @field_validator("reference_type")
    @classmethod
    def reference_type_known(cls, v: str) -> str:
        allowed = {"read", "priorvalue", "ischanged", "isnew"}
        if v not in allowed:
            raise ValueError(
                f"reference_type {v!r} not recognized. Known: {sorted(allowed)}"
            )
        return v


# ----------------------------------------------------------------------
# Registry: TIER_1_EDGES
# ----------------------------------------------------------------------

class EdgeTypeMetadata(BaseModel):
    """Metadata for one edge_type in the Tier 1 registry.

    - category: STRUCTURAL | CONFIG | PERMISSION | BEHAVIOR (matches edges.edge_category)
    - source_entity_types: which entity_types may appear as source_entity_id
    - target_entity_types: which entity_types may appear as target_entity_id
    - properties_schema: Pydantic class for properties JSONB, or None if no properties
    - derived_from_column: True if the sync engine auto-generates this edge
      from a detail-table column rather than writing it independently
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    category: str
    source_entity_types: tuple[str, ...]
    target_entity_types: tuple[str, ...]
    properties_schema: Optional[Type[BaseModel]] = None
    derived_from_column: bool = False


TIER_1_EDGES: dict[str, EdgeTypeMetadata] = {
    # ---- STRUCTURAL (2) ----
    "BELONGS_TO": EdgeTypeMetadata(
        category="STRUCTURAL",
        source_entity_types=(
            "Field", "RecordType", "ValidationRule", "Layout",
        ),
        target_entity_types=("Object",),
        properties_schema=None,
        derived_from_column=True,
    ),
    "HAS_RELATIONSHIP_TO": EdgeTypeMetadata(
        category="STRUCTURAL",
        source_entity_types=("Field",),  # lookup or master-detail field
        target_entity_types=("Object",),
        properties_schema=None,
        derived_from_column=True,
    ),

    # ---- CONFIG (4) ----
    "INCLUDES_FIELD": EdgeTypeMetadata(
        category="CONFIG",
        source_entity_types=("Layout",),
        target_entity_types=("Field",),
        properties_schema=IncludesFieldProperties,
        derived_from_column=False,
    ),
    "ASSIGNED_TO_PROFILE_RECORDTYPE": EdgeTypeMetadata(
        category="CONFIG",
        source_entity_types=("Layout",),
        target_entity_types=("Profile",),
        properties_schema=AssignedToProfileRecordtypeProperties,
        derived_from_column=False,
    ),
    "CONSTRAINS_PICKLIST_VALUES": EdgeTypeMetadata(
        category="CONFIG",
        source_entity_types=("RecordType",),
        target_entity_types=("PicklistValueSet",),
        properties_schema=None,
        derived_from_column=True,
    ),
    "HAS_PICKLIST_VALUES": EdgeTypeMetadata(
        category="CONFIG",
        source_entity_types=("Field",),
        target_entity_types=("PicklistValueSet",),
        properties_schema=None,
        derived_from_column=True,
    ),

    # ---- PERMISSION (5) ----
    "GRANTS_OBJECT_ACCESS": EdgeTypeMetadata(
        category="PERMISSION",
        source_entity_types=("Profile", "PermissionSet"),
        target_entity_types=("Object",),
        properties_schema=GrantsObjectAccessProperties,
        derived_from_column=False,
    ),
    "GRANTS_FIELD_ACCESS": EdgeTypeMetadata(
        category="PERMISSION",
        source_entity_types=("Profile", "PermissionSet"),
        target_entity_types=("Field",),
        properties_schema=GrantsFieldAccessProperties,
        derived_from_column=False,
    ),
    "INHERITS_PERMISSION_SET": EdgeTypeMetadata(
        category="PERMISSION",
        source_entity_types=("PermissionSet",),
        target_entity_types=("PermissionSet",),
        properties_schema=None,
        derived_from_column=False,
    ),
    "HAS_PROFILE": EdgeTypeMetadata(
        category="PERMISSION",
        source_entity_types=("User",),
        target_entity_types=("Profile",),
        properties_schema=None,
        derived_from_column=True,
    ),
    "HAS_PERMISSION_SET": EdgeTypeMetadata(
        category="PERMISSION",
        source_entity_types=("User",),
        target_entity_types=("PermissionSet",),
        properties_schema=HasPermissionSetProperties,
        derived_from_column=False,
    ),

    # ---- BEHAVIOR (3) ----
    "TRIGGERS_ON": EdgeTypeMetadata(
        category="BEHAVIOR",
        source_entity_types=("Flow",),
        target_entity_types=("Object",),
        properties_schema=TriggersOnProperties,
        derived_from_column=True,
    ),
    "APPLIES_TO": EdgeTypeMetadata(
        category="BEHAVIOR",
        source_entity_types=("ValidationRule",),
        target_entity_types=("Object",),
        properties_schema=None,
        derived_from_column=True,
    ),
    "REFERENCES": EdgeTypeMetadata(
        category="BEHAVIOR",
        source_entity_types=("ValidationRule",),
        target_entity_types=("Field",),
        properties_schema=ReferencesProperties,
        derived_from_column=True,
    ),
}


# ----------------------------------------------------------------------
# Public helpers
# ----------------------------------------------------------------------

KNOWN_EDGE_CATEGORIES = ("STRUCTURAL", "CONFIG", "PERMISSION", "BEHAVIOR")


def get_edge_metadata(edge_type: str) -> EdgeTypeMetadata:
    """Look up registry metadata for an edge_type. Raises KeyError on unknown."""
    if edge_type not in TIER_1_EDGES:
        raise KeyError(
            f"Unknown edge_type {edge_type!r}. Known types: "
            f"{sorted(TIER_1_EDGES.keys())}"
        )
    return TIER_1_EDGES[edge_type]


def validate_edge_properties(edge_type: str, properties: dict) -> dict:
    """Validate a properties dict against the edge_type's schema.

    Returns a dict ready for INSERT into edges.properties JSONB. The returned
    dict is the result of round-tripping through the Pydantic model — so any
    defaults are filled in, any normalizations are applied.

    Raises:
      KeyError if edge_type is unknown.
      pydantic.ValidationError if properties don't match the schema.
      ValueError if the edge_type has no schema (properties_schema=None) and
        the caller passed a non-empty dict — empty {} is the only valid value.

    Edge types without a properties schema (BELONGS_TO, HAS_PROFILE, etc.)
    accept ONLY the empty dict. We don't silently strip extra keys.
    """
    meta = get_edge_metadata(edge_type)

    if meta.properties_schema is None:
        if properties:
            raise ValueError(
                f"edge_type {edge_type!r} has no properties schema; "
                f"properties must be empty dict, got {properties!r}"
            )
        return {}

    # Pydantic v2: parse and dump. mode='json' produces a JSON-serializable
    # dict (UUIDs as strings, dates as ISO strings) ready for JSONB.
    instance = meta.properties_schema(**properties)
    return instance.model_dump(mode="json")


def edge_types_in_category(category: str) -> list[str]:
    """Return all edge_types belonging to a given category."""
    if category not in KNOWN_EDGE_CATEGORIES:
        raise ValueError(
            f"Unknown edge_category {category!r}. Known: {KNOWN_EDGE_CATEGORIES}"
        )
    return sorted(
        et for et, meta in TIER_1_EDGES.items()
        if meta.category == category
    )

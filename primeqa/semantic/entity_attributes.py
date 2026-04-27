"""Substrate 1 — Tier 1 entity attribute schemas (per D-025).

Per D-016: JSONB validation lives at the application layer. The DB-level
CHECK on entities.attributes only enforces `jsonb_typeof = 'object'`.
Per-entity-type structure is enforced here via Pydantic v2.

Per D-025: detail tables capture hot columns (queryable, filterable,
joinable across entities); entities.attributes JSONB carries sparse
metadata (accessed by name from a single entity, not queried across
the population). This file holds one Pydantic class per entity_type
defining the JSONB structure.

The TIER_1_ENTITIES registry maps entity_type -> metadata so the sync
engine and query layer can look up the right schema and detail table
without if/elif chains. Mirrors the TIER_1_EDGES pattern in edges.py.

Phase 1 grows this file incrementally as detail tables ship. Today:
ObjectAttributes only (paired with object_details). Tomorrow's
field_details migration will add FieldAttributes here. And so on through
the 10 Tier 1 entity types.
"""

from __future__ import annotations

from typing import Optional, Type

from pydantic import BaseModel, ConfigDict, Field


# ----------------------------------------------------------------------
# Common base — strict, frozen, no extras
# ----------------------------------------------------------------------

class _EntityAttributes(BaseModel):
    """Common config for all entity-attribute schemas.

    Same discipline as _EdgeProperties in edges.py:
      - frozen: attributes are immutable after construction. Once a Salesforce
        Describe response is parsed and validated, the dict is write-once
        on the way to entities.attributes JSONB.
      - extra='forbid': reject unknown keys at the boundary. If sync code
        adds a typo'd attribute name, fail loud rather than silently land
        garbage in JSONB.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")


# ----------------------------------------------------------------------
# ObjectAttributes — sparse metadata for entity_type='Object'
# ----------------------------------------------------------------------

class ObjectAttributes(_EntityAttributes):
    """Sparse Object metadata living in entities.attributes JSONB.

    Per D-025: hot Object attributes (key_prefix, is_custom, the four CRUD
    flags) are columns on object_details. The remaining DescribeSObjectResult
    fields land here.

    Defaults reflect Salesforce realism: most objects are retrievable
    (you can fetch them by ID), few are mergeable/feed-enabled/history-tracked.
    Real values come from the sync engine reading DescribeSObjectResult; the
    defaults exist only so partial dicts validate during testing or for
    edge-case entities (e.g., virtual objects) where some fields are
    legitimately absent.

    Promotion rule (D-018, D-025): if any of these attributes starts being
    queried, filtered, or joined across entities by application code, it
    is promoted to a column on object_details in a follow-up migration. The
    JSONB is for sparse access by attribute name, not for cross-entity queries.
    """
    is_searchable: bool = False
    is_layoutable: bool = False
    is_mergeable: bool = False
    is_replicable: bool = False
    is_retrievable: bool = True
    is_undeletable: bool = False
    is_feed_enabled: bool = False
    is_history_tracked: bool = False
    plural_label: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4000)


# ----------------------------------------------------------------------
# Registry: TIER_1_ENTITIES
# ----------------------------------------------------------------------

class EntityTypeMetadata(BaseModel):
    """Metadata for one entity_type in the Tier 1 registry.

    - attributes_schema: Pydantic class for entities.attributes JSONB
    - detail_table: name of the per-type detail table holding hot columns

    Future fields might include: source SObject for sync (e.g., 'EntityDefinition'
    for Object), default sync frequency tier, capability_level requirement.
    Keeping minimal for now.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    attributes_schema: Type[BaseModel]
    detail_table: str


TIER_1_ENTITIES: dict[str, EntityTypeMetadata] = {
    "Object": EntityTypeMetadata(
        attributes_schema=ObjectAttributes,
        detail_table="object_details",
    ),
    # Future entity types added here as detail tables ship:
    # "Field":          EntityTypeMetadata(attributes_schema=FieldAttributes,         detail_table="field_details"),
    # "RecordType":     EntityTypeMetadata(attributes_schema=RecordTypeAttributes,    detail_table="record_type_details"),
    # "Layout":         EntityTypeMetadata(attributes_schema=LayoutAttributes,        detail_table="layout_details"),
    # "ValidationRule": EntityTypeMetadata(attributes_schema=ValidationRuleAttributes, detail_table="validation_rule_details"),
    # "Flow":           EntityTypeMetadata(attributes_schema=FlowAttributes,          detail_table="flow_details"),
    # "Profile":        EntityTypeMetadata(attributes_schema=ProfileAttributes,       detail_table="profile_details"),
    # "PermissionSet":  EntityTypeMetadata(attributes_schema=PermissionSetAttributes, detail_table="permission_set_details"),
    # "User":           EntityTypeMetadata(attributes_schema=UserAttributes,          detail_table="user_details"),
    # "PicklistValueSet": EntityTypeMetadata(attributes_schema=PicklistValueSetAttributes, detail_table="picklist_value_details"),
}


# ----------------------------------------------------------------------
# Public helpers
# ----------------------------------------------------------------------

def get_entity_metadata(entity_type: str) -> EntityTypeMetadata:
    """Look up registry metadata for an entity_type. Raises KeyError on unknown."""
    if entity_type not in TIER_1_ENTITIES:
        raise KeyError(
            f"Unknown entity_type {entity_type!r}. Known types: "
            f"{sorted(TIER_1_ENTITIES.keys())}"
        )
    return TIER_1_ENTITIES[entity_type]


def validate_entity_attributes(entity_type: str, attributes: dict) -> dict:
    """Validate an attributes dict against the entity_type's schema.

    Returns a dict ready for INSERT into entities.attributes JSONB.
    Defaults are filled in; values are normalized (e.g., None -> default).

    Raises:
      KeyError if entity_type is unknown.
      pydantic.ValidationError if attributes don't match the schema.

    Empty dict is valid for any entity_type — all fields have defaults.
    """
    meta = get_entity_metadata(entity_type)
    instance = meta.attributes_schema(**attributes)
    # mode='json' produces a JSON-serializable dict (ready for JSONB).
    return instance.model_dump(mode="json")

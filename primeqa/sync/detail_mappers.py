"""Per-entity-type mappers from normalized payload → detail-table row dict.

Each mapper returns a dict of {column_name: value} suitable for
multi-row INSERT into the entity_type's detail table. The mapper
receives:
  - normalized: the substrate-1 normalize output (post-_strip_volatile)
  - entity_id: the entity row's UUID (populated AFTER entity INSERT)
  - parent_resolver: callable for resolving FK references to other
    entities (signature: parent_resolver(entity_type=..., external_id=...)
    → entity_id or None)

Per corrections-log §7 pattern: this is a parallel registry alongside
_PRESENTATION_FUNCTIONS, _extract_external_id, PHASE_REGISTRY. Add an
entry when implementing each entity type's phase.

The detail-table NAME is sourced from substrate-1's existing
TIER_1_ENTITIES registry (entity_attributes.py); this module only
holds the per-type column mapping logic. Single-source-of-truth
discipline.

PicklistValueSet is intentionally absent — substrate-1's design (per
PicklistValueAttributes docstring) is that PVS has no per-row hot
detail columns; its full shape lives in entities.attributes JSONB.
The TIER_1_ENTITIES registry confirms this (no PicklistValueSet entry).

This module exposes ONLY mapper-function references via
_DETAIL_TABLE_MAPPERS. The materialize layer dispatches via
get_detail_mapper(entity_type) which returns either:
  (detail_table_name, mapper_fn)   — entity_type has a detail table
  None                              — entity_type has no detail table
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from primeqa.semantic.entity_attributes import TIER_1_ENTITIES


# Mapper signature:
#   (normalized: dict, entity_id: str, parent_resolver: Callable)
#     -> dict[str, Any]
# Return dict's keys must match the target detail table's columns.
DetailMapper = Callable[..., dict[str, Any]]


def _map_object_details(
    normalized: dict[str, Any],
    entity_id: str,
    parent_resolver: Callable[..., Optional[str]],
) -> dict[str, Any]:
    """Map normalized Object payload → object_details row.

    Salesforce describe → normalize preserves camelCase shape.
    Maps the hot-column subset per substrate-1's D-025 design:
      keyPrefix     → key_prefix
      custom        → is_custom (default False)
      queryable     → is_queryable (default True per DB default)
      createable    → is_createable
      updateable    → is_updateable
      deletable     → is_deletable

    All booleans have DB defaults; if the normalized payload is
    missing one, we leave the DB to default. Missing keys → omit
    from returned dict so the column gets its DEFAULT value.
    """
    row: dict[str, Any] = {"entity_id": entity_id}
    # key_prefix is nullable — pass through (may be None)
    row["key_prefix"] = normalized.get("keyPrefix")
    # Boolean flags: only set if present in normalized payload, else
    # let DB default win. fetch_objects() reliably returns all 5,
    # so in practice these are always provided.
    for sf_key, db_col in (
        ("custom", "is_custom"),
        ("queryable", "is_queryable"),
        ("createable", "is_createable"),
        ("updateable", "is_updateable"),
        ("deletable", "is_deletable"),
    ):
        if sf_key in normalized:
            row[db_col] = bool(normalized[sf_key])
    return row


def _map_picklist_value_details(
    normalized: dict[str, Any],
    entity_id: str,
    parent_resolver: Callable[..., Optional[str]],
) -> dict[str, Any]:
    """Map normalized PicklistValue payload → picklist_value_details row.

    Live Salesforce shape (per survey of AccountType/Industry/
    OpportunityStage): {valueName, label, default, isActive,
    description, ...18 keys}. Substrate-1's _normalize_picklist_value
    is just _strip_volatile — preserves the raw shape.

    Column mapping:
      entity_id                    ← caller-supplied
      picklist_value_set_entity_id ← parent_resolver lookup
      value_label                  ← normalized['label']
      value_api_name               ← normalized['valueName']
      is_active                    ← normalized.get('isActive') is not False
                                     (None is treated as True — Salesforce
                                     returns null on standard value sets
                                     when isActive isn't explicitly set;
                                     the semantic default is active)
      is_default                   ← normalized.get('default', False)
      sort_order                   ← normalized.get('_sort_order', 0)
                                     (injected by phase_picklist_value
                                     from list-position index)

    Raises ValueError if:
      - _parent_external_id marker is missing (phase function bug)
      - parent_resolver returns None (parent PicklistValueSet not
        materialized — ENTITY_ORDER violation or empty parent table)
      - valueName or label is missing/empty (Salesforce data integrity
        issue, never observed in practice but failing loud is correct)
    """
    parent_ext = normalized.get("_parent_external_id")
    if not parent_ext:
        raise ValueError(
            f"PicklistValue normalized payload missing "
            f"'_parent_external_id' marker; phase_picklist_value "
            f"must inject this from the parent GVS/SVS record. "
            f"Got: {sorted(normalized.keys())}"
        )

    parent_entity_id = parent_resolver(
        entity_type="PicklistValueSet",
        external_id=parent_ext,
    )
    if parent_entity_id is None:
        raise ValueError(
            f"Cannot resolve parent PicklistValueSet "
            f"{parent_ext!r} for PicklistValue "
            f"{normalized.get('valueName')!r}. Parent must be "
            f"materialized before child (ENTITY_ORDER places "
            f"PicklistValueSet before PicklistValue)."
        )

    value_name = normalized.get("valueName")
    label = normalized.get("label")
    if not value_name:
        raise ValueError(
            f"PicklistValue requires non-empty 'valueName'; "
            f"got {value_name!r} in payload with parent {parent_ext!r}"
        )
    if not label:
        # Some SVSes have label=None for placeholder entries; substitute
        # valueName so the NOT NULL column is satisfied without losing
        # information. Lenient on label; strict on valueName.
        label = value_name

    return {
        "entity_id": entity_id,
        "picklist_value_set_entity_id": parent_entity_id,
        "value_label": label,
        "value_api_name": value_name,
        "is_active": normalized.get("isActive") is not False,
        "is_default": bool(normalized.get("default", False)),
        "sort_order": int(normalized.get("_sort_order", 0)),
    }


def _map_field_details(
    normalized: dict[str, Any],
    entity_id: str,
    parent_resolver: Callable[..., Optional[str]],
) -> dict[str, Any]:
    """Map normalized Field payload → field_details row.

    field_details has three FK columns to resolve via parent_resolver:
      object_entity_id              ← parent Object (always set; field
                                      can't exist without a parent
                                      Object). Resolved from
                                      _parent_object_api_name marker
                                      that phase_field injects.
      references_object_entity_id   ← the Object this field's
                                      reference points to (nullable).
                                      For reference-typed fields with
                                      referenceTo populated, takes the
                                      first target (substrate-1 detail
                                      column is singular UUID, not a
                                      list). Polymorphic refs' full
                                      multi-target graph lives in
                                      HAS_RELATIONSHIP_TO edges; this
                                      column captures only the
                                      primary target for hot-query use.
                                      If the target Object isn't
                                      materialized (e.g., filtered by
                                      Object phase's syncability
                                      filter), the column stays NULL
                                      — the edge resolution would
                                      also skip it, keeping data
                                      consistent.
      picklist_value_set_entity_id  ← always NULL this cycle. GVS
                                      reference detection requires
                                      Tooling+Metadata API
                                      (corrections-log §10); deferred.

    All booleans + INT length/precision/scale come from the raw
    describe per substrate-1's column-naming convention:
      is_custom        ← custom
      is_unique        ← unique
      is_external_id   ← externalId
      is_nillable      ← nillable
      is_calculated    ← calculated
      is_filterable    ← filterable
      is_sortable      ← sortable
      length/precision/scale pass through (nullable INTs)

    Raises ValueError if parent Object can't be resolved (ENTITY_ORDER
    places Object before Field, so this would indicate a serious bug
    — fail loud rather than write a NULL into a NOT NULL column).
    """
    parent_marker = normalized.get("_parent_object_api_name")
    if not parent_marker:
        raise ValueError(
            f"Field normalized payload missing "
            f"'_parent_object_api_name' marker; phase_field must "
            f"inject this. Got keys: {sorted(normalized.keys())}"
        )
    parent_object_id = parent_resolver(
        entity_type="Object",
        external_id=parent_marker,
    )
    if parent_object_id is None:
        raise ValueError(
            f"Cannot resolve parent Object {parent_marker!r} for "
            f"Field {normalized.get('name')!r}. Parent must be "
            f"materialized before child (ENTITY_ORDER places Object "
            f"before Field)."
        )

    # Reference target: only the first target lands in the detail
    # column; full set is in HAS_RELATIONSHIP_TO edges. Resolver
    # returns None if the target Object isn't in this org's synced
    # entity set (filtered or not-yet-synced) — that's OK; column
    # is nullable.
    ref_to_list = normalized.get("referenceTo") or []
    references_object_id: Optional[str] = None
    if ref_to_list:
        references_object_id = parent_resolver(
            entity_type="Object",
            external_id=ref_to_list[0],
        )

    return {
        "entity_id": entity_id,
        "object_entity_id": parent_object_id,
        "references_object_entity_id": references_object_id,
        "picklist_value_set_entity_id": None,
        "field_type": normalized.get("type"),
        "is_custom": bool(normalized.get("custom", False)),
        "is_unique": bool(normalized.get("unique", False)),
        "is_external_id": bool(normalized.get("externalId", False)),
        "is_nillable": bool(normalized.get("nillable", True)),
        "is_calculated": bool(normalized.get("calculated", False)),
        "is_filterable": bool(normalized.get("filterable", True)),
        "is_sortable": bool(normalized.get("sortable", True)),
        "length": normalized.get("length"),
        "precision": normalized.get("precision"),
        "scale": normalized.get("scale"),
    }


def _map_record_type_details(
    normalized: dict[str, Any],
    entity_id: str,
    parent_resolver: Callable[..., Optional[str]],
) -> dict[str, Any]:
    """Map normalized RecordType payload → record_type_details row.

    Hot columns per substrate-1's design (5 total; record_type_
    details is one of the cleanest detail tables):
      entity_id           ← caller-supplied
      object_entity_id    ← parent Object (NOT NULL FK; resolved
                            from _parent_object_api_name marker)
      is_active           ← Metadata.active (defaults True)
      is_master           ← derived: developerName == 'Master'
                            (Salesforce's implicit/master RT for
                            every Object; not the same as
                            "default" — that's a Profile-level
                            assignment carried elsewhere)
      created_at          ← DB DEFAULT now()

    Raises ValueError if:
      - _parent_object_api_name marker is missing (phase function
        bug — phase_record_type splits FullName to inject it)
      - parent_resolver returns None (parent Object not
        materialized — ENTITY_ORDER places Object well before
        RecordType, so this would signal a serious upstream
        problem)
    """
    parent_marker = normalized.get("_parent_object_api_name")
    if not parent_marker:
        raise ValueError(
            f"RecordType normalized payload missing "
            f"'_parent_object_api_name' marker; "
            f"phase_record_type must inject this. "
            f"Got keys: {sorted(normalized.keys())}"
        )
    parent_object_id = parent_resolver(
        entity_type="Object",
        external_id=parent_marker,
    )
    if parent_object_id is None:
        raise ValueError(
            f"Cannot resolve parent Object {parent_marker!r} for "
            f"RecordType {normalized.get('developerName')!r}. "
            f"Parent must be materialized before child "
            f"(ENTITY_ORDER places Object before RecordType)."
        )

    developer_name = normalized.get("developerName")
    return {
        "entity_id": entity_id,
        "object_entity_id": parent_object_id,
        "is_active": bool(normalized.get("active", True)),
        "is_master": developer_name == "Master",
    }


def _map_layout_details(
    normalized: dict[str, Any],
    entity_id: str,
    parent_resolver: Callable[..., Optional[str]],
) -> dict[str, Any]:
    """Map normalized Layout payload → layout_details row.

    Hot columns per substrate-1's design (5 total, mirroring
    record_type_details + 2 extras):
      entity_id           ← caller-supplied
      object_entity_id    ← parent Object (NOT NULL FK; resolved
                            from _parent_object_api_name marker)
      layout_type         ← _layout_type marker (Salesforce-
                            provided via Tooling Layout.LayoutType;
                            per §15)
      layout_api_name     ← _layout_name_resolved marker (Layout.Name
                            from Tooling SOQL second-pass; NOT NULL
                            no-default)
      is_active           ← always True (Salesforce doesn't expose
                            an inactive-layout concept via REST
                            describe/layouts; all returned layouts
                            are active by definition)

    Required markers (set by phase_layout):
    - _parent_object_api_name: parent Object QualifiedApiName
    - _layout_type: from Tooling Layout.LayoutType
      (e.g., "Standard")
    - _layout_name_resolved: from Tooling Layout.Name
      (e.g., "Account Layout")
    """
    parent_marker = normalized.get("_parent_object_api_name")
    if not parent_marker:
        raise ValueError(
            f"Layout normalized payload missing "
            f"'_parent_object_api_name' marker; phase_layout must "
            f"inject this. Got keys: {sorted(normalized.keys())}"
        )
    parent_object_id = parent_resolver(
        entity_type="Object",
        external_id=parent_marker,
    )
    if parent_object_id is None:
        raise ValueError(
            f"Cannot resolve parent Object {parent_marker!r} for "
            f"Layout {normalized.get('_layout_full_name')!r}. "
            f"Parent must be materialized before child "
            f"(ENTITY_ORDER places Object before Layout)."
        )

    layout_type = normalized.get("_layout_type")
    layout_name = normalized.get("_layout_name_resolved")
    if not layout_type:
        raise ValueError(
            f"Layout requires '_layout_type' marker (Salesforce "
            f"Tooling Layout.LayoutType value per §15); got "
            f"keys: {sorted(normalized.keys())}"
        )
    if not layout_name:
        raise ValueError(
            f"Layout requires '_layout_name_resolved' marker "
            f"(Tooling Layout.Name from fetch_layout_names()); "
            f"got keys: {sorted(normalized.keys())}"
        )

    return {
        "entity_id": entity_id,
        "object_entity_id": parent_object_id,
        "layout_type": layout_type,
        "layout_api_name": layout_name,
        "is_active": True,
    }


def _map_validation_rule_details(
    normalized: dict[str, Any],
    entity_id: str,
    parent_resolver: Callable[..., Optional[str]],
) -> dict[str, Any]:
    """Map normalized ValidationRule payload → validation_rule_details
    row.

    Sparse schema (only 3 NOT NULL columns the mapper sets; 5
    AI-enrichment columns are populated by Phase 5 enrichment worker
    NOT by sync layer):
      entity_id            ← caller-supplied
      object_entity_id     ← parent Object (NOT NULL FK; resolved
                             from _parent_object_api_name marker)
      is_active            ← Active (Tooling top-level; defaults
                             True if missing)

    Detail columns NOT set by this mapper (populated downstream):
      plain_english_summary   ← Phase 5 enrichment worker
      summary_model           ← Phase 5
      summary_prompt_version  ← Phase 5
      summary_generated_at    ← Phase 5
      summary_embedding       ← Phase 5 (pgvector)

    The validation rule's formula text + error message LIVE in
    entities.attributes JSONB via the normalize/_strip_volatile
    flow — not in detail. Substrate-1's design: the
    high-signal-for-attribution content stays in the JSONB column
    where semantic_text generation can read it.
    """
    parent_marker = normalized.get("_parent_object_api_name")
    if not parent_marker:
        raise ValueError(
            f"ValidationRule normalized payload missing "
            f"'_parent_object_api_name' marker; "
            f"phase_validation_rule must inject this. "
            f"Got keys: {sorted(normalized.keys())}"
        )
    parent_object_id = parent_resolver(
        entity_type="Object",
        external_id=parent_marker,
    )
    if parent_object_id is None:
        raise ValueError(
            f"Cannot resolve parent Object {parent_marker!r} for "
            f"ValidationRule {normalized.get('ValidationName')!r}. "
            f"Parent must be materialized before child "
            f"(ENTITY_ORDER places Object before ValidationRule)."
        )

    return {
        "entity_id": entity_id,
        "object_entity_id": parent_object_id,
        "is_active": bool(normalized.get("Active", True)),
    }


def _map_profile_details(
    normalized: dict[str, Any],
    entity_id: str,
    parent_resolver: Callable[..., Optional[str]],
) -> dict[str, Any]:
    """Map normalized Profile payload → profile_details row.

    Profile is ORG-LEVEL (not child-of-Object). No parent FK to
    resolve. parent_resolver is unused by this mapper — kept in
    signature for registry uniformity.

    Schema (4 hot columns + 5 AI-enrichment cols filled by Phase 5):
      entity_id            ← caller-supplied
      is_active            ← always True (Profiles returned by
                             Tooling are by definition active in
                             the org; there's no Tooling field
                             marking a Profile as inactive)
      is_custom            ← Metadata.custom (boolean; standard
                             Salesforce Profiles have custom=False;
                             user-created Profiles have custom=True)
      user_license_type    ← Metadata.userLicense (NOT NULL, no
                             DB default — every Profile is tied
                             to a Salesforce user license tier:
                             'Salesforce', 'Salesforce Platform',
                             'Analytics Cloud Integration User',
                             etc.)

    NOT NULL no-default column: user_license_type. If missing
    from Metadata (shouldn't happen — every Salesforce Profile
    has a userLicense), fail loud with a clear message.
    """
    metadata = normalized.get("Metadata") or {}
    user_license = metadata.get("userLicense")
    if not user_license:
        raise ValueError(
            f"Profile normalized payload missing Metadata.userLicense "
            f"(required by profile_details.user_license_type NOT "
            f"NULL); Profile name={normalized.get('Name')!r}, keys="
            f"{sorted(metadata.keys())}"
        )
    return {
        "entity_id": entity_id,
        "is_active": True,
        "is_custom": bool(metadata.get("custom", False)),
        "user_license_type": user_license,
    }


def _map_permission_set_details(
    normalized: dict[str, Any],
    entity_id: str,
    parent_resolver: Callable[..., Optional[str]],
) -> dict[str, Any]:
    """Map normalized PermissionSet (post-join, post-license-resolution)
    → permission_set_details row.

    PermissionSet is ORG-LEVEL (not child-of-Object). No parent FK to
    resolve. parent_resolver is unused — kept in signature for registry
    uniformity.

    Schema (3 NOT NULL columns; permission_set_details has no
    AI-enrichment columns this cycle):
      entity_id     ← caller-supplied
      is_custom     ← IsCustom from PS row (standard Salesforce PSes
                      have IsCustom=False; user-created PSes have
                      IsCustom=True)
      license_type  ← _license_label injected by phase_permission_set
                      via license_id_to_label resolution. Falls back
                      to '(no license)' sentinel for PSes with null
                      LicenseId or LicenseId unmappable from the
                      fetched PermissionSetLicense rows. Sentinel
                      satisfies NOT NULL constraint without losing
                      information.

    NOT NULL no-default column: license_type. The phase function
    enforces resolution to a non-empty string before this mapper
    runs (the sentinel ensures we never write NULL).
    """
    license_label = normalized.get("_license_label")
    if not license_label:
        # Defensive — phase function should always set this before
        # calling materialize. If it's missing/empty, fall back to
        # the sentinel rather than failing the whole sync.
        license_label = "(no license)"
    return {
        "entity_id": entity_id,
        "is_custom": bool(normalized.get("IsCustom", False)),
        "license_type": license_label,
    }


_DETAIL_TABLE_MAPPERS: dict[str, DetailMapper] = {
    "Object": _map_object_details,
    "PicklistValue": _map_picklist_value_details,
    "Field": _map_field_details,
    "RecordType": _map_record_type_details,
    "Layout": _map_layout_details,
    "ValidationRule": _map_validation_rule_details,
    "Profile": _map_profile_details,
    "PermissionSet": _map_permission_set_details,
    # Other entity types added by their respective phase cycles.
    # PicklistValueSet intentionally absent — no detail table.
}


def get_detail_mapper(
    entity_type: str,
) -> Optional[tuple[str, DetailMapper]]:
    """Return (detail_table_name, mapper_fn) for an entity type.

    Returns None when:
      - entity_type isn't registered in _DETAIL_TABLE_MAPPERS
        (no detail-table writes for this type), OR
      - entity_type isn't in TIER_1_ENTITIES (no detail_table
        attribute available — defensive guard against drift
        between the two registries).

    Use case: materialize layer calls this once per chunk to
    decide whether to invoke detail-row writes.
    """
    mapper = _DETAIL_TABLE_MAPPERS.get(entity_type)
    if mapper is None:
        return None
    entity_meta = TIER_1_ENTITIES.get(entity_type)
    if entity_meta is None:
        return None
    return (entity_meta.detail_table, mapper)

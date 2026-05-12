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


_DETAIL_TABLE_MAPPERS: dict[str, DetailMapper] = {
    "Object": _map_object_details,
    "PicklistValue": _map_picklist_value_details,
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

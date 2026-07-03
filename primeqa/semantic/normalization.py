"""Substrate 1 Phase 2 — per-entity-type normalization + hashing (per D-035).

D-035: phantom-change prevention. Salesforce describe payloads carry
volatile metadata (URLs, timestamps, audit IDs) and unstable orderings
that change between snapshots without semantic meaning. Hashing the raw
response would produce phantom changes — entities reported as "modified"
when nothing meaningful changed.

This module enforces a deterministic normalization step BEFORE hashing.
The hash of a normalized dict is the canonical change-detection signal
for sync's hash-equal short-circuit (entities_unchanged path).

Public API:
  normalize(entity_type, raw_data) -> dict
  hash_normalized(normalized) -> str (SHA-256 hex)

normalize() does NOT consult TIER_1_ENTITIES from entity_attributes.py.
That registry tracks which entity types have Pydantic sparse-attribute
schemas (10 types). normalize() covers all entity types Phase 2 sync
writes (11 types — adds PicklistValueSet as parent container for
PicklistValues, schema-less).

The two registries answer different questions and intentionally diverge.
See PHASE_2_PLAN_corrections.md for the design note.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


# ----------------------------------------------------------------------
# Universal transformations (apply to every entity type)
# ----------------------------------------------------------------------

# Top-level keys to strip from any describe payload. These are runtime
# metadata, not semantic content.
_VOLATILE_KEYS: frozenset[str] = frozenset({
    "urls",                # Salesforce runtime endpoint dict
    "attributes",          # SObject envelope: {"type": "...", "url": "..."}
    "LastModifiedDate",
    "CreatedDate",
    "SystemModstamp",
    "LastModifiedById",
    "CreatedById",
    "OwnerId",
    "IsDeleted",
})


def _normalize_value(v: Any) -> Any:
    """Recursive value normalization:
    - Whitespace-only strings -> None
    - Dicts -> recurse with _strip_volatile (drops volatile keys)
    - Lists -> recurse on each element
    - Other primitives -> return as-is
    """
    if isinstance(v, str):
        if v.strip() == "":
            return None
        return v
    if isinstance(v, dict):
        return _strip_volatile(v)
    if isinstance(v, list):
        return [_normalize_value(item) for item in v]
    return v


def _strip_volatile(d: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with volatile top-level keys removed and
    remaining values recursively normalized."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if k in _VOLATILE_KEYS:
            continue
        result[k] = _normalize_value(v)
    return result


def _sort_list_of_dicts(
    d: dict[str, Any], list_key: str, sort_keys: list[str]
) -> None:
    """Sort d[list_key] in place by the tuple of values from sort_keys.
    Missing keys treated as the empty string for stable comparison.
    No-op if the field is missing, not a list, empty, or contains
    non-dict elements."""
    items = d.get(list_key)
    if not isinstance(items, list) or not items:
        return
    if not all(isinstance(item, dict) for item in items):
        return
    items.sort(key=lambda item: tuple(str(item.get(k, "")) for k in sort_keys))


def _sort_string_list(d: dict[str, Any], list_key: str) -> None:
    """Sort d[list_key] in place if it's a list of strings.
    No-op if missing, not a list, or contains non-string items."""
    items = d.get(list_key)
    if not isinstance(items, list) or not items:
        return
    if not all(isinstance(item, str) for item in items):
        return
    d[list_key] = sorted(items)


# ----------------------------------------------------------------------
# Per-entity-type normalizers
# ----------------------------------------------------------------------
# Each function:
#   1. Calls _strip_volatile (universal cleanup)
#   2. Sorts type-specific lists-of-dicts by stable keys
#   3. Returns the normalized dict


def _normalize_object(raw: dict[str, Any]) -> dict[str, Any]:
    n = _strip_volatile(raw)
    _sort_list_of_dicts(n, "fields", ["name"])
    _sort_list_of_dicts(n, "recordTypeInfos", ["developerName"])
    _sort_list_of_dicts(n, "childRelationships",
                        ["relationshipName", "childSObject"])
    return n


def _normalize_field(raw: dict[str, Any]) -> dict[str, Any]:
    n = _strip_volatile(raw)
    _sort_list_of_dicts(n, "picklistValues", ["value"])
    _sort_string_list(n, "referenceTo")
    return n


def _normalize_record_type(raw: dict[str, Any]) -> dict[str, Any]:
    n = _strip_volatile(raw)
    _sort_list_of_dicts(n, "picklistValues", ["picklistName"])
    return n


def _normalize_layout(raw: dict[str, Any]) -> dict[str, Any]:
    n = _strip_volatile(raw)
    _sort_list_of_dicts(n, "layoutSections", ["index", "name"])
    sections = n.get("layoutSections")
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict):
                _sort_list_of_dicts(section, "layoutItems", ["position", "label"])
    return n


def _normalize_picklist_value_set(raw: dict[str, Any]) -> dict[str, Any]:
    """No list-shaped collections of significance at the PVS level —
    children (PicklistValues) are separate entities. Smallest normalizer."""
    n = _strip_volatile(raw)
    _sort_list_of_dicts(n, "customValue", ["valueName"])
    return n


def _normalize_picklist_value(raw: dict[str, Any]) -> dict[str, Any]:
    """No list-shaped fields of significance."""
    return _strip_volatile(raw)


def _normalize_profile(raw: dict[str, Any]) -> dict[str, Any]:
    n = _strip_volatile(raw)
    _sort_list_of_dicts(n, "objectPermissions", ["object", "SobjectType"])
    _sort_list_of_dicts(n, "fieldPermissions", ["field"])
    _sort_list_of_dicts(n, "userPermissions", ["name"])
    return n


def _normalize_permission_set(raw: dict[str, Any]) -> dict[str, Any]:
    """Same shape as Profile."""
    n = _strip_volatile(raw)
    _sort_list_of_dicts(n, "objectPermissions", ["object", "SobjectType"])
    _sort_list_of_dicts(n, "fieldPermissions", ["field"])
    _sort_list_of_dicts(n, "userPermissions", ["name"])
    return n


def _normalize_user(raw: dict[str, Any]) -> dict[str, Any]:
    """No list-shaped fields of significance."""
    return _strip_volatile(raw)


def _normalize_validation_rule(raw: dict[str, Any]) -> dict[str, Any]:
    """No list-shaped fields of significance at top level."""
    return _strip_volatile(raw)


def _normalize_flow(raw: dict[str, Any]) -> dict[str, Any]:
    n = _strip_volatile(raw)
    for list_key in (
        "decisions",
        "assignments",
        "actionCalls",
        "recordCreates",
        "recordUpdates",
        "recordLookups",
        "waits",
    ):
        _sort_list_of_dicts(n, list_key, ["name"])
    return n


def _normalize_approval_process(raw: dict[str, Any]) -> dict[str, Any]:
    """D-308: ProcessDefinition rows are flat (no nested Metadata) —
    volatile-strip is the whole normalization."""
    return _strip_volatile(raw)


# ----------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------

_NORMALIZERS: dict[str, Any] = {
    "Object":           _normalize_object,
    "Field":            _normalize_field,
    "RecordType":       _normalize_record_type,
    "Layout":           _normalize_layout,
    "PicklistValueSet": _normalize_picklist_value_set,
    "PicklistValue":    _normalize_picklist_value,
    "Profile":          _normalize_profile,
    "PermissionSet":    _normalize_permission_set,
    "User":             _normalize_user,
    "ValidationRule":   _normalize_validation_rule,
    "Flow":             _normalize_flow,
    "ApprovalProcess":  _normalize_approval_process,
}


def normalize(entity_type: str, raw_data: dict[str, Any]) -> dict[str, Any]:
    """Dispatch to the type-specific normalizer.

    Raises ValueError if entity_type is unknown.
    """
    fn = _NORMALIZERS.get(entity_type)
    if fn is None:
        raise ValueError(
            f"Unknown entity_type: {entity_type!r}. "
            f"Known types: {sorted(_NORMALIZERS.keys())}"
        )
    return fn(raw_data)


# ----------------------------------------------------------------------
# Hashing
# ----------------------------------------------------------------------

def hash_normalized(normalized: dict[str, Any]) -> str:
    """Return SHA-256 hex digest of canonical JSON serialization.

    Canonical form:
      - sort_keys=True (key order doesn't affect hash)
      - separators=(',', ':') (no whitespace ambiguity)
      - ensure_ascii=True (no encoding ambiguity)
      - default=str (datetimes etc. coerced to string repr)

    The output is a 64-character lowercase hex string suitable for
    storage in entities.last_seed_hash (VARCHAR(64)).
    """
    canonical = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

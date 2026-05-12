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

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class EdgeSpec:
    """Declarative spec for one edge type written from a source entity.

    Attributes:
        target_entity_type: which entity_type the edge points at
            (e.g., 'Object' for Field.BELONGS_TO)
        edge_type: the TIER_1_EDGES key (e.g., 'BELONGS_TO',
            'HAS_RELATIONSHIP_TO')
        extract_target_external_ids: callable that receives the source
            entity's normalized payload and returns a list of target
            external_ids. Returning an empty list means "this source
            entity has no edges of this type" — common for spec-by-
            spec conditional edges (e.g., HAS_RELATIONSHIP_TO only
            applies to reference-typed Fields, which is encoded by
            the extractor returning [] for non-reference fields).
    """
    target_entity_type: str
    edge_type: str
    extract_target_external_ids: Callable[[dict], list[str]]


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

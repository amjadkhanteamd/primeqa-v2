"""Presentation-shape adapters mapping substrate-1's normalized
payloads to the shape semantic_text generators expect.

Per docs/architecture/substrate_1_semantic_org_model/
PHASE_2_PLAN_corrections.md §7 — three logical shapes exist:
raw SF (transport) → normalized (hashable canonical) →
presentation (snake_case, enriched, semantic-text-ready).
Substrate-1 built the first two transforms; this module
builds the third.

Per-type adapters mirror the parallel registries pattern from
normalization.py and semantic_text.py. When adding a new entity
type to ENTITY_ORDER, extend _PRESENTATION_FUNCTIONS in lock
step.
"""
from __future__ import annotations

from typing import Any, Callable


def _to_presentation_object(normalized: dict[str, Any]) -> dict[str, Any]:
    """Map normalized Object payload to semantic_text input shape.

    Salesforce describe → normalize preserves camelCase
    ('custom', 'keyPrefix', 'customSetting'); semantic_text
    expects snake_case ('is_custom', etc.) and some derived
    fields not in the raw describe ('description',
    'key_field_names').

    Object semantic_text at the Object phase reflects what's
    available NOW: name, label, is_custom. Description is
    typically NULL on Salesforce describe responses (it's
    object-level metadata that customers rarely populate);
    empty/default value is acceptable. key_field_names would
    require Field-phase data which hasn't run yet; empty
    default is also acceptable here. Substrate-1's
    _to_text_object handles missing keys gracefully with
    "none listed" / "no description provided" defaults.

    Future cycles may add a post-phase semantic_text refresh
    step that re-generates Object semantic_text after Field
    phase to populate key_field_names. For now, partial
    enrichment is accepted per the async-enrichment philosophy.
    """
    return {
        "name": normalized.get("name"),
        "label": normalized.get("label"),
        "is_custom": normalized.get("custom", False),
        "description": normalized.get("description"),  # often None
        "key_field_names": None,  # populated by future refresh
    }


def _to_presentation_picklist_value_set(
    normalized: dict[str, Any],
) -> dict[str, Any]:
    """Map normalized GlobalValueSet to semantic_text input shape.

    Substrate-1 unifies GlobalValueSet + StandardValueSet under one
    PicklistValueSet entity_type (per corrections-log §8). This
    adapter handles the GVS source; an analogous SVS branch lands
    in the StandardValueSet phase cycle.

    Field mapping (verified against substrate-1's
    _picklist_value_set_fixture + _to_text_picklist_value_set):
        name              ← FullName (canonical identifier, handles
                            namespacing as <namespace>__<dev_name>)
        label             ← MasterLabel (display name)
        is_restricted     = True (hardcoded — see rationale below)
        is_global_value_set = True (GVS source, always)
        description       ← Description (may be None)

    is_restricted hardcoded True rationale: GlobalValueSets are
    generally restricted by design (reusable canonical
    enumerations; the whole point is constraining values to a
    known list). Salesforce's Metadata API doesn't expose a clean
    isRestricted flag at the top level of the GVS record.
    Revisit if customers report semantic_text misrepresenting a
    GVS as restricted when it isn't — though I'd be surprised if
    that case is common enough to surface.
    """
    return {
        "name": normalized.get("FullName"),
        "label": normalized.get("MasterLabel"),
        "is_restricted": True,
        "is_global_value_set": True,
        "description": normalized.get("Description"),
    }


_PRESENTATION_FUNCTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "Object": _to_presentation_object,
    "PicklistValueSet": _to_presentation_picklist_value_set,
    # Other entity types added by their respective phase cycles per
    # PHASE_2_PLAN_corrections.md §7.
}


def to_presentation(
    entity_type: str, normalized: dict[str, Any],
) -> dict[str, Any]:
    """Route to per-type presentation adapter.

    Raises:
        KeyError: entity_type has no registered presentation adapter.
    """
    fn = _PRESENTATION_FUNCTIONS.get(entity_type)
    if fn is None:
        raise KeyError(
            f"No presentation adapter for entity_type {entity_type!r}. "
            f"Add to primeqa/sync/presentation.py "
            f"_PRESENTATION_FUNCTIONS registry."
        )
    return fn(normalized)

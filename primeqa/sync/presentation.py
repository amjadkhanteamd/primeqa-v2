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
    """Map normalized PicklistValueSet to semantic_text input shape.

    Substrate-1 unifies GlobalValueSet + StandardValueSet under one
    PicklistValueSet entity_type (per corrections-log §8). The
    `_source` marker on the normalized payload selects the branch:
    `'GlobalValueSet'` (default) or `'StandardValueSet'`.

    Field mapping (verified against substrate-1's
    _picklist_value_set_fixture + _to_text_picklist_value_set):
        name              ← FullName (canonical identifier, handles
                            namespacing as <namespace>__<dev_name>)
        label             ← MasterLabel (display name)
        is_restricted     = True (hardcoded — see rationale below)
        is_global_value_set ← True for GVS source, False for SVS
        description       ← Description (GVS only; SVS has no
                            Description in the Tooling response,
                            hardcoded to None)

    is_restricted hardcoded True rationale: Both GlobalValueSets and
    StandardValueSets are restricted by design (reusable canonical
    enumerations; the whole point is constraining values to a known
    list). Salesforce's Metadata API doesn't expose a clean
    isRestricted flag at the top level of the GVS record; SVSes
    are restricted by definition (they're the platform's canonical
    catalog).

    Defaults to the GVS branch when `_source` is absent — preserves
    backward compatibility with raw payloads that pre-date the
    `_source` marker (e.g., unit-test fixtures from the prior cycle).
    """
    source = normalized.get("_source", "GlobalValueSet")
    if source == "GlobalValueSet":
        return {
            "name": normalized.get("FullName"),
            "label": normalized.get("MasterLabel"),
            "is_restricted": True,
            "is_global_value_set": True,
            "description": normalized.get("Description"),
        }
    # StandardValueSet branch — FullName ≈ MasterLabel for catalog
    # entries; both surface in the Tooling response. Description is
    # not in the SVS SELECT clause (see fetch_standard_value_sets).
    return {
        "name": normalized.get("FullName"),
        "label": normalized.get("MasterLabel"),
        "is_restricted": True,
        "is_global_value_set": False,
        "description": None,
    }


def _to_presentation_picklist_value(
    normalized: dict[str, Any],
) -> dict[str, Any]:
    """Map normalized PicklistValue to semantic_text input shape.

    Live Salesforce shape from Tooling API customValue/standardValue
    entries (verified live against AccountType, Industry,
    OpportunityStage):
        valueName        — value identifier (e.g., 'Analyst')
        label            — display label (e.g., 'Analyst' or 'Account
                           Executive'); occasionally None on synthetic
                           catalog entries
        default          — bool, is this the default value?
        isActive         — tri-state: True / False / None.
                           Salesforce returns null when isActive isn't
                           explicitly set on a Standard value (default
                           semantic = active). Treat None as True.
        description      — usually None
        + 13 domain-specific keys (color, won, closed, forecastCategory,
          probability, ...) — not surfaced in semantic_text

    Substrate-1's _to_text_picklist_value reads {value, set_name,
    is_active, is_default}. set_name comes from the phase-injected
    _parent_external_id marker (matches the parent PicklistValueSet's
    external_id, which is itself namespaced with SVS: prefix for
    StandardValueSet sources).

    is_active is_not_False rather than truthy: this preserves the
    "None means active" Salesforce convention. Truthy on None would
    coerce to False, which inverts the semantic.
    """
    return {
        "value": normalized.get("valueName"),
        "set_name": normalized.get("_parent_external_id"),
        "is_active": normalized.get("isActive") is not False,
        "is_default": bool(normalized.get("default", False)),
    }


def _to_presentation_field(normalized: dict[str, Any]) -> dict[str, Any]:
    """Map normalized Field to semantic_text input shape.

    Salesforce REST describe returns ~56 camelCase keys per field
    (verified live against Account.Industry, Account.OwnerId,
    Account.Name). Substrate-1's _to_text_field reads:
        {name, object_name, type, label, help_text, is_custom,
         is_required, reference_to, picklist_value_set_name}

    Bridges:
      name              ← name
      object_name       ← _parent_object_api_name (phase-injected)
      type              ← type (e.g., 'string', 'picklist', 'reference')
      label             ← label
      help_text         ← inlineHelpText (the Salesforce term);
                          falls through None for fields without help
      is_custom         ← custom
      is_required       = NOT nillable (substrate-1 convention; see
                          fixture in test_semantic_text.py — Industry
                          is nillable AND is_required=False, matching
                          this derivation)
      reference_to      ← referenceTo[0] when present, else None.
                          Substrate-1's fixture uses scalar 'User',
                          NOT a list. Multi-target polymorphic refs
                          (Task.WhoId → Contact OR Lead) lose
                          per-target detail in the rendered text;
                          the FULL polymorphic graph lives in
                          HAS_RELATIONSHIP_TO edges (one per target).
      picklist_value_set_name = None for this cycle. Detecting GVS
                          references requires Tooling+Metadata API
                          per corrections-log §10; deferred.
    """
    ref_to_list = normalized.get("referenceTo") or []
    return {
        "name": normalized.get("name"),
        "object_name": normalized.get("_parent_object_api_name"),
        "type": normalized.get("type"),
        "label": normalized.get("label"),
        "help_text": normalized.get("inlineHelpText"),
        "is_custom": normalized.get("custom", False),
        "is_required": not normalized.get("nillable", True),
        "reference_to": ref_to_list[0] if ref_to_list else None,
        "picklist_value_set_name": None,
    }


def _to_presentation_record_type(normalized: dict[str, Any]) -> dict[str, Any]:
    """Map normalized RecordType to semantic_text input shape.

    Salesforce Tooling API for RecordType returns camelCase Metadata
    keys (developerName, active, description, label, picklistValues,
    ...). Substrate-1's _to_text_record_type input shape (verified
    against tests/unit/test_semantic_text.py:_record_type_fixture):
        {name, object_name, label, is_active, description}

    Bridges:
      name              ← developerName (Salesforce's canonical
                          per-Object RT identifier, e.g.,
                          'PartnerAccount')
      object_name       ← _parent_object_api_name (phase-injected
                          from FullName parsing — see
                          phase_record_type)
      label             ← label (display name shown to users in
                          record-type pickers)
      is_active         ← active (defaults True if missing; almost
                          always present in Tooling Metadata)
      description       ← description (often None on minimal RTs)

    No `picklistValues` field carried through — the value-allowed-
    subset is the CONSTRAINS_PICKLIST_VALUES edge's domain
    (deferred per corrections-log §14). RecordType's semantic_text
    speaks to identity + scope, not the picklist constraints.
    """
    return {
        "name": normalized.get("developerName"),
        "object_name": normalized.get("_parent_object_api_name"),
        "label": normalized.get("label"),
        "is_active": normalized.get("active", True),
        "description": normalized.get("description"),
    }


_PRESENTATION_FUNCTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "Object": _to_presentation_object,
    "PicklistValueSet": _to_presentation_picklist_value_set,
    "PicklistValue": _to_presentation_picklist_value,
    "Field": _to_presentation_field,
    "RecordType": _to_presentation_record_type,
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

"""Phase function registry — one function per entity type.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §§3-4.

This skeleton ships no-op placeholders for all 12 entity types.
Real per-entity-type fetch + normalize + write logic lands in
subsequent implementation cycles.

A phase function signature:

    def phase_foo(ctx: SyncContext) -> PhaseResult: ...

The engine calls each phase function inside its own transaction
(per §3 staged transactional boundaries). The function:
- Reads any prior-phase state it needs via ctx.engine
- Fetches Salesforce data via ctx.sf_client
- Normalizes and writes the entity rows (and detail-table rows)
- Returns a PhaseResult with counts; raises an exception OR
  sets PhaseResult.error_message on failure
"""
from __future__ import annotations

from typing import Any, Callable

from primeqa.sync.context import SyncContext
from primeqa.sync.fk_assertion import ENTITY_ORDER
from primeqa.sync.materialize import batched_materialize
from primeqa.sync.result import PhaseResult


# A phase function receives (ctx, conn) where conn is the SQLAlchemy
# Connection opened by engine._phase_transaction. All DB writes go
# through this conn so the entire phase is atomic. Per the
# connection-threading refactor (post 5ed6c84).
PhaseFunction = Callable[[SyncContext, Any], PhaseResult]


def _noop_phase(entity_type: str) -> PhaseFunction:
    """Return a no-op phase function for the given entity_type.

    The returned function does no Salesforce fetching, no DB writes,
    and returns an empty PhaseResult. Used during skeleton bring-up;
    real implementations replace these one cycle at a time. Accepts
    the conn parameter per the PhaseFunction contract but ignores
    it (no writes to perform).
    """

    def phase(ctx: SyncContext, conn: Any) -> PhaseResult:
        return PhaseResult(entity_type=entity_type)

    phase.__name__ = f"phase_{entity_type.lower()}"
    phase.__doc__ = f"No-op placeholder phase for entity_type={entity_type!r}."
    return phase


def _is_syncable_object(raw: dict) -> bool:
    """Filter Salesforce sObjects to those representing user-facing
    data objects.

    Excludes:
    - Non-queryable / non-searchable objects (meta types like
      AggregateResult, OutgoingEmail, RecentlyViewed, etc. — these
      cannot be SELECTed from)
    - Deprecated objects (no longer used; would clutter the model)
    - Custom Setting objects (configuration storage, not user data —
      distinct entity-modeling concern; out of scope for Object phase)

    Includes:
    - Standard objects (Account, Contact, Lead, Opportunity, Case, ...)
    - Custom objects (suffix __c)
    - Managed-package objects (NamespacePrefix__Object__c)
    - Platform objects representing users/permissions
      (User, Profile, PermissionSet) — these are sObjects at the
      Salesforce level even though our model also has separate User /
      Profile / PermissionSet entities. Their semantics in the model:
      Object entity captures the SCHEMA (table definition); User /
      Profile / PermissionSet entities capture the ROW-LEVEL data.
    """
    return (
        bool(raw.get("queryable"))
        and bool(raw.get("searchable"))
        and not bool(raw.get("deprecatedAndHidden"))
        and not bool(raw.get("customSetting"))
    )


def phase_object(ctx: SyncContext, conn: Any) -> PhaseResult:
    """Object phase — fetches all syncable sObjects and materializes
    them as Object entities.

    Per PHASE_2_STEP_4_SYNC_DESIGN.md §§4-5, 7. First phase in
    ENTITY_ORDER; no upstream dependencies.

    Filter: queryable AND searchable AND NOT deprecatedAndHidden
    AND NOT customSetting (see _is_syncable_object).

    Uses Salesforce API name (raw['name']) as external_id.
    Substrate-1's fetch_objects() returns ~700+ sObjects on a
    typical org; ~50-200 typically pass the filter.

    Calls batched_materialize once with the full syncable list.
    Internally chunked at 500 rows per pass per design doc §7.
    For typical sandbox object counts (<500 syncable), this is a
    single chunk = a single batched-INSERT statement (plus the
    SELECT, the queue UPSERT, etc.).

    All DB writes execute on `conn` (engine._phase_transaction's
    transaction); commit happens when this function returns and
    the engine exits the _phase_transaction context.
    """
    result = PhaseResult(entity_type="Object")
    raw_objects = ctx.sf_client.fetch_objects()
    syncable = [raw for raw in raw_objects if _is_syncable_object(raw)]
    if syncable:
        batched_materialize(
            ctx=ctx,
            conn=conn,
            entity_type="Object",
            raw_payloads=syncable,
            result=result,
        )
    return result


def phase_picklist_value_set(
    ctx: SyncContext, conn: Any,
) -> PhaseResult:
    """PicklistValueSet phase — unified GVS + SVS source.

    Per corrections-log §8 + §8 addendum: PicklistValueSet
    entity_type unifies two Salesforce sObject sources under one
    entity:
      - GlobalValueSet via fetch_global_value_sets — Tooling SOQL
        bulk; user-defined; sandbox typically has 0.
      - StandardValueSet via fetch_standard_value_sets — hardcoded
        616-entry catalog (sf_constants.STANDARD_VALUE_SET_LABELS,
        pinned to API v66.0) iterated as N=616 per-label Metadata
        fetches per the reified-column-required constraint
        (corrections-log §4).

    Both streams are tagged with a `_source` marker before being
    combined and passed to batched_materialize. The marker:
      - survives _strip_volatile (not in _VOLATILE_KEYS), so it
        lands in the normalized payload and contributes to the
        hash — a record's source is part of its identity
      - drives _to_presentation_picklist_value_set's branch
        between is_global_value_set=True/False
      - drives _extract_external_id's `SVS:` prefix for SVS rows,
        preventing collisions between a customer GVS named
        'Industry' and the SVS catalog entry 'Industry'

    Runs after Object per ENTITY_ORDER. Field phase will reference
    PicklistValueSet entities for fields whose picklist values
    derive from a value set.

    No filter applied to either stream — every record is worth
    materializing. Sandbox: 0 GVSes + ~600 SVSes ≈ ~600 rows total.
    Wall-clock dominated by SVS fetch (~6 min for full 616-entry
    iteration at ~0.6s/call).

    SVS iteration uses labels=None (full canonical catalog). A
    future cycle may switch to a discovered-label subset once
    Field phase exposes which SVSes are actually referenced.
    """
    result = PhaseResult(entity_type="PicklistValueSet")

    # GVS source.
    raw_gvs = ctx.sf_client.fetch_global_value_sets()
    for r in raw_gvs:
        r["_source"] = "GlobalValueSet"

    # SVS source — full canonical catalog iteration (labels=None).
    raw_svs = ctx.sf_client.fetch_standard_value_sets(labels=None)
    for r in raw_svs:
        r["_source"] = "StandardValueSet"

    combined = list(raw_gvs) + list(raw_svs)
    if combined:
        batched_materialize(
            ctx=ctx,
            conn=conn,
            entity_type="PicklistValueSet",
            raw_payloads=combined,
            result=result,
        )
    return result


def phase_picklist_value(ctx: SyncContext, conn: Any) -> PhaseResult:
    """PicklistValue phase — individual values within value sets.

    Per substrate-1 design (corrections-log §8 + PicklistValueAttributes
    docstring): PicklistValue's parent linkage is a NOT NULL FK column
    on picklist_value_details (picklist_value_set_entity_id), not an
    edges row. TIER_1_EDGES has no edge type covering this
    relationship — substrate-1's stated rationale is "picklist values
    ARE their attributes; there is no edge structure to lean on".

    No fresh Salesforce call exclusive to this phase: values come
    nested inside GVS records' Metadata.customValue and SVS records'
    Metadata.standardValue. PicklistValueSet phase already fetched
    these in the prior phase — this phase re-fetches via the same
    sf_client methods (cheap; SF caches describe responses) and
    extracts the nested values.

    Each value gets two phase-injected markers before
    batched_materialize:
      _parent_external_id  — the parent PVS's external_id, including
                             the 'SVS:' prefix for StandardValueSet
                             sources (so child external_ids inherit
                             the namespace-collision avoidance)
      _sort_order          — the value's position in the parent's
                             customValue/standardValue list (Salesforce
                             returns them in display order; no
                             explicit sortOrder field on the value
                             record itself)

    Both markers survive _strip_volatile (not in _VOLATILE_KEYS) and
    land in the normalized payload, contributing to the hash. A
    re-ordered parent list → child sort_order changes → child
    supersession. That's the right semantic — display order is
    meaningful metadata.

    The detail-table write happens inside batched_materialize via the
    PicklistValue mapper in detail_mappers.py, which uses
    make_parent_resolver(conn, ctx) to look up the parent
    PicklistValueSet entity_id by _parent_external_id.

    Memory note: a sandbox with 95 PVS records × ~10 values each
    produces ~950 PicklistValue payloads in memory simultaneously
    before batched_materialize chunks them. At ~500 bytes per value
    record, that's ~500KB — well within budget. Production orgs with
    industry clouds enabled might see 5x this; still acceptable.
    """
    result = PhaseResult(entity_type="PicklistValue")

    # Re-fetch parents to extract their nested value lists.
    raw_gvs = ctx.sf_client.fetch_global_value_sets()
    raw_svs = ctx.sf_client.fetch_standard_value_sets(labels=None)

    pv_payloads: list[dict[str, Any]] = []

    # GVS path: parent_external_id is unprefixed (per PVS cycle's
    # GVS contract). Values live in Metadata.customValue.
    for gvs in raw_gvs:
        parent_external_id = gvs["FullName"]
        meta = gvs.get("Metadata") or {}
        values = meta.get("customValue") or []
        for idx, v in enumerate(values):
            if not isinstance(v, dict):
                continue
            if not v.get("valueName"):
                # Defensive: skip placeholder/blank entries that the
                # Metadata API occasionally returns; their external_id
                # would be malformed and the detail mapper would fail.
                continue
            pv_payloads.append({
                **v,
                "_parent_external_id": parent_external_id,
                "_sort_order": idx,
            })

    # SVS path: parent_external_id carries the 'SVS:' prefix per the
    # PVS cycle's collision-avoidance contract. Values live in
    # Metadata.standardValue.
    for svs in raw_svs:
        parent_external_id = f"SVS:{svs['FullName']}"
        meta = svs.get("Metadata") or {}
        values = meta.get("standardValue") or []
        for idx, v in enumerate(values):
            if not isinstance(v, dict):
                continue
            if not v.get("valueName"):
                continue
            pv_payloads.append({
                **v,
                "_parent_external_id": parent_external_id,
                "_sort_order": idx,
            })

    if pv_payloads:
        batched_materialize(
            ctx=ctx,
            conn=conn,
            entity_type="PicklistValue",
            raw_payloads=pv_payloads,
            result=result,
        )
    return result


# One phase function per ENTITY_ORDER value. Kept aligned by
# construction below — see test_phase_registry_has_function_for_every_
# entity_order_value for the lock. Real phase implementations replace
# their _noop_phase entries one cycle at a time per design doc §9.
PHASE_REGISTRY: dict[str, PhaseFunction] = {
    entity_type: _noop_phase(entity_type) for entity_type in ENTITY_ORDER
}
PHASE_REGISTRY["Object"] = phase_object
PHASE_REGISTRY["PicklistValueSet"] = phase_picklist_value_set
PHASE_REGISTRY["PicklistValue"] = phase_picklist_value


def get_phase_function(entity_type: str) -> PhaseFunction:
    """Look up the phase function for entity_type.

    Raises:
        KeyError: if entity_type is not registered.
    """
    if entity_type not in PHASE_REGISTRY:
        raise KeyError(f"No phase registered for entity_type={entity_type!r}")
    return PHASE_REGISTRY[entity_type]

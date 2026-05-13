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

import logging
from typing import Any, Callable

from sqlalchemy import text


logger = logging.getLogger(__name__)

from primeqa.semantic.normalization import normalize
from primeqa.sync.context import SyncContext
from primeqa.sync.fk_assertion import ENTITY_ORDER
from primeqa.sync.materialize import (
    batched_materialize,
    materialize_edges_for_entities,
)
from primeqa.sync.result import PhaseResult


# A phase function receives (ctx, conn) where conn is the SQLAlchemy
# Connection opened by engine._phase_transaction. All DB writes go
# through this conn so the entire phase is atomic. Per the
# connection-threading refactor (post 5ed6c84).
PhaseFunction = Callable[[SyncContext, Any], PhaseResult]


def _synced_object_api_names(
    ctx: SyncContext, conn: Any,
) -> set[str]:
    """Return the set of Object api_names currently in scope for
    this sync — i.e., the Objects materialized by Object phase as
    currently-active entities.

    Used by bulk-fetcher child phases (RecordType, ValidationRule)
    to filter their fetched payloads against the Object scope
    BEFORE materialization. Without this filter, payloads
    referencing Objects that Object phase's syncability rules
    excluded (managed-package internals, custom settings,
    deprecated, non-queryable, non-searchable) would reach the
    detail mapper and fail FK resolution.

    Per-Object-iteration phases (Field, Layout) inherit this
    filter implicitly — they only fetch children for Objects in
    the synced set. Bulk-fetcher phases need the explicit filter
    per corrections-log §18.

    Returns set rather than list for O(1) membership testing in
    the filter loop. Empty set when Object phase wrote nothing
    (e.g., on an empty connected_org), which is a defensible
    state — bulk-fetched payloads all get skipped with WARN logs.
    """
    rows = conn.execute(text("""
        SELECT sf_api_name FROM entities
        WHERE last_synced_from_org_id = :org_id
          AND entity_type = 'Object'
          AND valid_to_seq IS NULL
    """), {"org_id": ctx.connected_org_id}).fetchall()
    return {row.sf_api_name for row in rows}


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

    Per corrections-log §9, this phase also POPULATES
    ctx.svs_metadata_cache as a side effect of its SVS fetch. The
    PicklistValue phase consumes the cache instead of refetching
    — a ~6 min wall-clock optimization. The cache is keyed by
    SVS FullName; the value is the Metadata sub-tree (not the
    full Tooling record). GVS records are NOT cached: the GVS
    fetch is a single bulk Tooling call (cheap regardless of N),
    so refetching in PV phase is acceptable.
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
        # §9: cache the Metadata sub-tree keyed by FullName so the
        # PV phase can extract values without refetching. Stored
        # as `Metadata or {}` so the cached value is always a dict
        # — keeps the PV-phase loop free of None checks.
        full_name = r.get("FullName")
        if full_name:
            ctx.svs_metadata_cache[full_name] = r.get("Metadata") or {}

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

    Values come nested inside GVS records' Metadata.customValue
    and SVS records' Metadata.standardValue. PicklistValueSet
    phase already fetched these in the prior phase, and per
    corrections-log §9 it stashed the SVS Metadata into
    ctx.svs_metadata_cache. This phase reads from the cache
    rather than re-fetching the 616-record SVS catalog
    (~6 min wall-clock saved). GVS is re-fetched via a single
    bulk Tooling call (cheap regardless of N), so the GVS path
    is unchanged.

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

    Marker injection is performed by
    extract_picklist_value_payloads_from_metadata
    (sf_client.py module-level helper). Same function handles both
    SVS and GVS paths — only the parent_external_id prefix and
    value_list_key differ.

    The detail-table write happens inside batched_materialize via the
    PicklistValue mapper in detail_mappers.py, which uses
    make_parent_resolver(conn, ctx) to look up the parent
    PicklistValueSet entity_id by _parent_external_id.

    Memory note: a sandbox with 95 PVS records × ~10 values each
    produces ~950 PicklistValue payloads in memory simultaneously
    before batched_materialize chunks them. At ~500 bytes per value
    record, that's ~500KB — well within budget. Production orgs with
    industry clouds enabled might see 5x this; still acceptable.

    Fallback: if ctx.svs_metadata_cache is empty (PV phase running
    in isolation — e.g., a test that exercises PV without PVS, or
    a resumed sync that skipped PVS), this phase falls back to
    refetching SVS Metadata directly. Logs an INFO so the resumed-
    sync case is visible; the test-isolation case is rare and
    acceptable.
    """
    from primeqa.integrations.sf_client import (
        extract_picklist_value_payloads_from_metadata,
    )

    result = PhaseResult(entity_type="PicklistValue")

    pv_payloads: list[dict[str, Any]] = []

    # GVS path: parent_external_id is unprefixed (per PVS cycle's
    # GVS contract). Values live in Metadata.customValue. Cheap
    # to re-fetch (single bulk Tooling call) — no cache needed.
    raw_gvs = ctx.sf_client.fetch_global_value_sets()
    for gvs in raw_gvs:
        pv_payloads.extend(
            extract_picklist_value_payloads_from_metadata(
                parent_external_id=gvs["FullName"],
                metadata=gvs.get("Metadata") or {},
                value_list_key="customValue",
            )
        )

    # SVS path: parent_external_id carries the 'SVS:' prefix per
    # the PVS cycle's collision-avoidance contract. Values live in
    # Metadata.standardValue. Read from
    # ctx.svs_metadata_cache populated by phase_picklist_value_set.
    if ctx.svs_metadata_cache:
        for full_name, metadata in ctx.svs_metadata_cache.items():
            pv_payloads.extend(
                extract_picklist_value_payloads_from_metadata(
                    parent_external_id=f"SVS:{full_name}",
                    metadata=metadata,
                    value_list_key="standardValue",
                )
            )
    else:
        # Defensive fallback for PV-in-isolation (test scenario)
        # or resumed sync where PVS phase already completed.
        # Refetches the 616-record catalog (~6 min wall-clock).
        logger.info(
            "phase_picklist_value: svs_metadata_cache empty; "
            "falling back to direct SVS fetch. Expected on "
            "resumed syncs and isolated tests; should be rare in "
            "production sync flow."
        )
        raw_svs = ctx.sf_client.fetch_standard_value_sets(labels=None)
        for svs in raw_svs:
            pv_payloads.extend(
                extract_picklist_value_payloads_from_metadata(
                    parent_external_id=f"SVS:{svs['FullName']}",
                    metadata=svs.get("Metadata") or {},
                    value_list_key="standardValue",
                )
            )

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
def phase_field(ctx: SyncContext, conn: Any) -> PhaseResult:
    """Field phase — fields per Object, with detail rows + edges.

    First edge-writing phase in the sync pipeline. Establishes the
    batched edge-write pattern for subsequent phases (RecordType,
    ValidationRule, Layout) to use.

    Iterates over Object entities materialized earlier in the same
    sync_run (looked up via SELECT scoped to this org's currently-
    active Object rows). For each Object, calls
    fetch_fields_for_object to get the REST describe response's
    `fields` list, decorates each field with the
    `_parent_object_api_name` marker, and accumulates payloads for
    batched materialization.

    Three things land per Field:
      1. Entity row in `entities` (entity_type='Field',
         sf_api_name='{Object}.{fieldName}')
      2. Detail row in `field_details` with object_entity_id +
         optional references_object_entity_id resolved via
         make_parent_resolver
      3. Edges:
         - BELONGS_TO → parent Object (always)
         - HAS_RELATIONSHIP_TO → each referenceTo target Object
           (none for non-reference fields, one for standard refs,
           N for polymorphic refs)

      HAS_PICKLIST_VALUES → PicklistValueSet is deferred per
      corrections-log §10 (REST describe doesn't expose value-set
      references; would require a separate Tooling-API fetcher).

    High-cardinality phase: 146 Objects × ~30-150 fields each
    ≈ 4500-15000 Field entities + ≈ same BELONGS_TO + ~500-1500
    HAS_RELATIONSHIP_TO. Wall-clock cost is dominated by the
    per-Object REST describe calls (~0.5-1s each → ~75-150s for
    the fetch phase) + the materialization writes.

    Memory: all field payloads accumulated in memory before
    batched_materialize chunks them at 500-row boundaries. At
    ~10KB per raw describe field, 10K fields × 10KB ≈ 100MB —
    within budget. Production orgs with managed packages might hit
    2-3× this; still acceptable.
    """
    result = PhaseResult(entity_type="Field")

    # 1. Read this sync's Object entities to enumerate. Filter:
    # currently-active rows for this connected_org. ENTITY_ORDER
    # guarantees Object phase ran before Field phase in the same
    # sync_run, so this returns the freshly-synced Object set.
    objects = conn.execute(text("""
        SELECT id, sf_api_name FROM entities
        WHERE last_synced_from_org_id = :org_id
          AND entity_type = 'Object'
          AND valid_to_seq IS NULL
    """), {"org_id": ctx.connected_org_id}).fetchall()

    # 2. Bulk-fetch fields for all Objects via /composite/batch.
    # Up to 25 sObjects per round trip; for 146 syncable Objects on
    # this sandbox, that's ~6 composite batches × ~3-5s each instead
    # of 146 sequential describes × ~1-1.5s = ~150-220s. Live
    # measurement: ~5-7× wall-clock improvement on the fetch portion
    # of phase_field.
    #
    # Per-Object failure tolerance: bulk fetcher silently omits keys
    # for sObjects that 404 / 403 within a batch (e.g., an Object
    # the Object phase materialized but whose describe is restricted
    # in the current session). The omitted keys are logged at WARN.
    # We could detect missing keys via set-difference here and treat
    # them as a sync error; for now, the lenient behavior matches
    # fetch_standard_value_sets's per-label pattern.
    object_api_names = [obj.sf_api_name for obj in objects]
    fields_by_object = ctx.sf_client.fetch_fields_for_objects_bulk(
        object_api_names,
    )

    field_payloads: list[dict[str, Any]] = []
    for object_api_name, fields in fields_by_object.items():
        for f in fields:
            f["_parent_object_api_name"] = object_api_name
            field_payloads.append(f)

    if not field_payloads:
        return result

    # 3. Materialize Field entities + field_details rows (the latter
    # via detail_mappers registry inside batched_materialize). Get
    # back the entity_id_map so edge construction can resolve source
    # entity_ids without re-querying.
    entity_id_map = batched_materialize(
        ctx=ctx,
        conn=conn,
        entity_type="Field",
        raw_payloads=field_payloads,
        result=result,
        return_id_map=True,
    )

    # 4. Edges. Compute normalized payloads (same as
    # batched_materialize did internally for hashing) so the edge
    # extractors see the post-_strip_volatile, sort_list_of_dicts
    # shape — same view substrate-1 uses when emitting derived
    # edges from entities.
    normalized_payloads = [normalize("Field", p) for p in field_payloads]
    materialize_edges_for_entities(
        ctx=ctx,
        conn=conn,
        source_entity_type="Field",
        entity_id_map=entity_id_map,
        normalized_payloads=normalized_payloads,
        result=result,
    )

    return result


def phase_record_type(ctx: SyncContext, conn: Any) -> PhaseResult:
    """RecordType phase — per-Object variants for layouts, picklist
    subsets, and business processes.

    Fifth real phase (5/12). Modest cardinality: sandbox at 5 RTs,
    production orgs typically 5-200. Fetched via
    fetch_record_types() — substrate-1's Category-2 (two-phase
    Tooling+Metadata) fetcher per corrections-log §1. Each RT
    carries:
      - Id, Name, IsActive, Description, SobjectType,
        EntityDefinitionId, BusinessProcessId, NamespacePrefix
      - FullName (Salesforce canonical identifier;
        '{Object}.{DeveloperName}' or namespaced variant)
      - Metadata (active, label, description, businessProcess,
        compactLayoutAssignment, picklistValues — the per-Field
        allowed-value subset)

    Parent Object resolution: extract from FullName by splitting at
    the first '.'. Examples:
      'Account.PartnerAccount'      → parent 'Account'
      'MyNS__Account.PartnerAccount' → parent 'MyNS__Account'
      'sfLma__License__c.Trial'      → parent 'sfLma__License__c'

    Injected as `_parent_object_api_name` marker; survives
    _strip_volatile to land in the normalized hash and drive
    BELONGS_TO + detail-table FK resolution downstream.

    CONSTRAINS_PICKLIST_VALUES deferred per corrections-log §14.
    Substrate-1 has an internal registry-vs-derivation
    contradiction on the edge's target type (PicklistValueSet vs
    PicklistValue) that needs resolution alongside §10's
    fetch_custom_field_metadata. The grants junction table
    (record_type_picklist_value_grants) also stays empty until
    that follow-up cycle.

    Memory note: 5-200 RTs × ~5 KB per Metadata blob = trivial
    (<1 MB).
    """
    result = PhaseResult(entity_type="RecordType")

    raw_rts = ctx.sf_client.fetch_record_types()

    # Filter RTs whose parent Object isn't in this sync's syncable
    # scope (per corrections-log §18). Object phase's syncability
    # filter (Option C narrowest) excludes managed-package internal
    # Objects, custom settings, deprecated, non-queryable, non-
    # searchable. RTs on those Objects come back from Tooling but
    # can't be materialized — the detail mapper's parent-Object FK
    # resolution would fail.
    synced_objects = _synced_object_api_names(ctx, conn)
    filtered_rts: list[dict[str, Any]] = []
    skipped_count = 0
    for rt in raw_rts:
        full_name = rt.get("FullName") or ""
        # Defensive split — RTs without a '.' in FullName would be
        # malformed at the SF level; set parent=None so they fall
        # into the skip bucket (None won't be in synced_objects).
        if "." in full_name:
            parent_object = full_name.split(".", 1)[0]
        else:
            parent_object = None
        if parent_object not in synced_objects:
            skipped_count += 1
            continue
        rt["_parent_object_api_name"] = parent_object
        filtered_rts.append(rt)

    if skipped_count > 0:
        logger.info(
            "phase_record_type: skipped %d RecordTypes whose parent "
            "Object is not in scope (managed-package internals, "
            "filtered, or malformed FullName)",
            skipped_count,
        )

    if not filtered_rts:
        return result

    entity_id_map = batched_materialize(
        ctx=ctx,
        conn=conn,
        entity_type="RecordType",
        raw_payloads=filtered_rts,
        result=result,
        return_id_map=True,
    )

    normalized_payloads = [normalize("RecordType", p) for p in raw_rts]
    materialize_edges_for_entities(
        ctx=ctx,
        conn=conn,
        source_entity_type="RecordType",
        entity_id_map=entity_id_map,
        normalized_payloads=normalized_payloads,
        result=result,
    )

    return result


def phase_layout(ctx: SyncContext, conn: Any) -> PhaseResult:
    """Layout phase — per-Object page layouts with field placements.

    Sixth real phase (6/12). First phase to write property-bearing
    edges (INCLUDES_FIELD with section_name + position + flags).

    Two-step fetch per substrate-1's documented pattern:
    1. Per-Object REST `/sobjects/{name}/describe/layouts` returns
       the rich layout structure (detailLayoutSections, layoutRows,
       layoutItems, layoutComponents) but with Layout Ids only — no
       names. Substrate-1's `fetch_layouts_for_object` provides this.
    2. ONE bulk Tooling SOQL `SELECT Id, Name, EntityDefinitionId,
       LayoutType FROM Layout` resolves Id → Name + LayoutType
       mapping. Substrate-1's `fetch_layout_names()` provides this.

    The sync layer joins on Layout Id. Per corrections-log §5,
    substrate-1's fetcher docstring explicitly defers the name-
    resolution second pass to sync.

    Filtering:
    - `LayoutType = 'GlobalQuickActionList'`: skip with WARN log
      (no parent Object — EntityDefinitionId='Global'; can't
      satisfy layout_details.object_entity_id NOT NULL FK per §15)
    - Layouts whose Id isn't in the Tooling response: skip with
      WARN log (Tooling-vs-REST drift; rare but defensive)

    Edges:
    - BELONGS_TO → Object (property-less, every Layout)
    - INCLUDES_FIELD → Field (property-bearing — section_name,
      section_order, row, column, is_required, is_readonly per
      IncludesFieldProperties schema; one edge per layoutComponent
      with type='Field')
    - ASSIGNED_TO_PROFILE_RECORDTYPE → Profile (deferred per
      corrections-log §16 — Profile entities don't exist yet)

    Sandbox cardinality: 115 layouts in this dev org (per
    fetch_layout_names() docstring). Each layout has ~5-15
    sections × 1-10 rows × 1-3 columns × 1-3 components =
    ~30-100 INCLUDES_FIELD edges per layout → ~3,000-12,000 total.
    """
    result = PhaseResult(entity_type="Layout")

    objects = conn.execute(text("""
        SELECT id, sf_api_name FROM entities
        WHERE last_synced_from_org_id = :org_id
          AND entity_type = 'Object'
          AND valid_to_seq IS NULL
    """), {"org_id": ctx.connected_org_id}).fetchall()

    # Phase 1: per-Object REST describe/layouts. Accumulate
    # layout records keyed by parent Object api_name.
    layouts_by_object: dict[str, list[dict[str, Any]]] = {}
    for obj in objects:
        api_name = obj.sf_api_name
        try:
            response = ctx.sf_client.fetch_layouts_for_object(api_name)
        except Exception as e:
            # Per-Object describe/layouts failure: log + skip.
            # Industry-cloud Objects (DataKitObject, etc.) sometimes
            # 404 on this endpoint.
            logger.warning(
                "phase_layout: fetch_layouts_for_object(%r) failed: "
                "%s", api_name, e,
            )
            continue
        layouts = response.get("layouts") or []
        if layouts:
            layouts_by_object[api_name] = layouts

    if not layouts_by_object:
        return result

    # Phase 2: bulk Tooling SOQL resolves Layout.Id → metadata.
    # ONE call returns ALL layouts; we filter to Ids that appeared
    # in our REST responses.
    tooling_records = ctx.sf_client.fetch_layout_names()
    tooling_by_id: dict[str, dict[str, Any]] = {}
    for rec in tooling_records:
        rec_id = rec.get("Id")
        if rec_id:
            tooling_by_id[rec_id] = rec

    # Phase 3: decorate each layout with parent + name + type
    # markers. Filter GlobalQuickActionList; filter unresolved Ids.
    layout_payloads: list[dict[str, Any]] = []
    for api_name, layouts in layouts_by_object.items():
        for layout in layouts:
            layout_id = layout.get("id")
            if not layout_id:
                continue
            tooling = tooling_by_id.get(layout_id)
            if not tooling:
                logger.warning(
                    "phase_layout: layout id=%r on %r missing from "
                    "fetch_layout_names() response; skipping",
                    layout_id, api_name,
                )
                continue
            layout_type = tooling.get("LayoutType") or ""
            # Skip non-Page-Layout variants per §15
            if layout_type != "Standard":
                logger.info(
                    "phase_layout: skipping layout id=%r "
                    "(LayoutType=%r; only 'Standard' page layouts "
                    "supported in this cycle)",
                    layout_id, layout_type,
                )
                continue
            layout_name = tooling.get("Name") or ""
            if not layout_name:
                logger.warning(
                    "phase_layout: layout id=%r has empty Name; "
                    "skipping", layout_id,
                )
                continue
            # Inject markers per substrate-1's Layout FullName
            # convention (HYPHEN separator, not dot — per §15
            # parking lot in substrate-1's design)
            layout["_parent_object_api_name"] = api_name
            layout["_layout_full_name"] = f"{api_name}-{layout_name}"
            layout["_layout_type"] = layout_type
            layout["_layout_name_resolved"] = layout_name
            layout_payloads.append(layout)

    if not layout_payloads:
        return result

    entity_id_map = batched_materialize(
        ctx=ctx,
        conn=conn,
        entity_type="Layout",
        raw_payloads=layout_payloads,
        result=result,
        return_id_map=True,
    )

    normalized_payloads = [
        normalize("Layout", p) for p in layout_payloads
    ]
    materialize_edges_for_entities(
        ctx=ctx,
        conn=conn,
        source_entity_type="Layout",
        entity_id_map=entity_id_map,
        normalized_payloads=normalized_payloads,
        result=result,
    )

    return result


def phase_validation_rule(ctx: SyncContext, conn: Any) -> PhaseResult:
    """ValidationRule phase — per-Object business-logic rule with
    error condition formula + error message.

    Seventh real phase (7/12). Pure pattern-application against
    established infrastructure — closely mirrors phase_record_type
    (d91e777) since both fetch via Tooling Phase 1 + Phase 2 with
    FullName as canonical identifier.

    Fetched via fetch_validation_rules (Category 2 two-phase
    Tooling+Metadata per corrections-log §1). Each VR carries:
      Phase 1 (top-level): Id, ValidationName, Active,
        ErrorMessage, ErrorDisplayField, Description,
        EntityDefinitionId
      Phase 2 (per-Id): FullName, Metadata
        (errorConditionFormula, etc.)

    Post-P5 (48efea4): fetch_validation_rules' Phase 2 SOQL was
    enhanced to include FullName. Prior to P5, the fetcher omitted
    FullName and sync would have had to resolve EntityDefinitionId
    (Salesforce Id) → Object api_name via a separate Tooling call.
    P5's substrate-1 enhancement removes that complication.

    Parent Object resolution: extract from FullName by splitting at
    the first '.'. Examples:
      'Account.AmountPositive'         → 'Account'
      'MyNS__Object.RequiredField'      → 'MyNS__Object'

    Same algorithm as phase_record_type; injected as
    `_parent_object_api_name` marker.

    Two edge types written this cycle:
    - BELONGS_TO  → Object (STRUCTURAL containment; property-less)
    - APPLIES_TO  → Object (BEHAVIOR relationship; property-less)
    Both target the same parent Object but with distinct edge_types.
    UNIQUE active index allows both to coexist.

    REFERENCES → Field deferred per corrections-log §17. Substrate-1
    expects validation_rule_field_refs junction-table rows for that
    edge, which requires a Salesforce formula parser (PRIORVALUE,
    ISCHANGED, ISNEW tokenization + field-name disambiguation).
    Future cycle adds the parser + writer.

    Sandbox cardinality: 61 VRs (per survey).
    """
    result = PhaseResult(entity_type="ValidationRule")

    raw_vrs = ctx.sf_client.fetch_validation_rules()

    # Filter VRs whose parent Object isn't in this sync's syncable
    # scope (per corrections-log §18). Surfaced live: sandbox has
    # a VR 'FullNameUpdatePrevention' on
    # sfFma__FeatureParameterBoolean__c, a managed-package internal
    # Object that Object phase's syncability filter excludes.
    # Without filtering, the detail mapper's parent-Object FK
    # resolution fails for any such VR.
    synced_objects = _synced_object_api_names(ctx, conn)
    filtered_vrs: list[dict[str, Any]] = []
    skipped_count = 0
    for vr in raw_vrs:
        full_name = vr.get("FullName") or ""
        if "." in full_name:
            parent_object = full_name.split(".", 1)[0]
        else:
            parent_object = None
        if parent_object not in synced_objects:
            skipped_count += 1
            continue
        vr["_parent_object_api_name"] = parent_object
        filtered_vrs.append(vr)

    if skipped_count > 0:
        logger.info(
            "phase_validation_rule: skipped %d ValidationRules "
            "whose parent Object is not in scope (managed-package "
            "internals, filtered, or malformed FullName)",
            skipped_count,
        )

    if not filtered_vrs:
        return result

    entity_id_map = batched_materialize(
        ctx=ctx,
        conn=conn,
        entity_type="ValidationRule",
        raw_payloads=filtered_vrs,
        result=result,
        return_id_map=True,
    )

    normalized_payloads = [
        normalize("ValidationRule", p) for p in filtered_vrs
    ]
    materialize_edges_for_entities(
        ctx=ctx,
        conn=conn,
        source_entity_type="ValidationRule",
        entity_id_map=entity_id_map,
        normalized_payloads=normalized_payloads,
        result=result,
    )

    return result


def phase_profile(ctx: SyncContext, conn: Any) -> PhaseResult:
    """Profile phase — org-level permission templates.

    Eighth real phase (8/12). First org-level entity phase
    (previous seven were either top-of-hierarchy or child-of-Object).
    No parent-Object resolution needed — Profile is org-level.

    Fetched via fetch_profiles (Tooling Phase 1 + Phase 2 with
    FullName + Metadata). Sandbox at 18 standard Profiles; customer
    orgs typically 30-100. Per-Profile Metadata averages ~278 KB
    (System Administrator measured at 277 KB).

    Two property-bearing edge types written:
    - GRANTS_OBJECT_ACCESS → Object (PERMISSION, 6 access flags
      per GrantsObjectAccessProperties)
    - GRANTS_FIELD_ACCESS → Field (PERMISSION, 2 access flags
      per GrantsFieldAccessProperties)

    Cardinality (sandbox estimate):
    - GRANTS_OBJECT_ACCESS: 18 × ~130 = ~2.3K edges
    - GRANTS_FIELD_ACCESS: 18 × ~3K avg = ~50K edges
    - Total: ~52K edges this phase
    Heavy Profile (System Administrator) likely has 200+ object
    perms and 5K+ field perms; total scales accordingly.

    §18 parent-scope filter NOT needed at the phase level. Edge
    target resolution naturally skips fields/objects on filtered-
    out parent Objects via parent_resolver returning None +
    existing `if target_id is None: continue` guard. Many such
    skips expected for sandbox's managed-package Objects (sfFma,
    sfLma, etc.); they're informational, not errors.

    NOT in this cycle:
    - ASSIGNED_TO_PROFILE_RECORDTYPE (Layout source per §16):
      now unblocked (Profile entities exist) but requires
      Layout-payload layoutAssignments[] survey. Wires up in a
      subsequent cycle.
    - HAS_PROFILE (User source): User phase not yet built.
    """
    result = PhaseResult(entity_type="Profile")

    raw_profiles = ctx.sf_client.fetch_profiles()

    if not raw_profiles:
        return result

    entity_id_map = batched_materialize(
        ctx=ctx,
        conn=conn,
        entity_type="Profile",
        raw_payloads=raw_profiles,
        result=result,
        return_id_map=True,
    )

    normalized_payloads = [
        normalize("Profile", p) for p in raw_profiles
    ]
    materialize_edges_for_entities(
        ctx=ctx,
        conn=conn,
        source_entity_type="Profile",
        entity_id_map=entity_id_map,
        normalized_payloads=normalized_payloads,
        result=result,
    )

    return result


def phase_permission_set(
    ctx: SyncContext, conn: Any,
) -> PhaseResult:
    """PermissionSet phase — org-level permission templates +
    PSG inheritance.

    Ninth real phase (9/12). Second source for GRANTS_OBJECT_ACCESS
    and GRANTS_FIELD_ACCESS (Profile being the first); first writer
    of INHERITS_PERMISSION_SET (PSG → member PS).

    Fetched via fetch_permission_sets (substrate-1 Category 4 per
    corrections-log §5): five SOQL queries return a 5-key dict —
      permission_sets:     parent rows from Tooling FIELDS(STANDARD)
      object_permissions:  Data API rows, ParentId-joined
      field_permissions:   Data API rows, ParentId-joined
      psg_components:      PSG → member PS join rows
      license_id_to_label: LicenseId → MasterLabel map

    Sandbox cardinality (probe 2026-05-13):
      71 raw PS rows → filter Type='Profile' (18 auto-synth dups
      per substrate-1 §5) → 53 syncable PSes
      2,648 ObjectPermissions ÷ 53 ≈ 50 ops avg per PS
      11,397 FieldPermissions ÷ 53 ≈ 215 fps avg per PS
      Expected: ~2,650 GRANTS_OBJECT_ACCESS + ~11,400
      GRANTS_FIELD_ACCESS + ~6-10 INHERITS_PERMISSION_SET edges
      (2 PSGs × ~3-5 members each).

    Three transformations applied before materialize:

    1. Filter Type='Profile' rows. These are auto-synth duplicates
       of Profile entities (corrections-log §5 Category 4 note).
       Skipped count is logged.

    2. JOIN ObjectPermissions / FieldPermissions to each surviving
       PS by ParentId. The substrate-1 _normalize_permission_set
       expects top-level `objectPermissions` and `fieldPermissions`
       lists (mirrors Profile's Metadata.* shape); the join builds
       them. O(N) using ParentId-keyed dicts to avoid N+1.

    3. Resolve LicenseId → MasterLabel via license_id_to_label map.
       Inject `_license_label` marker on each PS. Used by the
       detail mapper (license_type NOT NULL column) and the
       presentation adapter (semantic_text input). Sentinel
       '(no license)' for null / unmapped LicenseId.

    PSG decoration:
      For each Type='Group' PS (PSG), walk psg_components rows
      where PermissionSetGroupId == PSG.Id; resolve each row's
      PermissionSetId → member PS.Name via an Id→Name map built
      from permission_sets list. Inject `_member_ps_names: list[str]`
      marker on the PSG. Non-PSGs have no marker → no INHERITS
      edges (extractor returns []).

    Three edge types written:
    - GRANTS_OBJECT_ACCESS → Object (property-bearing, 6 flags)
    - GRANTS_FIELD_ACCESS → Field (property-bearing, 2 flags)
    - INHERITS_PERMISSION_SET → PermissionSet (property-less; only
      written for PSGs via _member_ps_names marker)

    No §18 entity-level parent filter — PS is org-level. Edge
    target resolution naturally skips unsyncable targets via the
    materialize layer's skip-counter (corrections-log §19); skip
    counts surface in PhaseResult.edges_skipped_by_type.
    """
    result = PhaseResult(entity_type="PermissionSet")

    fetch_result = ctx.sf_client.fetch_permission_sets()
    raw_ps_records = fetch_result.get("permission_sets") or []
    object_permissions = fetch_result.get("object_permissions") or []
    field_permissions = fetch_result.get("field_permissions") or []
    psg_components = fetch_result.get("psg_components") or []
    license_id_to_label = fetch_result.get("license_id_to_label") or {}

    if not raw_ps_records:
        return result

    # Filter Type='Profile' auto-synth duplicates per substrate-1 §5.
    skipped_profile_count = 0
    syncable_ps_records: list[dict[str, Any]] = []
    for ps in raw_ps_records:
        if ps.get("Type") == "Profile":
            skipped_profile_count += 1
            continue
        syncable_ps_records.append(ps)
    if skipped_profile_count > 0:
        logger.info(
            "phase_permission_set: skipped %d Type='Profile' "
            "PermissionSets (auto-synth duplicates of Profile "
            "entities per substrate-1 §5)",
            skipped_profile_count,
        )

    if not syncable_ps_records:
        return result

    # Build ParentId-keyed indexes for O(N) JOIN against syncable PSes.
    op_by_parent: dict[str, list[dict[str, Any]]] = {}
    for op in object_permissions:
        if not isinstance(op, dict):
            continue
        pid = op.get("ParentId")
        if pid:
            op_by_parent.setdefault(pid, []).append(op)
    fp_by_parent: dict[str, list[dict[str, Any]]] = {}
    for fp in field_permissions:
        if not isinstance(fp, dict):
            continue
        pid = fp.get("ParentId")
        if pid:
            fp_by_parent.setdefault(pid, []).append(fp)

    # Build PSG-component index + Id→Name map for member-name
    # resolution.
    #
    # IMPORTANT — Salesforce schema asymmetry: the Type='Group' row
    # in PermissionSet is a SHADOW of the actual PermissionSetGroup
    # entity, which lives in a SEPARATE table with its own Id
    # (prefix 0PG vs PermissionSet's 0PS). The two are linked by
    # PermissionSet.PermissionSetGroupId → PermissionSetGroup.Id.
    # PermissionSetGroupComponent rows reference the PSG's 0PG-
    # prefixed Id via PermissionSetGroupId, not the shadow PS's
    # 0PS-prefixed Id.
    #
    # Correct join chain:
    #   PermissionSet(Type='Group').PermissionSetGroupId   (0PG...)
    #   ↓
    #   PermissionSetGroupComponent.PermissionSetGroupId   (0PG...)
    #   ↓
    #   PermissionSetGroupComponent.PermissionSetId        (0PS...)
    #   ↓
    #   PermissionSet.Id  (member PS's 0PS Id)
    #
    # The Id→Name map uses the FULL ps list (including Type='Profile'
    # rows we filtered out) because a PSG could reference a member
    # of any Type; using the full list is defensive against
    # Salesforce's evolving Type taxonomy.
    psg_components_by_group: dict[str, list[dict[str, Any]]] = {}
    for psg_c in psg_components:
        if not isinstance(psg_c, dict):
            continue
        gid = psg_c.get("PermissionSetGroupId")
        if gid:
            psg_components_by_group.setdefault(gid, []).append(psg_c)
    ps_id_to_name: dict[str, str] = {}
    for ps in raw_ps_records:
        ps_id = ps.get("Id")
        ps_name = ps.get("Name")
        if ps_id and ps_name:
            ps_id_to_name[ps_id] = ps_name

    # Decorate each syncable PS: JOIN children + resolve license +
    # PSG inheritance (Type='Group' only).
    #
    # Pre-sort the joined child lists by their PS-shape identity
    # keys (SobjectType / Field). Substrate-1's
    # _normalize_permission_set sorts on Profile-shape keys
    # (lowercase `object` / `field`); against PS data the sort
    # key resolves to "" for every entry, so the substrate-1
    # sort is a no-op. Pre-sorting here gives the lists a
    # deterministic order BEFORE normalize+hash so two consecutive
    # syncs don't see spurious supersession from SOQL-result
    # ordering. (Python's sort is stable, so substrate-1's
    # subsequent uniform-"" sort preserves our order.)
    for ps in syncable_ps_records:
        ps_id = ps.get("Id")
        op_list = list(op_by_parent.get(ps_id, []))
        op_list.sort(key=lambda p: str(p.get("SobjectType", "")))
        fp_list = list(fp_by_parent.get(ps_id, []))
        fp_list.sort(key=lambda p: str(p.get("Field", "")))
        ps["objectPermissions"] = op_list
        ps["fieldPermissions"] = fp_list
        license_id = ps.get("LicenseId")
        # _license_label sentinel keeps the NOT NULL license_type
        # column satisfied even when LicenseId is null or
        # unmapped (per §5 fault-tolerance: license query may
        # have returned []).
        ps["_license_label"] = (
            license_id_to_label.get(license_id) or "(no license)"
        )
        # PSG decoration: Type='Group' PSes get _member_ps_names
        # for INHERITS_PERMISSION_SET edge extraction. Look up
        # components via PermissionSetGroupId (the 0PG... id, NOT
        # the shadow PS's 0PS... Id — see comment above on the
        # Salesforce schema asymmetry). Non-Group PSes don't get
        # the marker; extractor returns [].
        if ps.get("Type") == "Group":
            psg_real_id = ps.get("PermissionSetGroupId")
            components = (
                psg_components_by_group.get(psg_real_id, [])
                if psg_real_id else []
            )
            member_names: list[str] = []
            for c in components:
                member_id = c.get("PermissionSetId")
                if not member_id:
                    continue
                member_name = ps_id_to_name.get(member_id)
                if member_name:
                    member_names.append(member_name)
            # Sort for deterministic hash (component fetch order
            # isn't guaranteed to be stable across syncs).
            ps["_member_ps_names"] = sorted(member_names)

    entity_id_map = batched_materialize(
        ctx=ctx,
        conn=conn,
        entity_type="PermissionSet",
        raw_payloads=syncable_ps_records,
        result=result,
        return_id_map=True,
    )

    normalized_payloads = [
        normalize("PermissionSet", p) for p in syncable_ps_records
    ]
    materialize_edges_for_entities(
        ctx=ctx,
        conn=conn,
        source_entity_type="PermissionSet",
        entity_id_map=entity_id_map,
        normalized_payloads=normalized_payloads,
        result=result,
    )

    return result


def phase_user(ctx: SyncContext, conn: Any) -> PhaseResult:
    """User phase — workforce identity + Profile/PermissionSet
    attribution.

    Tenth real phase (10/12). First entity to write
    HAS_PROFILE edges (User → Profile, property-less; the Profile
    cycle's deferred target now resolves) AND
    HAS_PERMISSION_SET edges (User → PermissionSet, property-
    bearing with HasPermissionSetProperties: assigned_at,
    assigned_by_user_entity_id, expiration_date).

    Fetched via fetch_users (substrate-1 Category 4 per
    corrections-log §5; extended this cycle to return 5-key dict):
      - users: parent rows from Data API SOQL (12 fields)
      - permission_set_assignments: direct PSA rows (PSG-derived
        membership excluded via WHERE PermissionSetGroupId IS
        NULL — captured via INHERITS_PERMISSION_SET edges already)
        with nested PermissionSet.Name for cheap edge target
        resolution
      - profile_id_to_name: Id→Name map for the
        user_details.profile_entity_id NOT NULL FK resolution
        (User has only ProfileId; Profile entities use Name as
        external_id)

    Sandbox cardinality (probe 2026-05-13):
      6 Users (3 Standard, 1 AutomatedProcess,
      1 CloudIntegrationUser, 1 CsnOnly) × ~1 Profile each →
      6 HAS_PROFILE edges; 5 PSAs → 5 HAS_PERMISSION_SET edges.

    TWO-PASS MATERIALIZATION (per task directive 2B):
      Pass 1: materialize User entities; capture
              Username → entity_id map (return_id_map=True)
      Pass 2: build User.Id → entity_id map (User.Id is the
              Salesforce Id; entity_id is the UUID); update each
              user's _ps_assignments entries with the resolved
              assigned_by_user_entity_id (from CreatedById →
              entity_id lookup); then call
              materialize_edges_for_entities with the same
              normalized payloads (which now carry the resolved
              assigners in their _ps_assignments markers).

    The two-pass approach handles HasPermissionSetProperties's
    forward reference to assigned_by_user_entity_id (a User
    entity_id) without leaving the schema field as None when
    the assigner is in this sync's User set. Unresolvable
    assigners (e.g., assigner is a system user not in the synced
    User set, or assigner User was filtered) fall back to None
    — schema allows Optional.

    No §18 entity-level parent filter — User is org-level. Edge
    target resolution for HAS_PROFILE / HAS_PERMISSION_SET
    silently skips unsyncable targets via the materialize layer's
    §19 observability counter on PhaseResult.
    """
    result = PhaseResult(entity_type="User")

    fetch_result = ctx.sf_client.fetch_users()
    raw_users = fetch_result.get("users") or []
    psas = fetch_result.get("permission_set_assignments") or []

    if not raw_users:
        return result

    # Build Profile + PermissionSet Id → external_id maps from
    # the entities table. These entity types are materialized
    # BEFORE User per ENTITY_ORDER, so the entities table is the
    # authoritative source of truth for external_ids. Reading
    # from the DB sidesteps the Salesforce SOQL asymmetry where
    # `SELECT Name FROM Profile` returns display names that
    # differ from Tooling Profile.FullName (e.g., 'System
    # Administrator' vs 'Admin', 'Standard User' vs 'Standard');
    # Profile entities use FullName as external_id, so a
    # Data-API Name map would mis-resolve and break HAS_PROFILE
    # edge writes + user_details FK resolution.
    #
    # PermissionSet entities use Name directly (no Tooling-vs-
    # Data asymmetry there), but we build the map the same way
    # for consistency + to avoid extra SOQL hops.
    profile_id_to_external_id: dict[str, str] = {}
    profile_rows = conn.execute(text("""
        SELECT attributes->>'Id' AS sf_id, sf_api_name
        FROM entities
        WHERE last_synced_from_org_id = :org_id
          AND entity_type = 'Profile'
          AND valid_to_seq IS NULL
    """), {"org_id": ctx.connected_org_id}).fetchall()
    for row in profile_rows:
        if row.sf_id and row.sf_api_name:
            profile_id_to_external_id[row.sf_id] = row.sf_api_name

    permission_set_id_to_external_id: dict[str, str] = {}
    ps_rows = conn.execute(text("""
        SELECT attributes->>'Id' AS sf_id, sf_api_name
        FROM entities
        WHERE last_synced_from_org_id = :org_id
          AND entity_type = 'PermissionSet'
          AND valid_to_seq IS NULL
    """), {"org_id": ctx.connected_org_id}).fetchall()
    for row in ps_rows:
        if row.sf_id and row.sf_api_name:
            permission_set_id_to_external_id[row.sf_id] = row.sf_api_name

    # Filter Users whose ProfileId isn't in the synced Profile
    # entity set. Salesforce keeps some system profiles (e.g.,
    # "Automated Process" for AutomatedProcess users) hidden
    # from both Data API and Tooling SELECT FROM Profile; those
    # Users can't be materialized because
    # user_details.profile_entity_id is NOT NULL FK with no
    # Profile entity to reference. Silent skip + INFO log per
    # the §19 observability pattern.
    syncable_users: list[dict[str, Any]] = []
    skipped_user_count = 0
    for user in raw_users:
        profile_id = user.get("ProfileId")
        if profile_id and profile_id in profile_id_to_external_id:
            syncable_users.append(user)
        else:
            skipped_user_count += 1
            logger.debug(
                "phase_user: skipping User %r — ProfileId %r not "
                "in synced Profile entity set (Salesforce hides "
                "some system profiles)",
                user.get("Username"), profile_id,
            )
    if skipped_user_count > 0:
        logger.info(
            "phase_user: skipped %d Users whose ProfileId could "
            "not be resolved (typical for AutomatedProcess / "
            "platform-synthetic users whose profile is not "
            "exposed via Salesforce SOQL); their HAS_PROFILE "
            "and HAS_PERMISSION_SET edges will not be written.",
            skipped_user_count,
        )

    if not syncable_users:
        return result

    # Filter PSAs to direct-PS assignments (PermissionSetGroupId
    # IS NULL). PSG-derived memberships are already captured via
    # INHERITS_PERMISSION_SET edges from the PermissionSet cycle;
    # writing HAS_PERMISSION_SET edges for them would duplicate
    # the traversal path. Python-side filter (SOQL `WHERE
    # PermissionSetGroupId = NULL` is HTTP 400 in this org).
    direct_psas: list[dict[str, Any]] = []
    psg_derived_count = 0
    for psa in psas:
        if not isinstance(psa, dict):
            continue
        if psa.get("PermissionSetGroupId"):
            psg_derived_count += 1
            continue
        direct_psas.append(psa)
    if psg_derived_count > 0:
        logger.debug(
            "phase_user: filtered out %d PSG-derived PSAs "
            "(captured via INHERITS_PERMISSION_SET edges from "
            "the PermissionSet cycle)",
            psg_derived_count,
        )

    # Build helper indexes:
    # - direct PSAs grouped by AssigneeId (User Id) → list of PSA rows
    psa_by_assignee: dict[str, list[dict[str, Any]]] = {}
    for psa in direct_psas:
        assignee = psa.get("AssigneeId")
        if assignee:
            psa_by_assignee.setdefault(assignee, []).append(psa)

    # Decorate each User payload with:
    # - _profile_name: resolved via profile_id_to_external_id
    #   (always present for syncable_users post-filter above)
    # - _ps_assignments: list ready for the edge extractor +
    #   properties extractor. Pass 2 below augments each entry
    #   with assigned_by_user_entity_id (currently always None
    #   in v1 — CreatedById is restricted on PSA in v66.0
    #   per fetch_users docstring).
    for user in syncable_users:
        profile_id = user.get("ProfileId")
        user["_profile_name"] = profile_id_to_external_id.get(
            profile_id,
        )

        user_psas = psa_by_assignee.get(user.get("Id"), [])
        # Resolve each PSA's PermissionSetId → PermissionSet
        # external_id via the entities-table-derived map. PSAs
        # whose PermissionSetId doesn't resolve (PermissionSet
        # was filtered, never synced, or is a Type='Profile'
        # shadow we excluded) get name=None and are dropped by
        # the extractor's `if name` guard.
        decorated_psas: list[dict[str, Any]] = []
        for psa in user_psas:
            ps_id = psa.get("PermissionSetId")
            ps_name = (
                permission_set_id_to_external_id.get(ps_id)
                if ps_id else None
            )
            if not ps_name:
                # PermissionSet not in our id map; skip silently
                # (substrate-1 silent-skip pattern, observability
                # already covered at the materialize edge layer
                # for the target-resolution step).
                continue
            decorated_psas.append({
                "permission_set_name": ps_name,
                # SystemModstamp serves as `assigned_at` proxy
                # (CreatedDate is restricted on PSA in v66.0;
                # SystemModstamp is the closest available).
                "assigned_at": psa.get("SystemModstamp"),
                "expiration_date": psa.get("ExpirationDate"),
                # _created_by_user_id: phase-internal marker for
                # pass 2's assigner resolution. CreatedById is
                # restricted in v66.0 (returns None always) so
                # pass 2's resolution always lands at "unsynced"
                # branch — assigned_by_user_entity_id stays None.
                # Architecture supports it for future versions.
                "_created_by_user_id": psa.get("CreatedById"),
            })
        # Sort for deterministic order (SOQL doesn't guarantee
        # stable ordering of PSAs across syncs).
        decorated_psas.sort(
            key=lambda a: str(a.get("permission_set_name", "")),
        )
        user["_ps_assignments"] = decorated_psas

    # PASS 1: materialize User entities; capture entity_id_map
    # (Username → entity_id).
    entity_id_map = batched_materialize(
        ctx=ctx,
        conn=conn,
        entity_type="User",
        raw_payloads=syncable_users,
        result=result,
        return_id_map=True,
    )

    # PASS 2: build User.Id → entity_id map and resolve
    # assigned_by_user_entity_id on each PSA entry. In v1 with
    # v66.0 SOQL restricting CreatedById on PSA, the marker is
    # always None and this resolution is a no-op — every
    # assignment falls through to the "unsynced assigner"
    # branch. Architecture stays in place for future versions
    # that recover CreatedById.
    user_id_to_entity_id: dict[str, str] = {}
    for user in syncable_users:
        username = user.get("Username")
        user_id = user.get("Id")
        eid = entity_id_map.get(username) if entity_id_map else None
        if eid and user_id:
            user_id_to_entity_id[user_id] = eid

    skipped_assigner_count = 0
    for user in syncable_users:
        for assignment in user.get("_ps_assignments", []):
            created_by_id = assignment.pop(
                "_created_by_user_id", None,
            )
            if not created_by_id:
                continue
            resolved = user_id_to_entity_id.get(created_by_id)
            if resolved:
                assignment["assigned_by_user_entity_id"] = resolved
            else:
                # Assigner not in synced User set — likely a
                # system user (PlatformIntegrationUser, etc.) or
                # an inactive historical user filtered out
                # upstream. Leave the property as None (schema
                # allows Optional).
                skipped_assigner_count += 1
                logger.debug(
                    "phase_user: PSA assigner %r not in synced "
                    "User set; assigned_by left None",
                    created_by_id,
                )
    if skipped_assigner_count > 0:
        logger.info(
            "phase_user: %d PSA assignments had "
            "assigned_by_user_entity_id unresolvable (assigner "
            "not in synced User set); HAS_PERMISSION_SET property "
            "left as None on those edges.",
            skipped_assigner_count,
        )

    # Write edges. The extractors read each user's decorated
    # _profile_name and _ps_assignments (now with resolved
    # assigners where available) to build the edge targets +
    # properties.
    normalized_payloads = [
        normalize("User", u) for u in syncable_users
    ]
    materialize_edges_for_entities(
        ctx=ctx,
        conn=conn,
        source_entity_type="User",
        entity_id_map=entity_id_map,
        normalized_payloads=normalized_payloads,
        result=result,
    )

    return result


PHASE_REGISTRY["Object"] = phase_object
PHASE_REGISTRY["PicklistValueSet"] = phase_picklist_value_set
PHASE_REGISTRY["PicklistValue"] = phase_picklist_value
PHASE_REGISTRY["Field"] = phase_field
PHASE_REGISTRY["RecordType"] = phase_record_type
PHASE_REGISTRY["Layout"] = phase_layout
PHASE_REGISTRY["ValidationRule"] = phase_validation_rule
PHASE_REGISTRY["Profile"] = phase_profile
PHASE_REGISTRY["PermissionSet"] = phase_permission_set
PHASE_REGISTRY["User"] = phase_user


def get_phase_function(entity_type: str) -> PhaseFunction:
    """Look up the phase function for entity_type.

    Raises:
        KeyError: if entity_type is not registered.
    """
    if entity_type not in PHASE_REGISTRY:
        raise KeyError(f"No phase registered for entity_type={entity_type!r}")
    return PHASE_REGISTRY[entity_type]

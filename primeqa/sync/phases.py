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


PHASE_REGISTRY["Object"] = phase_object
PHASE_REGISTRY["PicklistValueSet"] = phase_picklist_value_set
PHASE_REGISTRY["PicklistValue"] = phase_picklist_value
PHASE_REGISTRY["Field"] = phase_field
PHASE_REGISTRY["RecordType"] = phase_record_type
PHASE_REGISTRY["Layout"] = phase_layout
PHASE_REGISTRY["ValidationRule"] = phase_validation_rule
PHASE_REGISTRY["Profile"] = phase_profile


def get_phase_function(entity_type: str) -> PhaseFunction:
    """Look up the phase function for entity_type.

    Raises:
        KeyError: if entity_type is not registered.
    """
    if entity_type not in PHASE_REGISTRY:
        raise KeyError(f"No phase registered for entity_type={entity_type!r}")
    return PHASE_REGISTRY[entity_type]

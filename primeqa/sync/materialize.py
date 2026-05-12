"""Batched materialization: per-chunk batched writes per design doc §7.

Phase functions accumulate raw payloads from Salesforce, then call
batched_materialize once (or in chunks of 500 by default) to write
them.

Internal flow per chunk:
  1. Compute-only: normalize + presentation + semantic_text + hash
     each raw payload into an EntityForWrite (no DB)
  2. Batched SELECT: existing entities for this chunk's external_ids
  3. Pure bucketing via batching.bucket_entities
     (new / changed / unchanged)
  4. Three batched DB writes:
     a. Multi-row INSERT for new entities (RETURNING ids)
     b. Multi-row UPDATE to close superseded rows + multi-row
        INSERT for the new versions (SCD Type 2)
     c. Multi-row UPDATE for unchanged (touch last_synced_at)
  5. Batched UPSERT to ai_enrichment_queue for new + changed
     entity_ids (one row per primitive_type, two primitives)
  6. PhaseResult counters accumulated

Chunking: per design doc §7, 500-row chunks bound peak memory at
~2.5MB per chunk (typical 5KB normalized payload × 500). For each
chunk, the full read→bucket→write→enqueue cycle runs.

Connection-threading: all DB ops execute on the conn parameter
threaded down from the phase function (which got it from
engine._phase_transaction). The phase is one transaction;
mid-chunk failure rolls back the entire phase per design doc §3
"Mechanics".

Compared to the prior per-entity materialize_entity (884a0d8):
this version emits ~3-5 SQL statements per chunk instead of 5+
per entity. For a 200-entity sync, this drops total statement
count from ~2000 → ~10, eliminating the per-row SQL
round-trip-latency bottleneck.

SCD Type 2 close-out: `valid_to_seq = ctx.logical_version_seq`
(closed-open interval). Old row valid for versions
[valid_from_seq, valid_to_seq); new row picks up at
ctx.logical_version_seq. The entities_validity_range CHECK
requires valid_to_seq > valid_from_seq, which holds since
logical_version_seq is strictly increasing across sync_runs
(at minimum by 1, so valid_to_seq > valid_from_seq even in
back-to-back syncs).

Postgres + SQLAlchemy text() interaction for batched
UPDATE/DELETE against UUID-keyed rows: two toolchain idioms
compose awkwardly. Documented here so the next batched-writes
contributor doesn't rediscover them.

  Issue 1: psycopg2 type inference. Single-row
  `WHERE col = :param` infers per-column types; array variant
  `WHERE col = ANY(:array_param)` defaults RHS to text[] with
  no implicit cast. UUID columns need explicit cast.

  Issue 2: SQLAlchemy text() parser uses `:name` for bind
  parameters, making Postgres `::` cast syntax ambiguous.
  `:ids::uuid[]` gets parsed as a malformed bind token (the
  parser eats `:ids:` greedily before seeing `:uuid[]`), so
  the `ids` binding is never registered and Postgres sees
  literal `:ids::uuid[]` in the SQL.

  Solution: use ANSI CAST form,
  `WHERE id = ANY(CAST(:ids AS uuid[]))`. Same semantics, no
  parser ambiguity.

VARCHAR columns work without a cast because :array_param's
default text[] coerces cleanly to varchar[]. Only UUID (and
likely other strict-typed columns) need the CAST.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import text

from primeqa.semantic.normalization import hash_normalized, normalize
from primeqa.semantic.semantic_text import to_semantic_text
from primeqa.sync.batching import (
    EntityForWrite,
    bucket_entities,
)
from primeqa.sync.context import SyncContext
from primeqa.sync.detail_mappers import get_detail_mapper
from primeqa.sync.presentation import to_presentation
from primeqa.sync.result import PhaseResult


DEFAULT_CHUNK_SIZE = 500


def batched_materialize(
    ctx: SyncContext,
    conn: Any,
    entity_type: str,
    raw_payloads: list[dict[str, Any]],
    result: PhaseResult,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    return_id_map: bool = False,
) -> Optional[dict[str, str]]:
    """Batched normalize → bucket → write → enqueue for one
    entity type.

    Iterates raw_payloads in chunk_size-row chunks. Counters on
    `result` accumulate across chunks. No-op if raw_payloads is empty.

    When return_id_map=True, returns a dict mapping every input
    payload's external_id to its entity_id (covering new + changed
    + unchanged buckets). Used by phase functions that need entity
    ids to construct edges in a follow-on call to
    materialize_edges_for_entities. Returns None when
    return_id_map=False (the default) so callers that don't need
    the map don't pay the accumulation cost.
    """
    aggregate_id_map: dict[str, str] = {} if return_id_map else None  # type: ignore[assignment]
    for chunk_start in range(0, len(raw_payloads), chunk_size):
        chunk = raw_payloads[chunk_start:chunk_start + chunk_size]
        chunk_id_map = _materialize_chunk(
            ctx, conn, entity_type, chunk, result,
            return_id_map=return_id_map,
        )
        if return_id_map and chunk_id_map:
            aggregate_id_map.update(chunk_id_map)
    return aggregate_id_map if return_id_map else None


def _materialize_chunk(
    ctx: SyncContext,
    conn: Any,
    entity_type: str,
    raw_chunk: list[dict[str, Any]],
    result: PhaseResult,
    return_id_map: bool = False,
) -> Optional[dict[str, str]]:
    """Process one chunk through full read→bucket→write→enqueue cycle.

    When return_id_map=True, returns {external_id: entity_id} for
    every entity in the chunk (new + changed + unchanged). Used by
    phase functions that need to construct edges from materialized
    entities — see materialize_edges_for_entities.
    """
    if not raw_chunk:
        return {} if return_id_map else None

    # 1. Compute-only stage: prepare EntityForWrite objects.
    incoming: list[EntityForWrite] = []
    for raw in raw_chunk:
        normalized = normalize(entity_type, raw)
        presentation = to_presentation(entity_type, normalized)
        semantic = to_semantic_text(entity_type, presentation)
        h = hash_normalized(normalized)
        incoming.append(EntityForWrite(
            external_id=_extract_external_id(entity_type, raw),
            normalized=normalized,
            presentation=presentation,
            semantic_text=semantic,
            hash_normalized=h,
        ))

    # 2. Batched SELECT for existing entities in this chunk.
    external_ids = [e.external_id for e in incoming]
    existing_rows = _batch_read_existing(
        conn, ctx, entity_type, external_ids,
    )

    # 3. Pure bucketing.
    buckets = bucket_entities(existing_rows, incoming)

    # 4. Three batched writes (one path per bucket type).
    now = datetime.now(timezone.utc)
    new_entity_ids: list[str] = []
    changed_new_ids: list[str] = []

    if buckets.new:
        new_entity_ids = _batch_insert_new_entities(
            conn, ctx, entity_type, buckets.new, now,
        )
        result.entities_inserted += len(buckets.new)

    if buckets.changed:
        # SCD Type 2: close old, insert new.
        _batch_close_superseded(
            conn, ctx,
            [e.prior_entity_id for e in buckets.changed],
        )
        changed_new_ids = _batch_insert_new_entities(
            conn, ctx, entity_type, buckets.changed, now,
        )
        result.entities_superseded += len(buckets.changed)

    if buckets.unchanged_ids:
        _batch_touch_existing(conn, buckets.unchanged_ids, now)
        result.entities_unchanged += len(buckets.unchanged_ids)

    # 4b. Detail-table rows for new + changed entities.
    #
    # Detail rows are tied to entity_id, which already carries SCD
    # semantics. When an entity supersedes, the new entity_id gets a
    # fresh detail row; the old detail row remains attached to the
    # old entity_id for historical reference. No detail-row work for
    # the unchanged bucket — the existing detail row stays attached
    # to the same (still-active) entity_id.
    #
    # PicklistValue's mapper needs to resolve parent PicklistValueSet
    # entity_ids; make_parent_resolver provides a per-chunk memoizing
    # closure that caps the N+1 worst-case at one query per distinct
    # parent within the chunk.
    detail_info = get_detail_mapper(entity_type)
    if detail_info is not None:
        detail_table_name, mapper = detail_info
        parent_resolver = make_parent_resolver(conn, ctx)
        detail_rows: list[dict[str, Any]] = []
        for e, eid in zip(buckets.new, new_entity_ids):
            detail_rows.append(mapper(
                normalized=e.normalized,
                entity_id=eid,
                parent_resolver=parent_resolver,
            ))
        for e, eid in zip(buckets.changed, changed_new_ids):
            detail_rows.append(mapper(
                normalized=e.normalized,
                entity_id=eid,
                parent_resolver=parent_resolver,
            ))
        if detail_rows:
            _batch_insert_details(
                conn, detail_table_name, detail_rows,
            )

    # 5. Batched UPSERT to enrichment queue for new + changed.
    entity_ids_needing_enrichment = new_entity_ids + changed_new_ids
    if entity_ids_needing_enrichment:
        _batch_upsert_queue(
            conn, entity_type, entity_ids_needing_enrichment, now,
        )
        result.embeddings_queued += len(entity_ids_needing_enrichment)
        result.summaries_queued += len(entity_ids_needing_enrichment)

    # 6. (Optional) Build {external_id: entity_id} for callers
    # that need to construct edges from this chunk's entities.
    # Skipped when return_id_map=False (most callers don't need it
    # and the dict-build cost is non-trivial at high cardinality).
    if return_id_map:
        chunk_id_map: dict[str, str] = {}
        for e, eid in zip(buckets.new, new_entity_ids):
            chunk_id_map[e.external_id] = eid
        for e, eid in zip(buckets.changed, changed_new_ids):
            chunk_id_map[e.external_id] = eid
        # Unchanged: pull from the existing_rows dict (keyed by
        # external_id, value carries 'id'). Source of truth is the
        # SELECT we already did at the top of this chunk.
        for ext_id, ext_row in existing_rows.items():
            if ext_id not in chunk_id_map:
                chunk_id_map[ext_id] = ext_row["id"]
        return chunk_id_map
    return None


def _extract_external_id(entity_type: str, raw: dict[str, Any]) -> str:
    """Per-entity-type external_id extraction.

    Object: raw['name'] (e.g., 'Account')
    PicklistValueSet: branches on raw['_source'] (per corrections-
        log §8 addendum) to namespace SVS external_ids:
          - GlobalValueSet (default): raw['FullName']
            (e.g., 'MyValueSet' or 'MyNamespace__MyValueSet' for
            managed-package GVSes)
          - StandardValueSet: f"SVS:{raw['FullName']}"
            (e.g., 'SVS:AccountSource'). The 'SVS:' prefix prevents
            collisions between a customer-named GVS (e.g.,
            'Industry') and the SVS catalog entry of the same name.
    PicklistValue: composite — f"{parent_external_id}.{valueName}"
        (e.g., 'SVS:AccountType.Analyst' or 'MyGVS.Banking').
        Parent external_id is namespaced consistently with the
        PVS source, so PicklistValue external_ids inherit GVS/SVS
        collision-avoidance automatically.

    Other types added by their respective phase cycles. KeyError
    on unknown type catches drift.
    """
    if entity_type == "Object":
        return raw["name"]
    if entity_type == "PicklistValueSet":
        source = raw.get("_source", "GlobalValueSet")
        if source == "GlobalValueSet":
            return raw["FullName"]
        # StandardValueSet — prefix to avoid GVS/SVS namespace collision.
        return f"SVS:{raw['FullName']}"
    if entity_type == "PicklistValue":
        parent = raw.get("_parent_external_id")
        value_name = raw.get("valueName")
        if not parent or not value_name:
            raise ValueError(
                f"PicklistValue requires both '_parent_external_id' "
                f"(injected by phase_picklist_value) and 'valueName' "
                f"(from Salesforce response); got parent={parent!r}, "
                f"valueName={value_name!r}"
            )
        return f"{parent}.{value_name}"
    if entity_type == "Field":
        parent = raw.get("_parent_object_api_name")
        name = raw.get("name")
        if not parent or not name:
            raise ValueError(
                f"Field requires both '_parent_object_api_name' "
                f"(injected by phase_field) and 'name' (from "
                f"Salesforce describe); got parent={parent!r}, "
                f"name={name!r}"
            )
        return f"{parent}.{name}"
    raise KeyError(
        f"No external_id extractor for entity_type "
        f"{entity_type!r}. Add to "
        f"primeqa/sync/materialize.py::_extract_external_id."
    )


def resolve_entity_id_by_external_id(
    conn: Any,
    ctx: SyncContext,
    entity_type: str,
    external_id: str,
) -> Optional[str]:
    """Resolve a single (entity_type, external_id) → entity_id.

    Returns the UUID (as a string) of the currently-active entity
    row matching the lookup, or None if not found.

    Used by detail-table mappers to resolve FK references to other
    entities (e.g., PicklistValue's parent PicklistValueSet
    entity_id for picklist_value_details.picklist_value_set_entity_id).

    Filters:
      - last_synced_from_org_id = ctx.connected_org_id (tenant +
        connected org scope)
      - entity_type = :entity_type
      - sf_api_name = :external_id
      - valid_to_seq IS NULL (currently-active row only)

    Single-row lookup. Callers materializing many children of the
    same parent type should wrap this in a memoizing closure to
    avoid N+1; see make_parent_resolver() below.
    """
    row = conn.execute(text("""
        SELECT id FROM entities
        WHERE last_synced_from_org_id = :org_id
          AND entity_type = :entity_type
          AND sf_api_name = :external_id
          AND valid_to_seq IS NULL
        LIMIT 1
    """), {
        "org_id": ctx.connected_org_id,
        "entity_type": entity_type,
        "external_id": external_id,
    }).first()
    return str(row.id) if row else None


def make_parent_resolver(
    conn: Any, ctx: SyncContext,
) -> Callable[..., Optional[str]]:
    """Build a memoizing resolver bound to this conn + ctx.

    The returned callable signature:
      resolver(entity_type=..., external_id=...) → entity_id or None

    Caches lookups for the lifetime of the closure. For a chunk
    of 500 PicklistValue rows pointing at ~95 distinct
    PicklistValueSet parents, the cache reduces 500 lookup queries
    to 95.

    Discarded at chunk boundaries (callers create a fresh resolver
    per chunk via _materialize_chunk), which is fine — parents
    don't change mid-phase and cross-chunk re-resolves are
    informationally identical.
    """
    cache: dict[tuple[str, str], Optional[str]] = {}

    def _resolve(*, entity_type: str, external_id: str) -> Optional[str]:
        key = (entity_type, external_id)
        if key not in cache:
            cache[key] = resolve_entity_id_by_external_id(
                conn, ctx, entity_type, external_id,
            )
        return cache[key]

    return _resolve


# ----------------------------------------------------------------------
# Batched DB helpers — execute on caller-supplied connection.
# ----------------------------------------------------------------------


def _batch_read_existing(
    conn: Any,
    ctx: SyncContext,
    entity_type: str,
    external_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Batched SELECT — one query, returns dict keyed by external_id.

    Filters: WHERE last_synced_from_org_id = ctx.connected_org_id
              AND entity_type = :entity_type
              AND sf_api_name = ANY(:external_ids)
              AND valid_to_seq IS NULL  -- currently-active only
    """
    if not external_ids:
        return {}
    rows = conn.execute(text("""
        SELECT id, sf_api_name, last_seed_hash
        FROM entities
        WHERE last_synced_from_org_id = :org_id
          AND entity_type = :entity_type
          AND sf_api_name = ANY(:external_ids)
          AND valid_to_seq IS NULL
    """), {
        "org_id": ctx.connected_org_id,
        "entity_type": entity_type,
        "external_ids": external_ids,
    }).fetchall()
    return {
        row.sf_api_name: {
            "id": str(row.id),
            "last_seed_hash": row.last_seed_hash,
        }
        for row in rows
    }


def _batch_insert_new_entities(
    conn: Any,
    ctx: SyncContext,
    entity_type: str,
    entities: list[EntityForWrite],
    now: datetime,
) -> list[str]:
    """Multi-row INSERT, returning ids of newly inserted rows.

    Uses INSERT ... RETURNING id. Postgres preserves insertion
    order in RETURNING, so the returned list is position-aligned
    with `entities` — useful when callers want to pair new entity
    ids back to their source EntityForWrite objects (e.g., for
    the enrichment queue UPSERT). Caller must not assume that
    guarantee across major Postgres versions, but it holds at
    v14+ which is what Railway hosts.

    Includes tenant_id via current_setting('app.tenant_id')::INT
    to satisfy entities_tenant_assertion CHECK. attributes is
    JSON-serialized and cast to JSONB to satisfy
    entities_attributes_is_object CHECK.
    """
    if not entities:
        return []

    # Build VALUES rows + parameter dict.
    values_clauses: list[str] = []
    params: dict[str, Any] = {}
    for i, e in enumerate(entities):
        display_name = (
            e.presentation.get("label")
            or e.presentation.get("name")
            or e.external_id
        )
        values_clauses.append(
            f"(:et_{i}, NULL, :sapn_{i}, :dn_{i}, "
            f"CAST(:attr_{i} AS JSONB), :vfs_{i}, NULL, "
            f"current_setting('app.tenant_id')::INT, "
            f":created_at, :last_synced_at, 'sync', "
            f":lsh_{i}, :org_id, :st_{i})"
        )
        params[f"et_{i}"] = entity_type
        params[f"sapn_{i}"] = e.external_id
        params[f"dn_{i}"] = display_name
        params[f"attr_{i}"] = json.dumps(e.normalized)
        params[f"vfs_{i}"] = ctx.logical_version_seq
        params[f"lsh_{i}"] = e.hash_normalized
        params[f"st_{i}"] = e.semantic_text
    params["created_at"] = now
    params["last_synced_at"] = now
    params["org_id"] = ctx.connected_org_id

    sql = f"""
        INSERT INTO entities (
            entity_type, sf_id, sf_api_name, display_name,
            attributes, valid_from_seq, valid_to_seq,
            tenant_id, created_at, last_synced_at, entity_origin,
            last_seed_hash, last_synced_from_org_id, semantic_text
        )
        VALUES {', '.join(values_clauses)}
        RETURNING id
    """
    rows = conn.execute(text(sql), params).fetchall()
    return [str(row.id) for row in rows]


def _batch_close_superseded(
    conn: Any,
    ctx: SyncContext,
    prior_entity_ids: list[str],
) -> None:
    """SCD Type 2 close-out: UPDATE prior rows' valid_to_seq to
    ctx.logical_version_seq.

    Closed-open interval semantics: old row valid for versions
    [valid_from_seq, valid_to_seq); new row picks up at the
    current logical_version_seq. entities_validity_range CHECK
    requires valid_to_seq > valid_from_seq, which holds because
    logical_version_seq is strictly increasing across sync_runs.

    (Note: an earlier draft of this code used
    logical_version_seq - 1 with closed-interval semantics, but
    that fails the strict-inequality CHECK when consecutive syncs
    produce consecutive seq values. Closed-open is the correct
    semantic match for the CHECK as written.)
    """
    if not prior_entity_ids:
        return
    # ::uuid[] cast required for ANY-array against UUID column.
    # See module-level note on psycopg2/Postgres type-inference
    # asymmetry below.
    conn.execute(text("""
        UPDATE entities
        SET valid_to_seq = :close_seq
        WHERE id = ANY(CAST(:ids AS uuid[]))
    """), {
        "close_seq": ctx.logical_version_seq,
        "ids": prior_entity_ids,
    })


def _batch_touch_existing(
    conn: Any,
    entity_ids: list[str],
    now: datetime,
) -> None:
    """Multi-row UPDATE: refresh last_synced_at for unchanged
    entities.

    Preserves all other fields including AI primitives. Per
    design doc §5: unchanged entities don't re-trigger enrichment.
    """
    if not entity_ids:
        return
    # ::uuid[] cast required for ANY-array against UUID column.
    # See module-level note on psycopg2/Postgres type-inference
    # asymmetry below.
    conn.execute(text("""
        UPDATE entities
        SET last_synced_at = :now
        WHERE id = ANY(CAST(:ids AS uuid[]))
    """), {"now": now, "ids": entity_ids})


def _batch_upsert_queue(
    conn: Any,
    entity_type: str,
    entity_ids: list[str],
    now: datetime,
) -> None:
    """Multi-row UPSERT to ai_enrichment_queue.

    Two queue rows per entity (embedding + summary). ON CONFLICT
    (entity_type, entity_id, primitive_type) DO UPDATE resets
    status='pending' + attempts=0 + clears prior timestamps —
    re-enables enrichment for previously-failed_permanent rows
    on new structural change.
    """
    if not entity_ids:
        return

    values_clauses: list[str] = []
    params: dict[str, Any] = {
        "now": now,
        "entity_type": entity_type,
    }
    for i, eid in enumerate(entity_ids):
        for j, primitive in enumerate(("embedding", "summary")):
            key = f"{i}_{j}"
            values_clauses.append(
                f"(:entity_type, :eid_{key}, :prim_{key}, "
                f"'pending', :now)"
            )
            params[f"eid_{key}"] = eid
            params[f"prim_{key}"] = primitive

    sql = f"""
        INSERT INTO ai_enrichment_queue
            (entity_type, entity_id, primitive_type,
             status, enqueued_at)
        VALUES {', '.join(values_clauses)}
        ON CONFLICT (entity_type, entity_id, primitive_type)
        DO UPDATE SET
            status = 'pending',
            attempts = 0,
            enqueued_at = EXCLUDED.enqueued_at,
            started_at = NULL,
            completed_at = NULL,
            error_text = NULL
    """
    conn.execute(text(sql), params)


def _batch_insert_details(
    conn: Any,
    detail_table_name: str,
    detail_rows: list[dict[str, Any]],
) -> None:
    """Multi-row INSERT into a detail table.

    detail_rows: list of column-name → value dicts produced by the
    entity_type's mapper. Every dict in the list must have the same
    key set — heterogeneous shapes would corrupt the multi-row VALUES
    template. Mappers are deterministic per-type, so this invariant
    holds by construction; defensive validation here would just be
    overhead.

    No SCD on detail rows: detail rows are 1:1 with entity_ids, and
    entity_ids already carry SCD semantics in the entities table.
    When an entity supersedes, the new entity_id gets a fresh detail
    row; the old detail row stays attached to the old (now-superseded)
    entity_id. Queries traversing historical state can join from a
    valid-as-of entity row to its detail row by entity_id.

    Tenant scoping: detail tables in substrate-1 don't carry a
    tenant_id column (they live in the tenant's schema; entities
    enforce tenant via the GUC CHECK). Detail rows inherit tenant
    via the entity_id FK — querying picklist_value_details from
    tenant_2 yields no rows because no tenant_2 entity exists. No
    explicit tenant_id needed in the INSERT.

    Empty input is a no-op (defensive against callers that build the
    list eagerly and may end up with zero rows for a no-detail-mapper
    entity_type — though _materialize_chunk gates this call already).
    """
    if not detail_rows:
        return

    columns = list(detail_rows[0].keys())
    values_clauses: list[str] = []
    params: dict[str, Any] = {}
    for i, row in enumerate(detail_rows):
        placeholders = ", ".join(f":{col}_{i}" for col in columns)
        values_clauses.append(f"({placeholders})")
        for col in columns:
            params[f"{col}_{i}"] = row[col]

    sql = f"""
        INSERT INTO {detail_table_name} ({', '.join(columns)})
        VALUES {', '.join(values_clauses)}
    """
    conn.execute(text(sql), params)


# ----------------------------------------------------------------------
# Edge writes — property-less edges
# ----------------------------------------------------------------------
#
# Identity for property-less edges (TIER_1_EDGES properties_schema=
# None) is the triple (source_entity_id, target_entity_id, edge_type).
# Supersession is binary set-difference, not hash-compare:
#
#   incoming_set − existing_active_set → INSERT (new edges)
#   existing_active_set − incoming_set → close (set valid_to_seq)
#   intersection                        → no-op (already active)
#
# When property-bearing edges land (INCLUDES_FIELD, GRANTS_*, etc.),
# extend the bucketing with a properties-hash compare for the
# intersection set — same pattern as entity supersession in
# bucket_entities(). Documented in corrections-log §11.


def _lookup_edge_category(edge_type: str) -> str:
    """Look up an edge_type's category from substrate-1's TIER_1_EDGES.

    Category is required when writing the edges row (edges.edge_category
    NOT NULL CHECK). Reading from TIER_1_EDGES (rather than hardcoding
    here) keeps substrate-1 as the single source of truth — if
    TIER_1_EDGES.BELONGS_TO.category changes from STRUCTURAL to
    something else, our writes follow automatically.
    """
    from primeqa.semantic.edges import TIER_1_EDGES
    return TIER_1_EDGES[edge_type].category


def _batch_read_existing_edges_for_sources(
    conn: Any,
    edge_type: str,
    source_entity_ids: list[str],
) -> set[tuple[str, str]]:
    """Batched SELECT — currently-active edges of `edge_type` sourced
    from any of `source_entity_ids`.

    Returns a set of (source_id, target_id) tuples. The set form is
    the natural representation for the bucketing step (set-difference
    against the incoming set).

    Filter: WHERE edge_type = ... AND source_entity_id = ANY(...)
            AND valid_to_seq IS NULL  -- currently-active only

    No org-scope filter here — edges don't carry a connected-org
    column. Scope is implicit via source_entity_ids (which were
    already filtered by org at the calling phase). Empty input
    short-circuits to empty set.
    """
    if not source_entity_ids:
        return set()
    rows = conn.execute(text("""
        SELECT source_entity_id, target_entity_id FROM edges
        WHERE edge_type = :edge_type
          AND source_entity_id = ANY(CAST(:ids AS uuid[]))
          AND valid_to_seq IS NULL
    """), {
        "edge_type": edge_type,
        "ids": source_entity_ids,
    }).fetchall()
    return {(str(row.source_entity_id), str(row.target_entity_id))
            for row in rows}


def _batch_insert_new_edges(
    conn: Any,
    ctx: SyncContext,
    edge_type: str,
    edge_category: str,
    new_pairs: list[tuple[str, str]],
) -> None:
    """Multi-row INSERT for new property-less edges.

    Columns populated explicitly:
      source_entity_id, target_entity_id, edge_type, edge_category,
      valid_from_seq
    Columns left to DB defaults:
      id (gen_random_uuid), properties ('{}'::jsonb),
      valid_to_seq (NULL), tenant_id (current_setting),
      created_at (NOW())

    Empty input is a no-op (defensive).
    """
    if not new_pairs:
        return
    values_clauses: list[str] = []
    params: dict[str, Any] = {
        "edge_type": edge_type,
        "edge_category": edge_category,
        "valid_from_seq": ctx.logical_version_seq,
    }
    for i, (sid, tid) in enumerate(new_pairs):
        values_clauses.append(
            f"(CAST(:source_{i} AS uuid), CAST(:target_{i} AS uuid), "
            f":edge_type, :edge_category, :valid_from_seq)"
        )
        params[f"source_{i}"] = sid
        params[f"target_{i}"] = tid
    sql = f"""
        INSERT INTO edges (
            source_entity_id, target_entity_id, edge_type,
            edge_category, valid_from_seq
        )
        VALUES {', '.join(values_clauses)}
    """
    conn.execute(text(sql), params)


def _batch_close_superseded_edges(
    conn: Any,
    ctx: SyncContext,
    edge_type: str,
    superseded_pairs: list[tuple[str, str]],
) -> None:
    """Multi-row UPDATE: close edges whose (source, target) pair is
    no longer in the incoming set.

    Uses Postgres tuple-IN syntax with explicit UUID casts. SQLAlchemy
    text() parses `::` as a malformed bind token (see module docstring
    on the CAST(...) AS ... ANSI form); same idiom here for the
    per-tuple values.

    Closed-open SCD: sets valid_to_seq = ctx.logical_version_seq.
    Old edge valid for [valid_from_seq, valid_to_seq); new sync
    picks up at logical_version_seq. The edges_validity_range
    CHECK (valid_to_seq IS NULL OR > valid_from_seq) holds because
    logical_version_seq is strictly increasing across sync_runs.
    """
    if not superseded_pairs:
        return
    pair_clauses: list[str] = []
    params: dict[str, Any] = {
        "edge_type": edge_type,
        "close_seq": ctx.logical_version_seq,
    }
    for i, (sid, tid) in enumerate(superseded_pairs):
        pair_clauses.append(
            f"(CAST(:source_{i} AS uuid), CAST(:target_{i} AS uuid))"
        )
        params[f"source_{i}"] = sid
        params[f"target_{i}"] = tid
    sql = f"""
        UPDATE edges
        SET valid_to_seq = :close_seq
        WHERE edge_type = :edge_type
          AND valid_to_seq IS NULL
          AND (source_entity_id, target_entity_id) IN ({', '.join(pair_clauses)})
    """
    conn.execute(text(sql), params)


def batched_materialize_property_less_edges(
    ctx: SyncContext,
    conn: Any,
    edge_writes: list[tuple[str, str, str, str]],
    result: PhaseResult,
) -> None:
    """Drive the property-less edge supersession pipeline.

    edge_writes: list of (source_id, target_id, edge_type,
                          edge_category) tuples — every edge this
                          sync wants currently active.

    Groups by edge_type internally so each edge_type's bucketing
    happens against a same-edge-type existing-set. Counters
    accumulated on `result.edges_inserted` / `result.edges_superseded`.
    Unchanged edges (in both incoming and existing sets) are
    no-ops — neither counted nor touched.
    """
    if not edge_writes:
        return

    # Group by edge_type. categories[edge_type] is consistent
    # because edge_category is derived from edge_type via TIER_1_EDGES.
    by_type: dict[str, list[tuple[str, str]]] = {}
    categories: dict[str, str] = {}
    for sid, tid, etype, ecat in edge_writes:
        by_type.setdefault(etype, []).append((sid, tid))
        categories[etype] = ecat

    for etype, pairs in by_type.items():
        incoming_set = set(pairs)
        source_ids = sorted({sid for sid, _ in pairs})

        existing_set = _batch_read_existing_edges_for_sources(
            conn, etype, source_ids,
        )

        new_pairs = incoming_set - existing_set
        superseded_pairs = existing_set - incoming_set
        # intersection = unchanged; no-op.

        if new_pairs:
            _batch_insert_new_edges(
                conn, ctx, etype, categories[etype], list(new_pairs),
            )
            result.edges_inserted += len(new_pairs)

        if superseded_pairs:
            _batch_close_superseded_edges(
                conn, ctx, etype, list(superseded_pairs),
            )
            result.edges_superseded += len(superseded_pairs)


def materialize_edges_for_entities(
    ctx: SyncContext,
    conn: Any,
    source_entity_type: str,
    entity_id_map: dict[str, str],
    normalized_payloads: list[dict[str, Any]],
    result: PhaseResult,
) -> None:
    """Compose: read edge_specs for `source_entity_type`, extract
    per-payload target external_ids, resolve target entity_ids via
    parent_resolver, then call the batched edge-write pipeline.

    entity_id_map: {external_id: entity_id} for the source entities
    just materialized (typically the return value of
    batched_materialize(..., return_id_map=True)).

    normalized_payloads: the same source entities' normalized
    payloads (used to feed the per-spec extractors). Must be aligned
    in semantics with the entity_id_map keys — each payload's
    external_id (per _extract_external_id) must match a key in the
    map.

    Targets that don't resolve to a materialized entity_id are
    silently skipped. This is the right semantics for cases like
    a Field referenceTo='Quote' where Quote was filtered out by
    Object phase's syncability filter — we don't want to write a
    dangling edge, and we also don't want to fail the whole sync.
    Skipped targets could be logged for diagnostics in a future
    cycle if customers report missing edges.
    """
    from primeqa.sync.edge_specs import get_edge_specs

    specs = get_edge_specs(source_entity_type)
    if not specs:
        return

    parent_resolver = make_parent_resolver(conn, ctx)
    edge_writes: list[tuple[str, str, str, str]] = []

    for normalized in normalized_payloads:
        source_external_id = _extract_external_id(
            source_entity_type, normalized,
        )
        source_id = entity_id_map.get(source_external_id)
        if source_id is None:
            # Source entity wasn't in this batch's id_map; skip.
            # Defensive — normally every payload in normalized_payloads
            # comes from the same batched_materialize call that built
            # the id_map.
            continue

        for spec in specs:
            edge_category = _lookup_edge_category(spec.edge_type)
            target_external_ids = spec.extract_target_external_ids(
                normalized,
            )
            for target_external_id in target_external_ids:
                target_id = parent_resolver(
                    entity_type=spec.target_entity_type,
                    external_id=target_external_id,
                )
                if target_id is None:
                    continue
                edge_writes.append((
                    source_id, target_id,
                    spec.edge_type, edge_category,
                ))

    if edge_writes:
        batched_materialize_property_less_edges(
            ctx, conn, edge_writes, result,
        )

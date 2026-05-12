"""Shared materialization logic invoked by per-entity-type phase functions.

Implements the normalize → hash → compare → write → enqueue loop
uniformly across all entity types per PHASE_2_STEP_4_SYNC_DESIGN.md
§§5-6.

Three outcomes per entity:
  - NEW: no existing row for (org, entity_type, external_id) →
    INSERT new entity, enqueue both AI primitives,
    increment entities_inserted
  - CHANGED: existing row's hash differs from current →
    SCD Type 2 supersession (close out old row's valid_to_seq,
    INSERT new row at the current logical_version_seq), enqueue
    both AI primitives for the new row, increment
    entities_superseded
  - UNCHANGED: existing row's hash matches → UPDATE last_synced_at
    only (preserve AI primitives, no re-enqueue), increment
    entities_unchanged

Hash semantics: SHA-256 of canonical JSON of the normalized
payload, via substrate-1's hash_normalized helper. Identical
hash means the structural payload is identical (post
volatile-key stripping and list-sorting); change detection is
exact rather than heuristic.

Enrichment queue UPSERT: when an entity is new or changed, the
queue rows for (entity_type, entity_id, 'embedding') and
(entity_type, entity_id, 'summary') are written via ON CONFLICT
DO UPDATE — pending→pending is harmless, succeeded→pending
resets enrichment on the new content, failed_retryable/
failed_permanent→pending re-enables retry on the new content.
This is the substitute for "manually reset failed queue rows
after sync" per design doc §6.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from primeqa.semantic.normalization import hash_normalized, normalize
from primeqa.semantic.semantic_text import to_semantic_text
from primeqa.sync.context import SyncContext
from primeqa.sync.presentation import to_presentation
from primeqa.sync.result import PhaseResult


def materialize_entity(
    ctx: SyncContext,
    entity_type: str,
    external_id: str,
    raw_payload: dict[str, Any],
    result: PhaseResult,
) -> None:
    """Materialize one Salesforce entity into the entities table.

    Mutates `result` in place to track counters and enqueue counts.
    """
    # 1. Normalize for hashing (substrate-1 single-entry).
    normalized = normalize(entity_type, raw_payload)
    new_hash = hash_normalized(normalized)

    # 2. Presentation shape for semantic_text generation.
    presentation = to_presentation(entity_type, normalized)
    semantic_text = to_semantic_text(entity_type, presentation)

    # 3. Look up existing currently-active row for this entity.
    existing = _read_existing_entity(ctx, entity_type, external_id)

    if existing is None:
        # New entity — INSERT.
        entity_id = _insert_entity(
            ctx=ctx,
            entity_type=entity_type,
            external_id=external_id,
            normalized=normalized,
            presentation=presentation,
            semantic_text=semantic_text,
            new_hash=new_hash,
        )
        _enqueue_ai_primitives(ctx, entity_type, entity_id, result)
        result.entities_inserted += 1
        return

    if existing["last_seed_hash"] != new_hash:
        # Changed entity — SCD Type 2 supersession.
        _supersede_existing_row(ctx, existing["id"])
        new_entity_id = _insert_entity(
            ctx=ctx,
            entity_type=entity_type,
            external_id=external_id,
            normalized=normalized,
            presentation=presentation,
            semantic_text=semantic_text,
            new_hash=new_hash,
        )
        _enqueue_ai_primitives(ctx, entity_type, new_entity_id, result)
        result.entities_superseded += 1
        return

    # Unchanged — refresh last_synced_at only.
    _touch_existing_row(ctx, existing["id"])
    result.entities_unchanged += 1


# ----------------------------------------------------------------------
# DB helpers — open a fresh connection per call. Per the engine's
# _phase_transaction wrapping, all of these run inside the phase's
# transactional boundary because they go through ctx.engine which is
# the same engine the phase transaction was opened from. NOTE: this
# is a simplification appropriate for the skeleton; the phase
# transaction commits the connection it was given, but these helpers
# open new connections. Phase-transaction integration is a refinement
# for the per-phase real-write cycle; for now each helper opens its
# own short-lived connection, which is correct (auto-commits per
# call) but loses cross-call rollback. Acceptable trade-off for the
# Object phase's bring-up; revisit in a subsequent cycle when
# multi-row phases land.
# ----------------------------------------------------------------------


def _tenant_id_from_schema(tenant_schema: str) -> int:
    """Parse 'tenant_1' → 1. Duplicated from engine for
    materialize.py self-sufficiency (avoids circular import)."""
    return int(tenant_schema.split("_", 1)[1])


def _open_conn(ctx: SyncContext):
    """Open a tenant-scoped connection. Sets both search_path and
    app.tenant_id GUC per the entities_tenant_assertion CHECK
    requirement."""
    conn = ctx.engine.connect()
    trans = conn.begin()
    tenant_id = _tenant_id_from_schema(ctx.tenant_schema)
    conn.execute(
        text(f'SET LOCAL search_path TO "{ctx.tenant_schema}", public')
    )
    conn.execute(
        text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_id)},
    )
    return conn, trans


def _read_existing_entity(
    ctx: SyncContext, entity_type: str, external_id: str,
) -> dict[str, Any] | None:
    """Look up the currently-active entity row for this
    (org, entity_type, external_id) tuple.

    Returns dict with id + last_seed_hash, or None if not found.
    Filters valid_to_seq IS NULL to get only the active row;
    superseded rows are ignored.
    """
    conn, trans = _open_conn(ctx)
    try:
        row = conn.execute(text("""
            SELECT id, last_seed_hash FROM entities
            WHERE last_synced_from_org_id = :org_id
              AND entity_type = :entity_type
              AND sf_api_name = :external_id
              AND valid_to_seq IS NULL
            LIMIT 1
        """), {
            "org_id": ctx.connected_org_id,
            "entity_type": entity_type,
            "external_id": external_id,
        }).fetchone()
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()

    if row is None:
        return None
    return {"id": str(row[0]), "last_seed_hash": row[1]}


def _insert_entity(
    ctx: SyncContext,
    entity_type: str,
    external_id: str,
    normalized: dict[str, Any],
    presentation: dict[str, Any],
    semantic_text: str,
    new_hash: str,
) -> str:
    """INSERT a new entity row. Returns the new entity_id.

    Sets: entity_type, sf_api_name (= external_id), display_name
    (= presentation['label'] or fallback), attributes (=
    normalized JSONB), valid_from_seq (= ctx.logical_version_seq),
    valid_to_seq NULL, tenant_id (= GUC default), created_at +
    last_synced_at = NOW(), entity_origin = 'sync',
    last_seed_hash, last_synced_from_org_id, semantic_text.
    Embedding fields all NULL — enrichment worker populates.
    """
    display_name = (
        presentation.get("label")
        or presentation.get("name")
        or external_id
    )

    conn, trans = _open_conn(ctx)
    try:
        row = conn.execute(text("""
            INSERT INTO entities (
                entity_type, sf_id, sf_api_name, display_name,
                attributes, valid_from_seq, valid_to_seq,
                tenant_id, created_at, last_synced_at,
                entity_origin, last_seed_hash,
                last_synced_from_org_id, semantic_text
            ) VALUES (
                :entity_type, NULL, :sf_api_name, :display_name,
                CAST(:attributes AS JSONB),
                :valid_from_seq, NULL,
                current_setting('app.tenant_id')::INT,
                NOW(), NOW(),
                'sync', :last_seed_hash,
                :org_id, :semantic_text
            )
            RETURNING id
        """), {
            "entity_type": entity_type,
            "sf_api_name": external_id,
            "display_name": display_name,
            "attributes": json.dumps(normalized),
            "valid_from_seq": ctx.logical_version_seq,
            "last_seed_hash": new_hash,
            "org_id": ctx.connected_org_id,
            "semantic_text": semantic_text,
        }).fetchone()
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()
    return str(row[0])


def _supersede_existing_row(ctx: SyncContext, entity_id: str) -> None:
    """SCD Type 2 close-out: UPDATE the existing row's
    valid_to_seq to ctx.logical_version_seq.

    Closed-open interval semantics: the old row was valid for
    versions [valid_from_seq, valid_to_seq); the new row picks
    up at the current logical_version_seq. The
    entities_validity_range CHECK requires valid_to_seq >
    valid_from_seq, which holds because logical_version_seq is
    strictly increasing across sync_runs.
    """
    conn, trans = _open_conn(ctx)
    try:
        conn.execute(text("""
            UPDATE entities
            SET valid_to_seq = :v
            WHERE id = :id
        """), {"v": ctx.logical_version_seq, "id": entity_id})
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()


def _touch_existing_row(ctx: SyncContext, entity_id: str) -> None:
    """Hash-match case: UPDATE last_synced_at only.

    Preserves last_seed_hash (still valid), embedding state
    (still valid since content didn't change), valid_from_seq /
    valid_to_seq (still active).
    """
    conn, trans = _open_conn(ctx)
    try:
        conn.execute(text("""
            UPDATE entities
            SET last_synced_at = NOW()
            WHERE id = :id
        """), {"id": entity_id})
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()


def _enqueue_ai_primitives(
    ctx: SyncContext,
    entity_type: str,
    entity_id: str,
    result: PhaseResult,
) -> None:
    """UPSERT enrichment queue rows for embedding + summary.

    Per design doc §6: queue keyed by (entity_type, entity_id,
    primitive_type) UNIQUE. ON CONFLICT DO UPDATE resets the row
    to pending — handles the case where a previous queue row
    exists in any non-pending state and structural change re-
    enables enrichment.

    Mutates result.embeddings_queued and result.summaries_queued.
    """
    conn, trans = _open_conn(ctx)
    try:
        for primitive_type in ("embedding", "summary"):
            conn.execute(text("""
                INSERT INTO ai_enrichment_queue
                    (entity_type, entity_id, primitive_type, status)
                VALUES (:et, :eid, :pt, 'pending')
                ON CONFLICT (entity_type, entity_id, primitive_type)
                DO UPDATE SET
                    status = 'pending',
                    attempts = 0,
                    enqueued_at = NOW(),
                    started_at = NULL,
                    completed_at = NULL,
                    error_text = NULL
            """), {"et": entity_type, "eid": entity_id, "pt": primitive_type})
            if primitive_type == "embedding":
                result.embeddings_queued += 1
            else:
                result.summaries_queued += 1
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()

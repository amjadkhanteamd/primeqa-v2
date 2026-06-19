"""§24 Feature readiness signaling.

Implements the consumer-facing readiness lifecycle described in
``PHASE_2_STEP_4_SYNC_DESIGN.md`` §6 and step 5 of §9:

  - ``connected_orgs.ai_enrichment_status`` transitions through
    ``none → structural_only → partial → complete``.
  - ``sync_runs`` enrichment counters
    (``embeddings_generated`` / ``summaries_generated`` /
    ``summaries_failed``) accumulate per worker tick.
  - ``sync_runs`` reaches its terminal state
    (``phase='done'``, ``status='success'`` or
    ``'partial_success'``, ``completed_at=NOW()``) once enrichment
    is fully drained.

The functions in this module are pure (no globals, no I/O beyond the
provided session). ``primeqa.worker.enrichment_tick`` calls them
once per affected org per tick after the embedding and summary
subticks complete.

Eligibility for the four-state model respects
``SUMMARY_ENABLED_ENTITY_TYPES = {Flow, ValidationRule}``: an org
with zero Flow + zero ValidationRule entities reaches ``complete``
once its embedding queue drains — there is no summary work to wait
on.

Terminal classification:
  - ``status='success'`` if zero ``failed_permanent`` queue rows for
    the org.
  - ``status='partial_success'`` if one or more ``failed_permanent``
    rows.

``failed_retryable`` rows do NOT count toward ``partial_success``;
they should not exist at finalization time because the
``compute_org_status`` non-terminal filter blocks the
``complete`` transition while any are present. A defensive warning
is logged if such a row is observed during finalization (indicates
a worker bug, not a partial outcome).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)


# Mirrors primeqa.sync.materialize.SUMMARY_ENABLED_ENTITY_TYPES — kept
# as a module-local constant so readiness.py has no upward dependency
# on materialize.py. If this list changes, update both sites.
SUMMARY_ENABLED_ENTITY_TYPES = frozenset({"Flow", "ValidationRule"})


# ---------------------------------------------------------------------
# Compute + write the four-state status
# ---------------------------------------------------------------------

def compute_org_status(session, connected_org_id) -> str:
    """Return the readiness state for ``connected_org_id``.

    One of ``{'none', 'structural_only', 'partial', 'complete'}``.

    Rules (per PHASE_2_STEP_4_SYNC_DESIGN.md §6):

      - ``'none'``: no completed structural sync. Either the org
        has never been synced (``last_sync_run_id IS NULL``) or its
        latest sync is still in the structural phase
        (``phase='structural'``) — model is mid-rewrite, consumers
        should fall back.
      - ``'structural_only'``: structural sync done; queue still has
        non-terminal rows AND zero rows have reached a terminal
        state. Initial state immediately after structural completion.
      - ``'partial'``: structural sync done; queue has both terminal
        and non-terminal rows. Draining in progress.
      - ``'complete'``: structural sync done; all eligible queue
        rows are in terminal state
        (``succeeded`` ∪ ``failed_permanent``), or there are zero
        eligible queue rows at all.

    Non-terminal: ``{'pending', 'in_progress', 'failed_retryable'}``.
    Terminal: ``{'succeeded', 'failed_permanent'}``.

    Queue rows are scoped to the org via the transitive join
    ``ai_enrichment_queue.entity_id → entities.id``, filtered to the
    org's active entities
    (``entities.last_synced_from_org_id = :id``,
    ``entities.valid_to_seq IS NULL``).

    Single round-trip — one ``SELECT`` with CTEs for the latest sync
    state and the per-org queue tally, then a CASE over the
    combined shape.
    """
    row = session.execute(text("""
        WITH last_run AS (
            SELECT sr.phase, sr.status
            FROM connected_orgs co
            LEFT JOIN sync_runs sr ON sr.id = co.last_sync_run_id
            WHERE co.id = :id
        ),
        queue_state AS (
            SELECT
                COUNT(*) FILTER (
                    WHERE q.status IN ('pending', 'in_progress', 'failed_retryable')
                ) AS non_terminal,
                COUNT(*) FILTER (
                    WHERE q.status IN ('succeeded', 'failed_permanent')
                ) AS terminal
            FROM ai_enrichment_queue q
            JOIN entities e ON e.id = q.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.valid_to_seq IS NULL
        )
        SELECT
            CASE
                WHEN (SELECT phase FROM last_run) IS NULL THEN 'none'
                WHEN (SELECT phase FROM last_run) = 'structural' THEN 'none'
                WHEN (SELECT non_terminal FROM queue_state) > 0
                     AND (SELECT terminal FROM queue_state) = 0
                     THEN 'structural_only'
                WHEN (SELECT non_terminal FROM queue_state) > 0
                     AND (SELECT terminal FROM queue_state) > 0
                     THEN 'partial'
                ELSE 'complete'
            END AS status
    """), {"id": str(connected_org_id)}).fetchone()
    return row[0] if row else "none"


def apply_org_status(session, connected_org_id) -> str:
    """Compute the readiness state and UPDATE
    ``connected_orgs.ai_enrichment_status`` to match.

    Returns the new (= post-UPDATE) value. Idempotent: invoking
    twice with no DB changes between calls returns the same value
    and writes the same value (no-op at the row level).
    """
    new_status = compute_org_status(session, connected_org_id)
    session.execute(text("""
        UPDATE connected_orgs
        SET ai_enrichment_status = :status
        WHERE id = :id
    """), {"status": new_status, "id": str(connected_org_id)})
    return new_status


# ---------------------------------------------------------------------
# Per-tick sync_run counter maintenance
# ---------------------------------------------------------------------

def increment_run_counters(
    session,
    connected_org_id,
    *,
    embeddings_delta: int = 0,
    summaries_delta: int = 0,
    summaries_failed_delta: int = 0,
) -> None:
    """Increment ``sync_runs.{embeddings_generated, summaries_generated,
    summaries_failed}`` for the active sync_run of an org.

    "Active" = the run id in ``connected_orgs.last_sync_run_id`` with
    ``status='running'``. If the latest run is already in a terminal
    state, this is a no-op (the run has already been finalized; we
    don't retroactively credit it).

    All three deltas default to zero. If all are zero, the UPDATE is
    skipped entirely (saves a round-trip on no-op ticks).
    """
    if embeddings_delta == 0 and summaries_delta == 0 and summaries_failed_delta == 0:
        return
    session.execute(text("""
        UPDATE sync_runs
        SET embeddings_generated = embeddings_generated + :emb,
            summaries_generated  = summaries_generated  + :sum,
            summaries_failed     = summaries_failed     + :failed
        WHERE id = (SELECT last_sync_run_id FROM connected_orgs WHERE id = :org)
          AND status = 'running'
    """), {
        "emb": int(embeddings_delta),
        "sum": int(summaries_delta),
        "failed": int(summaries_failed_delta),
        "org": str(connected_org_id),
    })


def resolve_active_sync_run_id(session, connected_org_id) -> Optional[str]:
    """Return the org's active sync_run id — ``connected_orgs.last_sync_run_id``,
    the same pointer ``increment_run_counters`` credits.

    Used by the enrichment worker (1d cost-telemetry, migration 058) to
    attribute embedding + summary LLM cost to the sync_run that drove the
    work. Reads ``last_sync_run_id`` directly (NOT a ``status='running'``
    scan) per this module's own guidance — that pointer is the canonical
    active run even if its row has already been finalized; the cost still
    belongs to it.

    Returns the run id as a string (UUID canonical form), or ``None`` if the
    org has no recorded run. Best-effort: never raises into the worker.
    """
    try:
        rid = session.execute(text("""
            SELECT last_sync_run_id FROM connected_orgs WHERE id = :org
        """), {"org": str(connected_org_id)}).scalar()
        return str(rid) if rid else None
    except Exception:
        return None


# ---------------------------------------------------------------------
# Terminal-state finalization
# ---------------------------------------------------------------------

def maybe_finalize_run(session, connected_org_id) -> bool:
    """If the org has reached ``ai_enrichment_status='complete'`` and
    its active sync_run is still ``running``, finalize that run:

      - ``phase = 'done'``
      - ``status = 'success'`` (zero ``failed_permanent`` rows for
        this org) or ``'partial_success'`` (one or more)
      - ``completed_at = NOW()``

    Returns ``True`` iff a sync_run was finalized; ``False``
    otherwise. Idempotent: re-invoking after finalization is a no-op
    because the WHERE clause requires ``status='running'``.

    Note: If a new sync_run starts mid-drain (between the old run's
    structural completion and full enrichment drain), the old run's
    ``status='running'`` may persist briefly. Once that org's queue
    reaches ``'complete'`` under the combined queue state, this
    function finalizes the latest (now-current) run. The old run's
    status remains ``'running'`` indefinitely from the schema's
    perspective; consumer queries should read
    ``connected_orgs.last_sync_run_id`` to find the active run
    rather than scanning ``sync_runs`` by status.
    """
    status = session.execute(text("""
        SELECT ai_enrichment_status
        FROM connected_orgs
        WHERE id = :id
    """), {"id": str(connected_org_id)}).scalar()
    if status != "complete":
        return False

    # Defensive observability: failed_retryable should not be present
    # at finalization time — compute_org_status puts it in the
    # non-terminal bucket which prevents 'complete'. If we see one
    # here, the previous compute saw 'complete' but a retryable
    # failure has crept in since. Worker-bug indicator, not a partial
    # outcome.
    retryable_count = session.execute(text("""
        SELECT COUNT(*)
        FROM ai_enrichment_queue q
        JOIN entities e ON e.id = q.entity_id
        WHERE e.last_synced_from_org_id = :id
          AND e.valid_to_seq IS NULL
          AND q.status = 'failed_retryable'
    """), {"id": str(connected_org_id)}).scalar()
    if retryable_count:
        log.warning(
            "maybe_finalize_run: org=%s reached 'complete' but %d "
            "failed_retryable rows are present — worker-bug indicator",
            connected_org_id, retryable_count,
        )

    has_failed_perm = session.execute(text("""
        SELECT EXISTS (
            SELECT 1
            FROM ai_enrichment_queue q
            JOIN entities e ON e.id = q.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.valid_to_seq IS NULL
              AND q.status = 'failed_permanent'
        )
    """), {"id": str(connected_org_id)}).scalar()
    terminal = "partial_success" if has_failed_perm else "success"

    result = session.execute(text("""
        UPDATE sync_runs
        SET status = :terminal,
            phase = 'done',
            completed_at = NOW()
        WHERE id = (SELECT last_sync_run_id FROM connected_orgs WHERE id = :org)
          AND status = 'running'
    """), {"terminal": terminal, "org": str(connected_org_id)})
    return (result.rowcount or 0) > 0


# ---------------------------------------------------------------------
# Operational requeue (D-180)
# ---------------------------------------------------------------------

def requeue_failed_enrichment(session, connected_org_id) -> int:
    """Reset every ``failed_permanent`` enrichment queue row for an
    org's active entities back to ``pending`` so the worker re-attempts
    them (D-180).

    Drains rows stranded by a *systemic* failure — e.g. the keyless
    worker bug (D-179). Once the env's LLM connection carries the provider
    keys, those terminal rows would otherwise never retry:
    ``materialize._batch_upsert_queue`` only resets a row on a NEW
    structural change to its entity, and an unchanged org re-enqueues
    nothing (design §5). This is the product-level remedy that replaces
    hand-run SQL.

    Scope: rows joined to the org's active entities
    (``entities.last_synced_from_org_id = org`` AND ``valid_to_seq IS
    NULL``) with ``status='failed_permanent'``. The reset clears
    ``attempts`` and the prior run timestamps / error text so each row
    looks freshly enqueued. When ≥1 row is reset, recompute
    ``ai_enrichment_status`` so the org flips off ``complete`` (pending
    rows now exist) — keeping readiness honest before the next tick.

    Note: a sync_run that was already finalized (``status='partial_success'``
    before this requeue) is INTENTIONALLY not re-opened — ``maybe_finalize_run``
    only acts on a ``running`` run, and re-finalizing a terminal run would
    contradict the "consumers read ``connected_orgs.last_sync_run_id``, not
    ``sync_runs.status``" contract. The honest post-drain state lives in
    ``ai_enrichment_status`` (recomputed here + by the worker); the historical
    run row stays as its audit record. A subsequent structural sync gets a fresh
    run that finalizes ``success`` once the queue is clean.

    Returns the number of rows requeued. Joins the caller's transaction;
    the caller commits.
    """
    result = session.execute(text("""
        UPDATE ai_enrichment_queue
        SET status = 'pending',
            attempts = 0,
            started_at = NULL,
            completed_at = NULL,
            error_text = NULL
        WHERE id IN (
            SELECT q.id
            FROM ai_enrichment_queue q
            JOIN entities e ON e.id = q.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.valid_to_seq IS NULL
              AND q.status = 'failed_permanent'
        )
    """), {"id": str(connected_org_id)})
    count = result.rowcount or 0
    if count:
        apply_org_status(session, connected_org_id)
    return count


def count_failed_enrichment(session, connected_org_id) -> int:
    """Count an org's active-entity ``failed_permanent`` enrichment rows
    (D-180) — drives the panel's conditional "Re-run enrichment (N)"
    affordance. Same scoping as :func:`requeue_failed_enrichment`."""
    return int(session.execute(text("""
        SELECT COUNT(*)
        FROM ai_enrichment_queue q
        JOIN entities e ON e.id = q.entity_id
        WHERE e.last_synced_from_org_id = :id
          AND e.valid_to_seq IS NULL
          AND q.status = 'failed_permanent'
    """), {"id": str(connected_org_id)}).scalar() or 0)

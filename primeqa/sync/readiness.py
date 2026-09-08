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
    (``entities.connected_org_id = :id``,
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
            WHERE e.connected_org_id = :id
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
    describe_calls_delta: int = 0,
) -> None:
    """Increment ``sync_runs.{embeddings_generated, summaries_generated,
    summaries_failed, describe_calls}`` for the active sync_run of an org.

    "Active" = the run id in ``connected_orgs.last_sync_run_id`` with
    ``status='running'``. If the latest run is already in a terminal
    state, this is a no-op (the run has already been finalized; we
    don't retroactively credit it).

    All four deltas default to zero. If all are zero, the UPDATE is
    skipped entirely (saves a round-trip on no-op ticks).

    #4a: ``describe_calls`` (migration 20260622_0010) is referenced ONLY when
    ``describe_calls_delta`` is non-zero — so the embedding/summary worker path
    (which passes 0) never touches that column and can't regress on a tenant whose
    schema is not yet migrated. The engine's describe-count flush, which does pass
    a non-zero delta, is savepoint-guarded by its caller.
    """
    if (embeddings_delta == 0 and summaries_delta == 0
            and summaries_failed_delta == 0 and describe_calls_delta == 0):
        return
    set_clauses = [
        "embeddings_generated = embeddings_generated + :emb",
        "summaries_generated  = summaries_generated  + :sum",
        "summaries_failed     = summaries_failed     + :failed",
    ]
    params = {
        "emb": int(embeddings_delta),
        "sum": int(summaries_delta),
        "failed": int(summaries_failed_delta),
        "org": str(connected_org_id),
    }
    if describe_calls_delta:
        set_clauses.append("describe_calls = describe_calls + :dc")
        params["dc"] = int(describe_calls_delta)
    session.execute(text(f"""
        UPDATE sync_runs
        SET {', '.join(set_clauses)}
        WHERE id = (SELECT last_sync_run_id FROM connected_orgs WHERE id = :org)
          AND status = 'running'
    """), params)


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
        WHERE e.connected_org_id = :id
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
            WHERE e.connected_org_id = :id
              AND e.valid_to_seq IS NULL
              AND q.status = 'failed_permanent'
        )
    """), {"id": str(connected_org_id)}).scalar()
    # Phase 2 Slice B: a run that surfaced ≥1 GENUINE metadata-fetch gap (a typed
    # failure that dropped real model data, flushed to permission_gaps by the
    # engine at structural completion) also finalizes partial_success — the sync
    # DID finish, but with a recorded, typed loss rather than success-with-silent-
    # loss. (permission_gaps has DEFAULT 0; migrate-first guarantees the column.)
    permission_gaps = session.execute(text("""
        SELECT COALESCE(permission_gaps, 0)
        FROM sync_runs
        WHERE id = (SELECT last_sync_run_id FROM connected_orgs WHERE id = :org)
    """), {"org": str(connected_org_id)}).scalar() or 0
    terminal = ("partial_success"
                if (has_failed_perm or permission_gaps > 0) else "success")

    result = session.execute(text("""
        UPDATE sync_runs
        SET status = :terminal,
            phase = 'done',
            completed_at = NOW()
        WHERE id = (SELECT last_sync_run_id FROM connected_orgs WHERE id = :org)
          AND status = 'running'
    """), {"terminal": terminal, "org": str(connected_org_id)})
    return (result.rowcount or 0) > 0


def record_run_gaps(session, connected_org_id, permission_gaps, gap_details) -> None:
    """Phase 2 Slice B: flush the run's surfaced metadata-fetch gaps to the active
    sync_run — ``permission_gaps`` (the genuine-gap count) + ``gap_details`` (the
    full classified list, JSONB). Mirrors :func:`increment_run_counters`'s "active
    run" targeting (``last_sync_run_id`` + ``status='running'``). A no-op if the
    run is already terminal. Called by the engine at structural completion, before
    :func:`maybe_finalize_run` reads ``permission_gaps`` to decide the status."""
    import json
    session.execute(text("""
        UPDATE sync_runs
        SET permission_gaps = :n,
            gap_details = CAST(:details AS JSONB)
        WHERE id = (SELECT last_sync_run_id FROM connected_orgs WHERE id = :org)
          AND status = 'running'
    """), {
        "n": int(permission_gaps),
        "details": json.dumps(gap_details) if gap_details else None,
        "org": str(connected_org_id),
    })


# ---------------------------------------------------------------------
# Stranded-run reaper (sync_runs stuck 'running')
# ---------------------------------------------------------------------

def reap_stranded_sync_runs(
    session, *, stale_minutes: int, final_phase: str,
) -> int:
    """Finalize ``sync_runs`` stranded in ``status='running'`` to ``'failure'``.

    A run that completed ALL structural phases (``last_completed_phase =
    final_phase``, i.e. ``ENTITY_ORDER[-1]``) but was never finalized — its
    enrichment never drained to :func:`maybe_finalize_run` (worker death mid-
    enrichment, or the §26 cross-org finalize race documented above) — stays
    ``'running'`` forever. The job reaper only touches ``s1_sync_jobs``, and the
    *resume* mechanism deliberately EXCLUDES structurally-complete runs
    (``create_or_get_job`` / the enqueuer's needs-sync scan both gate on
    ``last_completed_phase IS DISTINCT FROM 'Flow'``), so nothing else ever closes
    them. This finalizes such a run (older than ``stale_minutes`` by
    ``started_at``) to ``status='failure'`` + ``completed_at=NOW()`` so the
    freshness reader, the cost roll-up, and the UI stop seeing a perpetual ghost.

    **Deliberately narrow + collision-free.** ONLY structurally-complete runs are
    touched. A run that is NOT structurally complete (``last_completed_phase`` !=
    ``final_phase``) is resume-eligible — the enqueuer will re-create a job that
    resumes it — so it is LEFT ALONE: forcing it to ``'failure'`` would corrupt the
    in-flight resume (a resumed run whose status was flipped to terminal is never
    re-finalized to ``'success'`` by :func:`maybe_finalize_run`'s ``status='running'``
    guard, leaving a usable org model permanently marked failed).

    ``stale_minutes`` should be GENEROUS (the caller defaults to hours) — well
    beyond the longest legitimate structural+enrichment run — so a genuinely
    in-flight enrichment drain is never reaped. Timing uses the DB clock (NOW()),
    avoiding app/DB skew. Returns the number of runs reaped.
    """
    result = session.execute(text("""
        UPDATE sync_runs
        SET status = 'failure',
            completed_at = NOW(),
            error_message = COALESCE(error_message || ' | ', '')
                || 'reaped: structural sync completed but the run was never '
                || 'finalized (enrichment stranded); stuck in running past the '
                || 'stale window'
        WHERE status = 'running'
          AND last_completed_phase = :final_phase
          AND started_at < NOW() - make_interval(mins => :stale_minutes)
    """), {"final_phase": final_phase, "stale_minutes": int(stale_minutes)})
    return result.rowcount or 0


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
    (``entities.connected_org_id = org`` AND ``valid_to_seq IS
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
            WHERE e.connected_org_id = :id
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
        WHERE e.connected_org_id = :id
          AND e.valid_to_seq IS NULL
          AND q.status = 'failed_permanent'
    """), {"id": str(connected_org_id)}).scalar() or 0)


def count_enrichment_progress(session, connected_org_id) -> dict:
    """Enrichment lane done/total for an org (UI Pass 2 progress bar). ``done`` =
    terminal rows (``succeeded`` ∪ ``failed_permanent``); ``total`` = all eligible
    queue rows. Same per-org scoping as :func:`count_failed_enrichment` (the queue
    joined to the org's ACTIVE entities). One cheap aggregate; pure read.

    Returns ``{"done": int, "total": int}`` — the honest real-time tally for
    "Enrichment {done}/{total}". ``total=0`` (no queue rows yet) is a valid state
    the UI renders as 0/0."""
    row = session.execute(text("""
        SELECT
            COUNT(*) FILTER (
                WHERE q.status IN ('succeeded', 'failed_permanent')
            ) AS done,
            COUNT(*) AS total
        FROM ai_enrichment_queue q
        JOIN entities e ON e.id = q.entity_id
        WHERE e.connected_org_id = :id
          AND e.valid_to_seq IS NULL
    """), {"id": str(connected_org_id)}).fetchone()
    return {"done": int(row[0] or 0), "total": int(row[1] or 0)} if row \
        else {"done": 0, "total": 0}


# ---------------------------------------------------------------------
# Step B (D-479 B / LLD_STEP_B_STALENESS_PIN §a) — the ONE decision-facing
# "current sequence" resolver. Org-REQUIRED: there is no org-less mode.
# ---------------------------------------------------------------------

from dataclasses import dataclass  # noqa: E402  (section-local import)
from datetime import datetime  # noqa: E402

SEQ_CURRENT = "CURRENT"
SEQ_CANNOT_DETERMINE = "CANNOT_DETERMINE"
SEQ_SOURCE_ORG_CURRENT = "org_current"
SEQ_SOURCE_RUN_STAMP = "run_stamp"          # RESERVED — Step 2 adds the stamp
SEQ_AXIS = "org_sequence"                    # never the claim-version axis
REASON_ORG_UNBOUND = "org_unbound"
REASON_ORG_NEVER_SYNCED = "org_never_synced"


@dataclass(frozen=True)
class SequenceResolution:
    """What the decision layer may know about an org's current S1 sequence.

    ``axis`` is always ``org_sequence`` — the org changed under the claim
    (→ ``grounding.stale``). Claim-version drift (the claim or recipe
    changed under the run) is NOT this object's business: it lives on the
    evidence row (``approved_seq``, ``latest_run.version_unknown``,
    ``superseded_newer_run``) and is graded by ``version_currency``.
    ``source`` is ``org_current`` today; ``run_stamp`` is reserved for
    Step 2 (the org sequence persisted on each run)."""
    state: str
    current_seq: Optional[int]
    as_of: Optional[datetime]
    source: str
    axis: str = SEQ_AXIS
    connected_org_id: Optional[str] = None
    reason: Optional[str] = None

    def as_dict(self) -> dict:
        return {"state": self.state, "current_seq": self.current_seq,
                "as_of": self.as_of.isoformat() if self.as_of else None,
                "source": self.source, "axis": self.axis,
                "connected_org_id": self.connected_org_id,
                "reason": self.reason}


def resolve_current_sequence(session, *, connected_org_id) -> SequenceResolution:
    """The ONLY place a decision-facing current S1 sequence is computed.

    ``connected_org_id`` is keyword-only and required by the signature; a
    caller that resolved env→org and got ``None`` passes ``None`` and
    receives the recorded refusal (``CANNOT_DETERMINE / org_unbound``) —
    never a tenant-wide number. Bound → ONE read over the org's OWN rows
    (org-less ``logical_versions`` rows can never match the uuid WHERE, so
    they are excluded by construction); no row → ``CANNOT_DETERMINE /
    org_never_synced``. Never raises on a refusal path; a DB error
    propagates to the caller's best-effort wrapper."""
    if connected_org_id is None:
        return SequenceResolution(
            state=SEQ_CANNOT_DETERMINE, current_seq=None, as_of=None,
            source=SEQ_SOURCE_ORG_CURRENT, reason=REASON_ORG_UNBOUND)
    row = session.execute(text(
        "SELECT version_seq, created_at FROM logical_versions "
        "WHERE connected_org_id = CAST(:org AS uuid) "
        "ORDER BY version_seq DESC LIMIT 1"),
        {"org": str(connected_org_id)}).first()
    if row is None:
        return SequenceResolution(
            state=SEQ_CANNOT_DETERMINE, current_seq=None, as_of=None,
            source=SEQ_SOURCE_ORG_CURRENT,
            connected_org_id=str(connected_org_id),
            reason=REASON_ORG_NEVER_SYNCED)
    return SequenceResolution(
        state=SEQ_CURRENT, current_seq=int(row[0]), as_of=row[1],
        source=SEQ_SOURCE_ORG_CURRENT, connected_org_id=str(connected_org_id))


# ---------------------------------------------------------------------
# Step 2 (D-479 step 2 / LLD_STEP_2_CONTEMPORANEITY §b) — readiness for
# (claim, environment) from the RUN STAMP, targeted through what the
# claim reads. ``source = run_stamp``: the source Step B reserved.
# ---------------------------------------------------------------------

READY_NEVER_RUN = "NEVER_RUN"
READY_STALE = "STALE"
READY_CURRENT = "CURRENT"
READY_CANNOT_DETERMINE = "CANNOT_DETERMINE"
READINESS_STATES = (READY_NEVER_RUN, READY_STALE, READY_CURRENT,
                    READY_CANNOT_DETERMINE)
REASON_UNSTAMPED = "unstamped"
REASON_NO_COVERAGE = "no_coverage"

#: The TA's Fork-3 sentence, verbatim — it carries the action.
SENTENCE_UNSTAMPED = ("Freshness unknown — this run predates run-level "
                      "environment stamping. Run again to establish current "
                      "readiness.")
SENTENCE_NO_COVERAGE = ("This test's reads are not recorded, so its currency "
                        "cannot be determined.")
SENTENCE_NEVER_RUN = "No run in this environment."


@dataclass(frozen=True)
class ReadinessResolution:
    """Readiness of ONE (claim, environment) on the ORG axis — never the
    claim-version axis, which stays a separate fact on the evidence row."""
    state: str
    source: str = SEQ_SOURCE_RUN_STAMP
    axis: str = SEQ_AXIS
    run_id: Optional[str] = None
    stamp_seq: Optional[int] = None
    current_seq: Optional[int] = None
    connected_org_id: Optional[str] = None
    changed_reads: tuple = ()
    reason: Optional[str] = None

    @property
    def sentence(self) -> str:
        if self.state == READY_NEVER_RUN:
            return SENTENCE_NEVER_RUN
        if self.state == READY_CANNOT_DETERMINE:
            if self.reason == REASON_UNSTAMPED:
                return SENTENCE_UNSTAMPED
            if self.reason == REASON_NO_COVERAGE:
                return SENTENCE_NO_COVERAGE
            return ("Freshness unknown — the org's current sequence could not "
                    f"be resolved ({self.reason}).")
        if self.state == READY_STALE:
            names = ", ".join(f"{t} {n}" for t, n, _ in self.changed_reads[:5])
            more = (f" (+{len(self.changed_reads) - 5} more)"
                    if len(self.changed_reads) > 5 else "")
            return (f"{len(self.changed_reads)} thing(s) this test reads changed "
                    f"after this run (org seq {self.stamp_seq} → "
                    f"{self.current_seq}): {names}{more}")
        return f"Current against org seq {self.current_seq}."

    def as_dict(self) -> dict:
        return {"state": self.state, "source": self.source, "axis": self.axis,
                "run_id": self.run_id, "stamp_seq": self.stamp_seq,
                "current_seq": self.current_seq,
                "connected_org_id": self.connected_org_id,
                "changed_reads": [list(c) for c in self.changed_reads],
                "reason": self.reason, "sentence": self.sentence}


_CHANGED_READS_SQL = """
    WITH cov AS (
        SELECT c.claim_test_id, c.entity_type, c.entity_id
        FROM test_claim_coverage c
        WHERE c.claim_test_id = CAST(:claim AS uuid)
    )
    SELECT cov.entity_type, e.sf_api_name, 'entity_closed' AS what
    FROM cov JOIN entities e ON e.id = cov.entity_id
    WHERE e.connected_org_id = CAST(:org AS uuid)
      AND e.valid_to_seq IS NOT NULL AND e.valid_to_seq > :since
    UNION ALL
    SELECT cov.entity_type, e.sf_api_name,
           CASE WHEN g.valid_to_seq IS NOT NULL AND g.valid_to_seq > :since
                THEN 'edge_closed' ELSE 'edge_opened' END AS what
    FROM cov JOIN entities e ON e.id = cov.entity_id
    JOIN edges g ON (g.source_entity_id = cov.entity_id
                     OR g.target_entity_id = cov.entity_id)
    WHERE g.connected_org_id = CAST(:org AS uuid)
      AND ((g.valid_to_seq IS NOT NULL AND g.valid_to_seq > :since)
           OR g.valid_from_seq > :since)
"""


def covered_reads_changed(session, *, claim_test_id, connected_org_id,
                          since_seq: int) -> tuple:
    """The claim's covered reads that changed in ``connected_org_id`` AFTER
    ``since_seq``: a covered entity row closed after it (SCD Type 2: the read
    was superseded), or an edge on a covered entity closed or appeared after
    it. Returns ``((entity_type, sf_api_name, what), …)``; empty = nothing the
    claim reads moved. An org change touching none of the claim's reads is
    invisible here — never "org moved → all stale"."""
    rows = session.execute(text(_CHANGED_READS_SQL), {
        "claim": str(claim_test_id), "org": str(connected_org_id),
        "since": int(since_seq)}).all()
    seen, out = set(), []
    for et, name, what in rows:
        key = (et, name, what)
        if key not in seen:
            seen.add(key); out.append(key)
    return tuple(out)


def _has_coverage(session, claim_test_id) -> bool:
    return bool(session.execute(text(
        "SELECT 1 FROM test_claim_coverage WHERE claim_test_id = CAST(:c AS uuid) "
        "LIMIT 1"), {"c": str(claim_test_id)}).scalar())


def _latest_run(session, claim_test_id, environment_id):
    return session.execute(text(
        "SELECT CAST(run_id AS text) AS run_id, org_version_seq, "
        "       CAST(connected_org_id AS text) AS connected_org_id "
        "FROM s4_execution_runs "
        "WHERE claim_test_id = CAST(:c AS uuid) AND environment_id = :e "
        "ORDER BY finished_at DESC LIMIT 1"),
        {"c": str(claim_test_id), "e": int(environment_id)}).mappings().first()


def resolve_run_readiness(session, *, claim_test_id, environment_id,
                          connected_org_id=None) -> ReadinessResolution:
    """Readiness for ONE (claim, environment), the rules in order (§b):

    1. no run in this environment → NEVER_RUN;
    2. the latest run carries no stamp → CANNOT_DETERMINE / unstamped;
    3. the org's current sequence (the Step B resolver) refuses →
       CANNOT_DETERMINE with its reason;
    4. the claim has no recorded reads → CANNOT_DETERMINE / no_coverage
       (never CURRENT by absence);
    5. a covered read changed after the stamp → STALE, the reads named;
       else CURRENT.

    ``connected_org_id`` may be supplied by a caller that already resolved
    the environment's org; otherwise the run's own stamp names it."""
    run = _latest_run(session, claim_test_id, environment_id)
    if run is None:
        return ReadinessResolution(state=READY_NEVER_RUN)
    if run["org_version_seq"] is None or run["connected_org_id"] is None:
        return ReadinessResolution(state=READY_CANNOT_DETERMINE,
                                   run_id=run["run_id"], reason=REASON_UNSTAMPED)
    org = connected_org_id or run["connected_org_id"]
    cur = resolve_current_sequence(session, connected_org_id=org)
    if cur.state != SEQ_CURRENT:
        return ReadinessResolution(state=READY_CANNOT_DETERMINE,
                                   run_id=run["run_id"],
                                   stamp_seq=int(run["org_version_seq"]),
                                   connected_org_id=str(org), reason=cur.reason)
    if not _has_coverage(session, claim_test_id):
        return ReadinessResolution(state=READY_CANNOT_DETERMINE,
                                   run_id=run["run_id"],
                                   stamp_seq=int(run["org_version_seq"]),
                                   current_seq=cur.current_seq,
                                   connected_org_id=str(org),
                                   reason=REASON_NO_COVERAGE)
    changed = covered_reads_changed(session, claim_test_id=claim_test_id,
                                    connected_org_id=org,
                                    since_seq=int(run["org_version_seq"]))
    return ReadinessResolution(
        state=READY_STALE if changed else READY_CURRENT,
        run_id=run["run_id"], stamp_seq=int(run["org_version_seq"]),
        current_seq=cur.current_seq, connected_org_id=str(org),
        changed_reads=changed)


def resolve_run_readiness_bulk(session, pairs) -> dict:
    """``{(claim_test_id_str, environment_id): ReadinessResolution}`` for many
    pairs. Per-pair reads today (the pair sets on every page are small —
    a release's claims × its environments); the org's current sequence is
    resolved once per org."""
    out = {}
    org_cache: dict = {}
    for claim_test_id, environment_id in pairs:
        run = _latest_run(session, claim_test_id, environment_id)
        key = (str(claim_test_id), int(environment_id))
        if run is None:
            out[key] = ReadinessResolution(state=READY_NEVER_RUN); continue
        if run["org_version_seq"] is None or run["connected_org_id"] is None:
            out[key] = ReadinessResolution(state=READY_CANNOT_DETERMINE,
                                           run_id=run["run_id"],
                                           reason=REASON_UNSTAMPED); continue
        org = run["connected_org_id"]
        if org not in org_cache:
            org_cache[org] = resolve_current_sequence(session, connected_org_id=org)
        cur = org_cache[org]
        stamp = int(run["org_version_seq"])
        if cur.state != SEQ_CURRENT:
            out[key] = ReadinessResolution(state=READY_CANNOT_DETERMINE,
                                           run_id=run["run_id"], stamp_seq=stamp,
                                           connected_org_id=str(org),
                                           reason=cur.reason); continue
        if not _has_coverage(session, claim_test_id):
            out[key] = ReadinessResolution(state=READY_CANNOT_DETERMINE,
                                           run_id=run["run_id"], stamp_seq=stamp,
                                           current_seq=cur.current_seq,
                                           connected_org_id=str(org),
                                           reason=REASON_NO_COVERAGE); continue
        changed = covered_reads_changed(session, claim_test_id=claim_test_id,
                                        connected_org_id=org, since_seq=stamp)
        out[key] = ReadinessResolution(
            state=READY_STALE if changed else READY_CURRENT,
            run_id=run["run_id"], stamp_seq=stamp, current_seq=cur.current_seq,
            connected_org_id=str(org), changed_reads=changed)
    return out

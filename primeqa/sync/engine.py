"""SyncEngine — orchestrates a sync_run end-to-end.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §§2-4.

Responsibilities:
  - Asserts ENTITY_ORDER respects schema FKs at startup (fail-fast
    before any data is written; see fk_assertion.py)
  - Creates a fresh sync_run row, or resumes an existing one from
    last_completed_phase
  - Runs phases per ENTITY_ORDER, one transaction each
  - Updates sync_run.last_completed_phase after each phase commit
  - Marks sync_run status='success' or 'failure' per outcome of the
    structural phases. (status='partial_success' is reserved for the
    enrichment worker — structural-only failure produces 'failure'.)
  - Sets connected_orgs.ai_enrichment_status='structural_only' on
    successful structural completion (the worker further advances
    this through 'partial' and 'complete' per §24 / readiness.py)

Does NOT run AI enrichment — that's a separate process per design
doc §6, implemented in primeqa/worker.py:enrichment_tick (§23).
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator

from sqlalchemy import text

from primeqa.sync.context import SyncContext
from primeqa.sync.exceptions import (
    EntityOrderViolation,
    PhaseExecutionError,
    SyncAlreadyRunningError,
    SyncEngineError,
)
from primeqa.sync.fk_assertion import (
    ENTITY_ORDER,
    assert_entity_order_respects_schema_fks,
)
from primeqa.sync.phases import get_phase_function
from primeqa.sync.result import PhaseResult


logger = logging.getLogger(__name__)

# Salesforce retains SetupAuditTrail entries for ~180 days. A skip trusts
# "no rows since the watermark" as proof of "no changes" — but only while the
# watermark is recent enough that SF still retains the whole window since it.
# A watermark older than this forces a full sync: changes could have aged out
# of the trail, so "no rows" is uncertainty, and the gate never skips on
# uncertainty. 90 days is a conservative half of the retention window.
MAX_SKIP_WATERMARK_AGE = timedelta(days=90)


@dataclass
class _SkipDecision:
    """Outcome of the org-level skip gate (1b.1).

    ``skip`` — True iff the whole sync can be skipped (org has a watermark AND
    SetupAuditTrail confirms no setup changes since it). FAIL-SAFE default is
    False (run the full sync) on any error / missing watermark / ambiguity.
    ``reason`` — human-readable, logged + (on skip) recorded.
    ``server_time`` — Salesforce server time captured at the probe (fetch start),
    reused to advance the watermark on a successful full sync; None when the
    probe couldn't capture it (then the watermark is NOT advanced — never on
    uncertainty).
    """
    skip: bool
    reason: str
    server_time: datetime | None
    # 1b.2: the watermark the gate READ (the delta "since"), distinct from
    # ``server_time`` (the new time captured this run). Set only when the
    # watermark was loaded AND the probe captured a server time — i.e. when a
    # delta is both possible (a "since" exists) and the run will advance the
    # watermark afterward. None on any fail-safe (unreadable watermark / probe
    # failure / no watermark) → that run full-fetches. Consumed by run_sync to
    # set ctx.delta_since on the fresh, non-skip path.
    since_watermark: datetime | None = None


class SyncEngine:
    """Orchestrates a sync_run from end to end.

    Per PHASE_2_STEP_4_SYNC_DESIGN.md §§2-4. See module docstring
    for the responsibility summary.
    """

    def __init__(
        self,
        engine_db: Any,
        sf_client: Any,
        tenant_schema: str,
    ) -> None:
        """Initialize engine, run FK assertion immediately.

        Args:
            engine_db: SQLAlchemy Engine bound to the tenant's
                database. Per-phase transactions are opened from this.
            sf_client: SalesforceClient instance (or compatible).
                Phases use this to fetch Salesforce data.
            tenant_schema: e.g., 'tenant_1'. Used for search_path
                scoping on connections.

        Raises:
            EntityOrderViolation: ENTITY_ORDER violates the actual
                FK declarations in the schema. Fails fast before any
                sync_run row is created.
        """
        self.db = engine_db
        self.sf = sf_client
        self.tenant_schema = tenant_schema

        # Run the assertion at construction — fail fast before any
        # sync_run row is created. Per design doc §4.
        assert_entity_order_respects_schema_fks(engine_db, tenant_schema)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_sync(
        self,
        connected_org_id: str,
        resume_sync_run_id: str | None = None,
        *,
        should_abort: Callable[[], str | None] | None = None,
        on_run_started: Callable[[str], None] | None = None,
    ) -> str:
        """Run a sync_run end-to-end.

        Args:
            connected_org_id: UUID of the connected_orgs row being
                synced.
            resume_sync_run_id: UUID of an existing sync_run row to
                resume. If provided, the engine reads
                last_completed_phase from that row and starts from the
                next phase. If None, a fresh sync_run row is created.
            should_abort: optional fence probe (D-341), consulted before
                each phase. Returning a non-None reason aborts the run
                cleanly — no failure stamp, the run stays resumable. The
                consumer wires this to "is my job still the active
                claimant?" so a reaped zombie stops at the next phase
                boundary instead of racing its own resume.
            on_run_started: optional callback invoked with the sync_run_id
                as soon as the run row exists (fresh or resumed) — the
                consumer stamps the job's resume anchor up front so a
                mid-run shutdown requeues a resumable job (D-341).

        Returns:
            The sync_run_id (whether newly created or resumed).

        Raises:
            SyncAlreadyRunningError: another live session holds this
                org's sync advisory lock (a concurrent run is in flight).
                Raised before any row is written.
            SyncEngineError: fatal failure (DB connectivity, etc.).
                Per-phase failures do NOT raise here — they're
                captured in sync_run.status and error_message.
        """
        # D-341 layer 1: org-scoped advisory lock, acquired BEFORE any row
        # is written (a refused run leaves zero droppings). Held on a
        # dedicated session for the whole run: lock lifetime = session
        # lifetime = process liveness, so a dead worker's lock auto-releases
        # while a live zombie's lock refuses the concurrent resume.
        lock_conn = self._acquire_org_lock(connected_org_id)
        sync_run_id: str | None = None
        pass_t0 = time.monotonic()
        try:
            # 1. Create or resume sync_run row.
            if resume_sync_run_id is None:
                sync_run_id, logical_version_seq = self._create_sync_run_row(
                    connected_org_id,
                )
            else:
                sync_run_id = resume_sync_run_id
                logical_version_seq = self._get_logical_version_seq(
                    sync_run_id)

            if on_run_started is not None:
                # Best-effort: the anchor stamp must never fail the run.
                try:
                    on_run_started(sync_run_id)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "on_run_started callback failed for sync_run=%s: %s",
                        sync_run_id, e,
                    )

            self._begin_pass(sync_run_id)
            return self._run_structural_pass(
                sync_run_id, logical_version_seq, connected_org_id,
                resume=resume_sync_run_id is not None,
                should_abort=should_abort,
            )
        finally:
            # Pass accounting (D-341): one finally covers every exit —
            # skip-gate return, structural-complete, phase-failure, fenced
            # abort, and raises (incl. KeyboardInterrupt on shutdown).
            if sync_run_id is not None:
                self._record_pass_seconds(
                    sync_run_id, int(time.monotonic() - pass_t0))
            self._release_org_lock(lock_conn)

    def _run_structural_pass(
        self,
        sync_run_id: str,
        logical_version_seq: int,
        connected_org_id: str,
        *,
        resume: bool,
        should_abort: Callable[[], str | None] | None = None,
    ) -> str:
        """Steps 2–5 of a run_sync pass (context, skip gate, phases,
        finalize). Split out of :meth:`run_sync` so the advisory lock +
        pass accounting wrap it in one try/finally (D-341)."""
        # 2. Build context.
        ctx = self._build_context(
            sync_run_id, connected_org_id, logical_version_seq,
        )

        # 3. Determine where to start (resumability).
        last_completed = self._get_last_completed_phase(sync_run_id)
        if last_completed is None:
            start_index = 0
        else:
            try:
                start_index = ENTITY_ORDER.index(last_completed) + 1
            except ValueError:
                raise SyncEngineError(
                    f"sync_run {sync_run_id} has last_completed_phase="
                    f"{last_completed!r} which is not in ENTITY_ORDER. "
                    f"Schema/code drift detected."
                )

        # 3.5 Org-level skip gate (1b.1) — FRESH runs ONLY. A fresh run re-fetches
        # the WHOLE org, so the SetupAuditTrail probe time is an honest single
        # "as-of" for the entire org: it both decides the skip and, on a full sync,
        # advances connected_orgs.setup_audit_watermark to that captured SF server
        # time. This is the ONLY path that advances the watermark.
        #
        # A RESUME is deliberately excluded — it neither skips nor advances the
        # watermark. A resume continues from a mid-phase checkpoint and does NOT
        # re-fetch the early phases, so it cannot honestly establish a single
        # "as-of" time for the org. Advancing the watermark on a resume would let a
        # change to an already-fetched early-phase entity (one that landed after
        # that phase ran but before the resume's probe) be silently SKIPPED on the
        # next sync — the T2 silent-miss gap. So a resume leaves the watermark
        # exactly as it was, including NULL. A perpetually-resuming org bootstraps
        # its watermark via the reaper (which clears stranded runs back into fresh
        # syncs), not by advancing on resume.
        #
        # FAIL SAFE: any error / missing watermark / ambiguity → run the full sync
        # (the gate never skips on uncertainty).
        setup_audit_server_time: datetime | None = None
        if not resume:
            decision = self._evaluate_skip_gate(ctx, connected_org_id)
            setup_audit_server_time = decision.server_time
            if decision.skip:
                self._mark_sync_run_skipped(sync_run_id, connected_org_id)
                logger.info(
                    "sync_run=%s org=%s SKIPPED: %s",
                    sync_run_id, connected_org_id, decision.reason,
                )
                return sync_run_id
            logger.info(
                "sync_run=%s org=%s running full sync (fresh): %s",
                sync_run_id, connected_org_id, decision.reason,
            )
            # 1b.2: offer the delta "since" to delta-safe phases. FRESH runs
            # only (this branch). The watermark the gate read; None when there
            # is no watermark or the probe couldn't capture a server time →
            # every category full-fetches (fail-safe). A RESUME (else branch)
            # leaves ctx.delta_since at its None default — it never deltas (and
            # never advances the watermark, per D-250), because a resume does
            # not re-fetch the early phases and cannot establish a single as-of.
            ctx.delta_since = decision.since_watermark
        else:
            logger.info(
                "sync_run=%s org=%s running full sync (resume) — "
                "watermark left unchanged", sync_run_id, connected_org_id,
            )

        # 4. Run phases.
        failed_phase: str | None = None
        first_error: PhaseExecutionError | None = None

        for phase_name in ENTITY_ORDER[start_index:]:
            if should_abort is not None:
                reason = should_abort()
                if reason:
                    # D-341 layer 2: fenced abort. NO failure stamp, NO
                    # structural-complete — status stays 'running' and
                    # last_completed_phase stays at the last committed
                    # phase, exactly the resumable shape the enqueuer's
                    # carry-forward targets.
                    logger.warning(
                        "sync_run=%s org=%s ABORTED before phase %r: %s "
                        "(leaving run resumable)",
                        sync_run_id, connected_org_id, phase_name, reason,
                    )
                    return sync_run_id
            try:
                with self._phase_transaction(
                    sync_run_id, phase_name,
                ) as conn:
                    phase_fn = get_phase_function(phase_name)
                    result = phase_fn(ctx, conn)
                    if not result.succeeded:
                        # Phase reported failure via result rather than
                        # raising. Convert to PhaseExecutionError to
                        # trigger rollback.
                        raise PhaseExecutionError(
                            phase_name=phase_name,
                            sync_run_id=sync_run_id,
                            original_exception=Exception(
                                result.error_message or "(no error_message)"
                            ),
                        )
                    self._advance_last_completed_phase(
                        conn, sync_run_id, phase_name, result,
                    )
            except PhaseExecutionError as e:
                failed_phase = phase_name
                first_error = e
                logger.error(
                    "Phase %r failed (sync_run=%s); halting sync",
                    phase_name, sync_run_id,
                )
                break  # halt sync on first phase failure
            except Exception as e:
                # Wrap any unexpected exception as PhaseExecutionError
                # so the failure-mode handling is uniform.
                failed_phase = phase_name
                first_error = PhaseExecutionError(
                    phase_name=phase_name,
                    sync_run_id=sync_run_id,
                    original_exception=e,
                )
                logger.error(
                    "Unexpected exception in phase %r (sync_run=%s): %s",
                    phase_name, sync_run_id, e,
                )
                break

        # 5. Finalize sync_run status.
        if failed_phase is None:
            self._mark_sync_run_structural_complete(
                sync_run_id, connected_org_id,
                setup_audit_watermark=setup_audit_server_time,
                # #4a: describe-class API calls accumulated by the SF client across
                # this run's phases (one client per run → naturally run-scoped).
                describe_calls=int(getattr(self.sf, "describe_calls", 0) or 0),
            )
        else:
            # first_error is not None here (failed_phase truthy
            # implies the except branch ran)
            assert first_error is not None
            self._mark_sync_run_failed(
                sync_run_id, failed_phase, first_error,
            )

        return sync_run_id

    # ------------------------------------------------------------------
    # Internal: org advisory lock + pass accounting (D-341)
    # ------------------------------------------------------------------

    def _acquire_org_lock(self, connected_org_id: str) -> Any:
        """Acquire the org's sync advisory lock on a dedicated AUTOCOMMIT
        connection and return that connection (held for the whole run).

        AUTOCOMMIT matters: the connection sits idle for the duration of
        the run; without it the SELECT would open an implicit transaction
        and the session would sit idle-in-transaction for minutes.
        Advisory locks are session-scoped, so AUTOCOMMIT is safe.

        Raises SyncAlreadyRunningError when another live session holds
        the lock (a concurrent run_sync for this org is in flight).
        """
        lock_conn = self.db.connect().execution_options(
            isolation_level="AUTOCOMMIT")
        try:
            acquired = lock_conn.execute(text(
                "SELECT pg_try_advisory_lock("
                "hashtextextended('s1_sync:' || CAST(:org AS text), 0))"
            ), {"org": str(connected_org_id)}).scalar()
        except Exception:
            lock_conn.invalidate()
            raise
        if not acquired:
            lock_conn.close()  # nothing held — safe to return to the pool
            raise SyncAlreadyRunningError(
                f"org {connected_org_id}: sync advisory lock held by "
                f"another session — a concurrent sync is in flight"
            )
        return lock_conn

    def _release_org_lock(self, lock_conn: Any) -> None:
        """Release the org lock and return the connection to the pool.

        On ANY release error, invalidate the connection (closes the DBAPI
        socket → the server ends the session → the lock drops) and swallow
        with a warning — a release failure must never mask the run's own
        outcome, and a lock-holding session must never re-enter the pool
        (the pool's checkin hook resets GUCs, not advisory locks).
        """
        try:
            lock_conn.execute(text("SELECT pg_advisory_unlock_all()"))
            lock_conn.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "could not cleanly release the org sync lock (%s) — "
                "invalidating the connection so the session ends", e,
            )
            try:
                lock_conn.invalidate()
            except Exception:  # noqa: BLE001
                pass

    def _begin_pass(self, sync_run_id: str) -> None:
        """Count this engine pass on the run (fresh AND resume). Best-effort
        telemetry on the describe_calls pattern — a tenant missing the
        attempt_passes column (migration 20260708_0010 not yet applied)
        degrades to "not counted" rather than failing the sync."""
        try:
            with self._connect() as conn:
                conn.execute(text(
                    "UPDATE sync_runs "
                    "SET attempt_passes = attempt_passes + 1 "
                    "WHERE id = :id"
                ), {"id": sync_run_id})
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "could not count pass for sync_run=%s (%s) — migration "
                "20260708_0010 may not be applied to this schema yet",
                sync_run_id, e,
            )

    def _record_pass_seconds(self, sync_run_id: str, seconds: int) -> None:
        """Accumulate this pass's wall time into sync_runs.active_seconds
        (the honest duration — excludes reaper dead time between passes).
        Best-effort telemetry, never load-bearing."""
        try:
            with self._connect() as conn:
                conn.execute(text(
                    "UPDATE sync_runs "
                    "SET active_seconds = active_seconds + :s "
                    "WHERE id = :id"
                ), {"s": max(0, int(seconds)), "id": sync_run_id})
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "could not record pass seconds for sync_run=%s (%s)",
                sync_run_id, e,
            )

    # ------------------------------------------------------------------
    # Internal: sync_run row lifecycle
    # ------------------------------------------------------------------

    def _create_sync_run_row(
        self, connected_org_id: str,
    ) -> tuple[str, int]:
        """Three-step initialization: sync_run row, then
        logical_versions row with back-reference, then back-link
        sync_run.logical_version_seq.

        All three statements run in the same transaction so partial
        state is impossible.

        Chicken-and-egg ordering rationale: sync_run must exist
        before logical_versions.created_by_sync_run_id can reference
        it; logical_version_seq must be allocated before sync_run
        can store it. Solution: create sync_run with NULL
        logical_version_seq, allocate logical_version (referencing
        the now-existing sync_run_id), UPDATE sync_run with the
        allocated seq.

        Returns:
            (sync_run_id, logical_version_seq) tuple.
        """
        with self._connect() as conn:
            # 1. Create sync_run row (logical_version_seq starts as
            # NULL; will be back-filled in step 3).
            sync_run_row = conn.execute(text("""
                INSERT INTO sync_runs (source_org_id, status, phase)
                VALUES (:org_id, 'running', 'structural')
                RETURNING id
            """), {"org_id": connected_org_id}).fetchone()
            if sync_run_row is None:
                raise SyncEngineError(
                    f"INSERT into sync_runs returned no row for "
                    f"connected_org_id={connected_org_id!r}"
                )
            sync_run_id = str(sync_run_row[0])

            # 2. Allocate logical_versions row with back-reference.
            logical_version_seq = self._allocate_logical_version(
                conn, sync_run_id, connected_org_id,
            )

            # 3. Back-link sync_run.logical_version_seq.
            conn.execute(text("""
                UPDATE sync_runs
                SET logical_version_seq = :v
                WHERE id = :id
            """), {"v": logical_version_seq, "id": sync_run_id})

        return sync_run_id, logical_version_seq

    def _allocate_logical_version(
        self, conn: Any, sync_run_id: str, connected_org_id: str,
    ) -> int:
        """Allocate a new logical_versions row for this sync_run.

        Per Object phase cycle decision: one version per sync_run.
        All entities written by this run share its version_seq for
        valid_from_seq. Hash-change supersession sets prior rows'
        valid_to_seq to the NEW row's valid_from_seq (closed-open
        interval semantics — old row valid for [valid_from, valid_to);
        constraint valid_to > valid_from holds since version_seq is
        strictly increasing).

        version_type='sync_run' per migration 20260512_0020.
        version_name='sync_run_{uuid}' is deterministic and never
        collides (UNIQUE constraint on version_name).

        connected_org_id stamps the per-org partition key (per-org
        Slice 1): every version this run produces belongs to the org
        being synced. NULLABLE in the schema — a genesis/bootstrap or
        test-fixture version with no sync_run is org-less.
        """
        result = conn.execute(text("""
            INSERT INTO logical_versions
                (version_name, version_type, description,
                 created_by_sync_run_id, connected_org_id)
            VALUES
                (:name, 'sync_run', :description, :sync_run_id,
                 :connected_org_id)
            RETURNING version_seq
        """), {
            "name": f"sync_run_{sync_run_id}",
            "description": f"Allocated by sync_run {sync_run_id}",
            "sync_run_id": sync_run_id,
            "connected_org_id": connected_org_id,
        })
        row = result.fetchone()
        if row is None:
            raise SyncEngineError(
                f"INSERT into logical_versions returned no row "
                f"for sync_run {sync_run_id}"
            )
        return int(row[0])

    def _get_logical_version_seq(self, sync_run_id: str) -> int:
        """SELECT logical_version_seq FROM sync_runs WHERE id = :id.

        Used when resuming an existing sync_run. Raises if the row
        doesn't exist or if logical_version_seq is still NULL
        (which would mean the sync_run was created but never had
        its version allocated — a bug in _create_sync_run_row).
        """
        with self._connect() as conn:
            row = conn.execute(text("""
                SELECT logical_version_seq FROM sync_runs
                WHERE id = :id
            """), {"id": sync_run_id}).fetchone()
        if row is None:
            raise SyncEngineError(
                f"sync_run {sync_run_id} not found"
            )
        if row[0] is None:
            raise SyncEngineError(
                f"sync_run {sync_run_id} has NULL logical_version_seq; "
                f"cannot resume without an allocated version"
            )
        return int(row[0])

    def _get_last_completed_phase(
        self, sync_run_id: str,
    ) -> str | None:
        """SELECT last_completed_phase FROM sync_runs WHERE id = :id."""
        with self._connect() as conn:
            row = conn.execute(text("""
                SELECT last_completed_phase FROM sync_runs
                WHERE id = :id
            """), {"id": sync_run_id}).fetchone()
        if row is None:
            raise SyncEngineError(
                f"sync_run {sync_run_id} not found"
            )
        return row[0]

    def _advance_last_completed_phase(
        self,
        conn: Any,
        sync_run_id: str,
        phase_name: str,
        result: PhaseResult,
    ) -> None:
        """UPDATE last_completed_phase + accumulate counter columns.

        Called from inside a phase transaction (caller's responsibility
        — this UPDATE runs in the same transaction as the phase's
        entity writes, ensuring atomicity between data writes and
        phase-marker advancement).
        """
        conn.execute(text("""
            UPDATE sync_runs
            SET last_completed_phase = :phase,
                entities_inserted = entities_inserted + :ei,
                entities_superseded = entities_superseded + :es,
                entities_unchanged = entities_unchanged + :eu,
                edges_inserted = edges_inserted + :gi,
                edges_superseded = edges_superseded + :gs
            WHERE id = :id
        """), {
            "phase": phase_name,
            "ei": result.entities_inserted,
            "es": result.entities_superseded,
            "eu": result.entities_unchanged,
            "gi": result.edges_inserted,
            "gs": result.edges_superseded,
            "id": sync_run_id,
        })

    def _mark_sync_run_structural_complete(
        self,
        sync_run_id: str,
        connected_org_id: str,
        setup_audit_watermark: datetime | None = None,
        describe_calls: int = 0,
    ) -> None:
        """All structural phases completed — advance phase to
        'enrichment' (the enrichment worker will pick up from here)
        and update connected_orgs.ai_enrichment_status.

        ``setup_audit_watermark`` (1b.1): the Salesforce server time captured at
        this run's fetch start. When given, it is written to
        ``connected_orgs.setup_audit_watermark`` so the next sync's skip gate
        compares against an SF-server-time value. When None (the SetupAuditTrail
        probe couldn't capture SF time), the watermark is simply not touched —
        never advanced on uncertainty. The watermark write is BEST-EFFORT
        (savepoint-guarded): it can never fail the load-bearing finalization, so
        the sync still completes even if the watermark column isn't migrated yet.

        Note: sync_run.status remains 'running' here. The terminal
        status ('success' / 'partial_success' / 'failure') is set by
        the enrichment worker on its own completion, per design
        doc §6 + §2 state machine.

        After this call, the enrichment worker
        (``primeqa.worker.enrichment_tick``) picks up the queue,
        drains it, and finalizes the sync_run to ``phase='done'`` +
        ``status='success'`` / ``'partial_success'`` once enrichment
        is complete — see
        ``primeqa.sync.readiness.maybe_finalize_run`` (§24).

        §26: after seeding ``ai_enrichment_status='structural_only'``,
        call ``readiness.apply_org_status`` + ``maybe_finalize_run``
        for every org in the tenant whose ``sync_run`` is still
        ``'running'``. Single-org case: exactly one running run →
        one iteration → behaviorally identical to single-org
        pre-§26. Multi-org case: catches the D-030 cross-org
        attribution shift.

        D-030's touch path can shift entity attribution across
        orgs. When sync B completes and rotates
        ``last_synced_from_org_id`` to org B, org A's sync_run
        (still 'running') loses its queue-row credits. Without
        this loop, org A's sync_run stays 'running' indefinitely
        (the worker's readiness wiring only advances orgs whose
        entities it touches).

        Concurrency safety: ``compute_org_status`` returns
        ``'none'`` for ``phase='structural'`` (sync still
        in-flight), so syncs in progress are correctly excluded
        from finalization. ``sr.status='running'`` matches both
        ``phase='structural'`` (early) and ``phase='enrichment'``
        (post-this-call) running runs; the per-run check via
        ``compute_org_status`` filters correctly.
        """
        from primeqa.sync import readiness  # local import: avoid
        # an engine→readiness import cycle at module load time.

        with self._connect() as conn:
            conn.execute(text("""
                UPDATE sync_runs
                SET phase = 'enrichment'
                WHERE id = :id
            """), {"id": sync_run_id})
            # Load-bearing: enrichment status + active-run pointer. These MUST
            # commit for the run to finalize, so they are NOT coupled to the
            # optional watermark write below.
            conn.execute(text("""
                UPDATE connected_orgs
                SET ai_enrichment_status = 'structural_only',
                    last_sync_run_id = :run_id
                WHERE id = :id
            """), {"run_id": sync_run_id, "id": connected_org_id})

            # #4a: credit the describe-class API call count accumulated by the SF
            # client across this run's phases into sync_runs.describe_calls. Done
            # HERE (not before finalize) because last_sync_run_id now points at this
            # run + status is still 'running', so increment_run_counters credits the
            # correct run. SAVEPOINT-guarded + non-zero-only: a tenant missing the
            # describe_calls column (migration 20260622_0010 not yet applied) rolls
            # back to the savepoint and degrades to "not credited" rather than
            # FAILING finalization. Telemetry, never load-bearing.
            if describe_calls:
                try:
                    with conn.begin_nested():
                        readiness.increment_run_counters(
                            conn, connected_org_id,
                            describe_calls_delta=describe_calls,
                        )
                except Exception as e:
                    logger.warning(
                        "could not credit describe_calls=%s for org %s (%s) — "
                        "finalizing without it (migration 20260622_0010 may not be "
                        "applied to this schema yet)",
                        describe_calls, connected_org_id, e,
                    )

            # Phase 2 Slice B: flush the swallowed metadata-fetch gaps the SF
            # client accumulated this run (a permission/FLS denial that dropped
            # real model data) to sync_runs.{permission_gaps, gap_details}. Done
            # HERE — last_sync_run_id points at this run + status is still
            # 'running', so record_run_gaps targets the right run, BEFORE
            # maybe_finalize_run reads permission_gaps to decide success vs
            # partial_success. SAVEPOINT-guarded like the describe_calls flush.
            gaps = list(getattr(self.sf, "metadata_gaps", []) or [])
            if gaps:
                from primeqa.integrations.failure_taxonomy import genuine_gap_count
                genuine = genuine_gap_count(gaps)
                try:
                    with conn.begin_nested():
                        readiness.record_run_gaps(
                            conn, connected_org_id, genuine, gaps)
                except Exception as e:
                    # CORR-1: record_run_gaps is the ONLY writer of
                    # sync_runs.permission_gaps, which maybe_finalize_run (below)
                    # reads (DEFAULT 0) to choose success vs partial_success.
                    # Swallowing a failed write when a GENUINE gap was surfaced
                    # would leave permission_gaps at 0 and finalize a false
                    # 'success' despite dropped Salesforce metadata. Migrate-first
                    # guarantees the column (readiness.maybe_finalize_run reads it
                    # unconditionally), so this is a real error, not a benign
                    # missing column — FAIL LOUD: re-raise so the sync finalizes
                    # 'failed' (→ job retry) rather than lying. A benign-only run
                    # (genuine == 0) doesn't change the success/partial decision,
                    # so its visibility-only gap_details write stays best-effort.
                    if genuine > 0:
                        raise
                    logger.warning(
                        "could not record %d benign metadata gap(s) for org %s "
                        "(%s) — finalizing without them (gap_details is "
                        "visibility-only when no gap is genuine)",
                        len(gaps), connected_org_id, e,
                    )

            # Best-effort: advance the setup-audit watermark (1b.1 optimization
            # metadata). Wrapped in a SAVEPOINT so a missing column — e.g. tenant
            # migration 20260619_0010 not yet applied to this schema (Railway has
            # no deploy-time migration step; migrations are applied manually) —
            # rolls back to the savepoint and degrades to "not advanced" instead
            # of aborting the transaction and FAILING the whole sync at
            # finalization. Mirrors the skip-gate READ's fail-safe; the watermark
            # is only an optimization hint, never load-bearing.
            if setup_audit_watermark is not None:
                try:
                    with conn.begin_nested():
                        conn.execute(text("""
                            UPDATE connected_orgs
                            SET setup_audit_watermark = :watermark
                            WHERE id = :id
                        """), {"watermark": setup_audit_watermark,
                               "id": connected_org_id})
                except Exception as e:
                    logger.warning(
                        "could not advance setup_audit_watermark for org %s "
                        "(%s) — finalizing the sync without it (migration "
                        "20260619_0010 may not be applied to this schema yet)",
                        connected_org_id, e,
                    )

            # §26: readiness functions accept session-like with
            # .execute(); a SQLAlchemy connection works the same
            # way (both apply_org_status and maybe_finalize_run
            # only call .execute() + .scalar() / .fetchone() /
            # .rowcount). Operates in the existing transaction;
            # commit on context exit.
            running_orgs = conn.execute(text("""
                SELECT co.id::text AS org_id
                FROM connected_orgs co
                JOIN sync_runs sr ON sr.id = co.last_sync_run_id
                WHERE sr.status = 'running'
            """)).fetchall()
            for row in running_orgs:
                readiness.apply_org_status(conn, row.org_id)
                readiness.maybe_finalize_run(conn, row.org_id)

    def _mark_sync_run_failed(
        self,
        sync_run_id: str,
        failed_phase: str,
        error: PhaseExecutionError,
    ) -> None:
        """Mark sync_run.status='failure' with error context.

        Phase 2 Slice A: classify the underlying exception into a typed
        ``failure_category`` (+ the Salesforce ``sf_error_code`` when present) via
        the shared taxonomy, persisted ALONGSIDE the existing freetext
        ``error_message`` (the freetext is kept). ``source_org_id`` is already on
        ``sync_runs``, so the typed failure is queryable per org with no extra work."""
        from primeqa.integrations.failure_taxonomy import classify_failure

        failure_category, sf_error_code = classify_failure(error.original)
        with self._connect() as conn:
            conn.execute(text("""
                UPDATE sync_runs
                SET status = 'failure',
                    completed_at = NOW(),
                    error_message = :msg,
                    failure_category = :failure_category,
                    sf_error_code = :sf_error_code
                WHERE id = :id
            """), {
                "msg": f"phase={failed_phase}: {error.original}",
                "failure_category": failure_category,
                "sf_error_code": sf_error_code,
                "id": sync_run_id,
            })

    # ------------------------------------------------------------------
    # Internal: org-level skip gate (1b.1)
    # ------------------------------------------------------------------

    def _load_setup_audit_watermark(
        self, connected_org_id: str,
    ) -> datetime | None:
        """SELECT setup_audit_watermark FROM connected_orgs WHERE id = :id.

        Returns the stored Salesforce server-time watermark, or None when the
        org has never had a confirmed sync (NULL).
        """
        with self._connect() as conn:
            row = conn.execute(text("""
                SELECT setup_audit_watermark FROM connected_orgs
                WHERE id = :id
            """), {"id": connected_org_id}).fetchone()
        return row[0] if row else None

    def _evaluate_skip_gate(
        self, ctx: SyncContext, connected_org_id: str,
    ) -> _SkipDecision:
        """Decide whether this org's sync can be skipped (1b.1).

        FAIL SAFE — never raises; any error / missing watermark / ambiguity
        yields ``skip=False`` (run the full sync). Returns the SF server time
        captured at the probe so the caller can advance the watermark after a
        successful full sync.
        """
        try:
            watermark = self._load_setup_audit_watermark(connected_org_id)
        except Exception as e:
            logger.warning(
                "skip-gate: could not read setup_audit_watermark for org %s "
                "(%s) — running full sync (fail-safe)", connected_org_id, e,
            )
            return _SkipDecision(False, "watermark unreadable", None)

        try:
            has_changes, server_time = ctx.sf_client.probe_setup_audit_trail(
                watermark)
        except Exception as e:
            logger.warning(
                "skip-gate: SetupAuditTrail probe failed for org %s (%s) — "
                "running full sync (fail-safe)", connected_org_id, e,
            )
            return _SkipDecision(False, "SetupAuditTrail probe failed", None)

        if watermark is None:
            # First confirmed sync: no "since" → full-fetch + bootstrap the
            # watermark to server_time. since_watermark stays None (== watermark).
            return _SkipDecision(
                False, "no watermark (first confirmed sync)", server_time,
                since_watermark=watermark)
        if has_changes:
            # Setup changed since the watermark → run, but delta-safe categories
            # can delta from the watermark (since_watermark) and advance it.
            return _SkipDecision(
                False, "setup changes detected since watermark", server_time,
                since_watermark=watermark)
        # Probe came back clean, but only trust it if the watermark is recent
        # enough that SetupAuditTrail still retains the window since it (SF keeps
        # ~180 days). An older watermark means changes could have aged out of the
        # trail → "no rows" is uncertainty → run the full sync. Measure age
        # against SF's clock (server_time) when available, else local now (a
        # coarse 90-day threshold tolerates clock skew).
        reference_now = server_time or datetime.now(timezone.utc)
        if reference_now - watermark > MAX_SKIP_WATERMARK_AGE:
            # Too old for the org-level SKIP (SetupAuditTrail rows may have aged
            # out), but a per-category LastModifiedDate delta is still correct
            # for any age, so since_watermark is still offered.
            return _SkipDecision(
                False,
                f"watermark {watermark.isoformat()} older than "
                f"{MAX_SKIP_WATERMARK_AGE.days}d — outside SetupAuditTrail "
                f"retention, running full sync",
                server_time, since_watermark=watermark)
        return _SkipDecision(
            True, f"no setup changes since {watermark.isoformat()}",
            server_time, since_watermark=watermark)

    def _mark_sync_run_skipped(
        self, sync_run_id: str, connected_org_id: str,
    ) -> None:
        """Finalize a skipped sync_run (1b.1).

        Terminal ``status='success'`` (so the D-183 staleness clock, which reads
        ``status IN ('success','partial_success')``, counts the run as a recent
        successful sync), ``phase='done'``, ``completed_at=NOW()``, ``skipped=true``.
        Counter columns stay at their 0 defaults — no phases ran. Bumps
        ``connected_orgs.last_sync_run_id`` (the skip is the org's latest run, so
        it shows up in history) but leaves ``setup_audit_watermark``,
        ``ai_enrichment_status``, and all entities/edges UNCHANGED, and enqueues
        no enrichment.
        """
        with self._connect() as conn:
            conn.execute(text("""
                UPDATE sync_runs
                SET status = 'success',
                    phase = 'done',
                    skipped = true,
                    completed_at = NOW()
                WHERE id = :id
            """), {"id": sync_run_id})
            conn.execute(text("""
                UPDATE connected_orgs
                SET last_sync_run_id = :run_id
                WHERE id = :id
            """), {"run_id": sync_run_id, "id": connected_org_id})

    # ------------------------------------------------------------------
    # Internal: context + transactions
    # ------------------------------------------------------------------

    def _build_context(
        self,
        sync_run_id: str,
        connected_org_id: str,
        logical_version_seq: int,
    ) -> SyncContext:
        """Construct the SyncContext passed to phase functions."""
        return SyncContext(
            sf_client=self.sf,
            engine=self.db,
            sync_run_id=sync_run_id,
            connected_org_id=connected_org_id,
            tenant_schema=self.tenant_schema,
            logical_version_seq=logical_version_seq,
        )

    def _tenant_id_from_schema(self) -> int:
        """Parse 'tenant_1' → 1.

        Used by _connect to set the app.tenant_id GUC so that
        entities_tenant_assertion CHECK constraints pass on
        INSERTs.
        """
        prefix = "tenant_"
        if not self.tenant_schema.startswith(prefix):
            raise SyncEngineError(
                f"tenant_schema {self.tenant_schema!r} does not "
                f"match the tenant_<int> naming convention"
            )
        try:
            return int(self.tenant_schema[len(prefix):])
        except ValueError as e:
            raise SyncEngineError(
                f"tenant_schema {self.tenant_schema!r} does not end "
                f"with an integer tenant id: {e}"
            )

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        """Open a connection with tenant search_path AND
        app.tenant_id GUC set.

        Per substrate-1's tenant-isolation pattern (primeqa/semantic/
        connection.py): every entity-table operation requires
        BOTH search_path (so unqualified table references resolve)
        AND the app.tenant_id GUC (so the entities_tenant_assertion
        CHECK constraint passes during INSERT).
        """
        tenant_id = self._tenant_id_from_schema()
        with self.db.connect() as conn:
            with conn.begin():
                conn.execute(
                    text(f'SET LOCAL search_path TO "{self.tenant_schema}", public')
                )
                conn.execute(
                    text("SET LOCAL app.tenant_id = :tid"),
                    {"tid": str(tenant_id)},
                )
                yield conn

    @contextmanager
    def _phase_transaction(
        self,
        sync_run_id: str,
        phase_name: str,
    ) -> Iterator[Any]:
        """Wrap a phase in its own transaction; yield the connection.

        Per design doc §3 "Mechanics" — each phase's atomicity is
        independent. ONE connection per phase, threaded through the
        phase function and its downstream materialize calls. All
        writes within the phase commit atomically; failure rolls
        back the whole phase.

        This replaces the prior per-helper short-lived-connection
        pattern (commit 5ed6c84). The Object phase live test went
        from 1038s → <60s after this change because the
        connection-ceremony overhead dominated for high-row-count
        phases.
        """
        logger.info(
            "sync_run=%s phase=%r begin", sync_run_id, phase_name,
        )
        try:
            with self._connect() as conn:
                yield conn
            logger.info(
                "sync_run=%s phase=%r commit", sync_run_id, phase_name,
            )
        except Exception:
            logger.warning(
                "sync_run=%s phase=%r rollback", sync_run_id, phase_name,
            )
            raise

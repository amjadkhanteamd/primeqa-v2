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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy import text

from primeqa.sync.context import SyncContext
from primeqa.sync.exceptions import (
    EntityOrderViolation,
    PhaseExecutionError,
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
    ) -> str:
        """Run a sync_run end-to-end.

        Args:
            connected_org_id: UUID of the connected_orgs row being
                synced.
            resume_sync_run_id: UUID of an existing sync_run row to
                resume. If provided, the engine reads
                last_completed_phase from that row and starts from the
                next phase. If None, a fresh sync_run row is created.

        Returns:
            The sync_run_id (whether newly created or resumed).

        Raises:
            SyncEngineError: fatal failure (DB connectivity, etc.).
                Per-phase failures do NOT raise here — they're
                captured in sync_run.status and error_message.
        """
        # 1. Create or resume sync_run row.
        if resume_sync_run_id is None:
            sync_run_id, logical_version_seq = self._create_sync_run_row(
                connected_org_id,
            )
        else:
            sync_run_id = resume_sync_run_id
            logical_version_seq = self._get_logical_version_seq(sync_run_id)

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
        if resume_sync_run_id is None:
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
        else:
            logger.info(
                "sync_run=%s org=%s running full sync (resume) — "
                "watermark left unchanged", sync_run_id, connected_org_id,
            )

        # 4. Run phases.
        failed_phase: str | None = None
        first_error: PhaseExecutionError | None = None

        for phase_name in ENTITY_ORDER[start_index:]:
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
                conn, sync_run_id,
            )

            # 3. Back-link sync_run.logical_version_seq.
            conn.execute(text("""
                UPDATE sync_runs
                SET logical_version_seq = :v
                WHERE id = :id
            """), {"v": logical_version_seq, "id": sync_run_id})

        return sync_run_id, logical_version_seq

    def _allocate_logical_version(
        self, conn: Any, sync_run_id: str,
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
        """
        result = conn.execute(text("""
            INSERT INTO logical_versions
                (version_name, version_type, description,
                 created_by_sync_run_id)
            VALUES
                (:name, 'sync_run', :description, :sync_run_id)
            RETURNING version_seq
        """), {
            "name": f"sync_run_{sync_run_id}",
            "description": f"Allocated by sync_run {sync_run_id}",
            "sync_run_id": sync_run_id,
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
        """Mark sync_run.status='failure' with error context."""
        with self._connect() as conn:
            conn.execute(text("""
                UPDATE sync_runs
                SET status = 'failure',
                    completed_at = NOW(),
                    error_message = :msg
                WHERE id = :id
            """), {
                "msg": f"phase={failed_phase}: {error.original}",
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
            return _SkipDecision(
                False, "no watermark (first confirmed sync)", server_time)
        if has_changes:
            return _SkipDecision(
                False, "setup changes detected since watermark", server_time)
        # Probe came back clean, but only trust it if the watermark is recent
        # enough that SetupAuditTrail still retains the window since it (SF keeps
        # ~180 days). An older watermark means changes could have aged out of the
        # trail → "no rows" is uncertainty → run the full sync. Measure age
        # against SF's clock (server_time) when available, else local now (a
        # coarse 90-day threshold tolerates clock skew).
        reference_now = server_time or datetime.now(timezone.utc)
        if reference_now - watermark > MAX_SKIP_WATERMARK_AGE:
            return _SkipDecision(
                False,
                f"watermark {watermark.isoformat()} older than "
                f"{MAX_SKIP_WATERMARK_AGE.days}d — outside SetupAuditTrail "
                f"retention, running full sync",
                server_time)
        return _SkipDecision(
            True, f"no setup changes since {watermark.isoformat()}",
            server_time)

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

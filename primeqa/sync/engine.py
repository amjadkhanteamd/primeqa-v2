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
    successful structural completion

Does NOT run AI enrichment — that's a separate process per design
doc §6. See primeqa/sync/enrichment.py (future) for that worker.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
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
            sync_run_id = self._create_sync_run_row(connected_org_id)
        else:
            sync_run_id = resume_sync_run_id

        # 2. Build context.
        ctx = self._build_context(sync_run_id, connected_org_id)

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

        # 4. Run phases.
        failed_phase: str | None = None
        first_error: PhaseExecutionError | None = None

        for phase_name in ENTITY_ORDER[start_index:]:
            try:
                with self._phase_transaction(sync_run_id, phase_name):
                    phase_fn = get_phase_function(phase_name)
                    result = phase_fn(ctx)
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
                        sync_run_id, phase_name, result,
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

    def _create_sync_run_row(self, connected_org_id: str) -> str:
        """INSERT a new sync_run row in 'running' / 'structural' state.

        Returns the new sync_run id (UUID, stringified).
        """
        with self._connect() as conn:
            row = conn.execute(text("""
                INSERT INTO sync_runs (source_org_id, status, phase)
                VALUES (:org_id, 'running', 'structural')
                RETURNING id
            """), {"org_id": connected_org_id}).fetchone()
        if row is None:
            raise SyncEngineError(
                f"INSERT into sync_runs returned no row for "
                f"connected_org_id={connected_org_id!r}"
            )
        return str(row[0])

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
        sync_run_id: str,
        phase_name: str,
        result: PhaseResult,
    ) -> None:
        """UPDATE last_completed_phase + accumulate counter columns.

        Called after a phase function has returned successfully and
        the phase transaction is about to commit. Same transaction as
        the phase write (caller's responsibility — this is called
        inside _phase_transaction's `with` block).
        """
        with self._connect() as conn:
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
    ) -> None:
        """All structural phases completed — advance phase to
        'enrichment' (the enrichment worker will pick up from here)
        and update connected_orgs.ai_enrichment_status.

        Note: sync_run.status remains 'running' here. The terminal
        status ('success' / 'partial_success' / 'failure') is set by
        the enrichment worker on its own completion, per design
        doc §6 + §2 state machine.

        For the current skeleton (no enrichment worker yet), this
        leaves the sync_run in (phase=enrichment, status=running) —
        which is the intended intermediate state. Tests for the
        full success path verify this intermediate state explicitly.
        """
        with self._connect() as conn:
            conn.execute(text("""
                UPDATE sync_runs
                SET phase = 'enrichment'
                WHERE id = :id
            """), {"id": sync_run_id})
            conn.execute(text("""
                UPDATE connected_orgs
                SET ai_enrichment_status = 'structural_only',
                    last_sync_run_id = :run_id
                WHERE id = :id
            """), {"run_id": sync_run_id, "id": connected_org_id})

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
    # Internal: context + transactions
    # ------------------------------------------------------------------

    def _build_context(
        self,
        sync_run_id: str,
        connected_org_id: str,
    ) -> SyncContext:
        """Construct the SyncContext passed to phase functions."""
        return SyncContext(
            sf_client=self.sf,
            engine=self.db,
            sync_run_id=sync_run_id,
            connected_org_id=connected_org_id,
            tenant_schema=self.tenant_schema,
        )

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        """Open a connection with tenant search_path set.

        Sets search_path to the engine's tenant_schema for the
        duration of the connection so unqualified table references
        resolve there.
        """
        with self.db.connect() as conn:
            with conn.begin():
                conn.execute(
                    text(f'SET LOCAL search_path TO "{self.tenant_schema}", public')
                )
                yield conn

    @contextmanager
    def _phase_transaction(
        self,
        sync_run_id: str,
        phase_name: str,
    ) -> Iterator[Any]:
        """Wrap a phase in its own transaction.

        Begins a transaction on the tenant schema, yields control to
        the phase, and commits on clean exit OR rollbacks on
        exception. Per design doc §3 "Mechanics" — each phase's
        atomicity is independent.

        Note: this is a no-op wrapper in the current skeleton; phase
        functions don't yet use the connection. Real phase
        implementations in subsequent cycles will receive the
        connection via SyncContext or via this manager's yield value.
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

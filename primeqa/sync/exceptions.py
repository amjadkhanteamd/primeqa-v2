"""Sync engine exception hierarchy.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §3 (failure modes).
"""
from __future__ import annotations


class SyncEngineError(Exception):
    """Base for sync engine errors."""


class EntityOrderViolation(SyncEngineError):
    """Hardcoded ENTITY_ORDER violates the actual FK dependency graph.

    Raised at sync_run startup before any phase runs. Catches schema
    drift loudly rather than producing subtle FK-violation errors
    mid-sync. Per PHASE_2_STEP_4_SYNC_DESIGN.md §4 "FK-topological-
    sort assertion".
    """


class PhaseExecutionError(SyncEngineError):
    """A phase function raised an exception.

    Wraps the original exception with context (phase name,
    sync_run_id). Triggers per-phase rollback per §3.
    """

    def __init__(
        self,
        phase_name: str,
        sync_run_id: str,
        original_exception: Exception,
    ) -> None:
        self.phase_name = phase_name
        self.sync_run_id = sync_run_id
        self.original = original_exception
        super().__init__(
            f"Phase '{phase_name}' (sync_run={sync_run_id}) "
            f"failed: {original_exception}"
        )

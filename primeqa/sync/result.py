"""PhaseResult dataclass — outcome of a single entity-type phase.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §3.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PhaseResult:
    """Outcome of a single entity-type phase within a sync_run.

    Counts increment-only fields on the sync_run row when the
    phase commits. error_message is populated only on failure
    and triggers phase rollback.
    """

    entity_type: str
    entities_inserted: int = 0
    entities_superseded: int = 0
    entities_unchanged: int = 0
    edges_inserted: int = 0
    edges_superseded: int = 0
    embeddings_queued: int = 0
    summaries_queued: int = 0
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """True iff this phase completed without recording an error."""
        return self.error_message is None

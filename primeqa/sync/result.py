"""PhaseResult dataclass — outcome of a single entity-type phase.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §3.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PhaseResult:
    """Outcome of a single entity-type phase within a sync_run.

    Counts increment-only fields on the sync_run row when the
    phase commits. error_message is populated only on failure
    and triggers phase rollback.

    edges_skipped_by_type tracks per-edge-type counts for edges
    that the materialize layer silently skipped (parent_resolver
    returned None for the target — typically because the target
    isn't in the syncable scope, e.g., Profile.objectPermissions
    granting access to managed-package internal Objects that
    Object phase correctly excluded). Skip behavior is intentional
    design across all phases; this counter surfaces the magnitude
    for observability per corrections-log §19.
    """

    entity_type: str
    entities_inserted: int = 0
    entities_superseded: int = 0
    entities_unchanged: int = 0
    edges_inserted: int = 0
    edges_superseded: int = 0
    embeddings_queued: int = 0
    summaries_queued: int = 0
    edges_skipped_by_type: dict[str, int] = field(default_factory=dict)
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """True iff this phase completed without recording an error."""
        return self.error_message is None

    def record_skipped_edge(self, edge_type: str) -> None:
        """Increment the per-edge-type skipped-edge counter.

        Called by materialize_edges_for_entities when
        parent_resolver returns None for a target external_id —
        the edge isn't written (preventing dangling rows) but
        the skip is observable via this counter. Skipping is the
        documented behavior for legitimate scope misses; the
        counter exists for diagnostics, not for error handling.
        """
        self.edges_skipped_by_type[edge_type] = (
            self.edges_skipped_by_type.get(edge_type, 0) + 1
        )

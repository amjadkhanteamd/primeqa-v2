"""Phase function registry — one function per entity type.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §§3-4.

This skeleton ships no-op placeholders for all 12 entity types.
Real per-entity-type fetch + normalize + write logic lands in
subsequent implementation cycles.

A phase function signature:

    def phase_foo(ctx: SyncContext) -> PhaseResult: ...

The engine calls each phase function inside its own transaction
(per §3 staged transactional boundaries). The function:
- Reads any prior-phase state it needs via ctx.engine
- Fetches Salesforce data via ctx.sf_client
- Normalizes and writes the entity rows (and detail-table rows)
- Returns a PhaseResult with counts; raises an exception OR
  sets PhaseResult.error_message on failure
"""
from __future__ import annotations

from typing import Callable

from primeqa.sync.context import SyncContext
from primeqa.sync.fk_assertion import ENTITY_ORDER
from primeqa.sync.result import PhaseResult


PhaseFunction = Callable[[SyncContext], PhaseResult]


def _noop_phase(entity_type: str) -> PhaseFunction:
    """Return a no-op phase function for the given entity_type.

    The returned function does no Salesforce fetching, no DB writes,
    and returns an empty PhaseResult. Used during skeleton bring-up;
    real implementations replace these one cycle at a time.
    """

    def phase(ctx: SyncContext) -> PhaseResult:
        return PhaseResult(entity_type=entity_type)

    phase.__name__ = f"phase_{entity_type.lower()}"
    phase.__doc__ = f"No-op placeholder phase for entity_type={entity_type!r}."
    return phase


# One phase function per ENTITY_ORDER value. Kept aligned by
# construction below — see test_phase_registry_has_function_for_every_
# entity_order_value for the lock.
PHASE_REGISTRY: dict[str, PhaseFunction] = {
    entity_type: _noop_phase(entity_type) for entity_type in ENTITY_ORDER
}


def get_phase_function(entity_type: str) -> PhaseFunction:
    """Look up the phase function for entity_type.

    Raises:
        KeyError: if entity_type is not registered.
    """
    if entity_type not in PHASE_REGISTRY:
        raise KeyError(f"No phase registered for entity_type={entity_type!r}")
    return PHASE_REGISTRY[entity_type]

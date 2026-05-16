"""Sync engine for materializing Salesforce data into the
semantic model.

Per docs/architecture/substrate_1_semantic_org_model/
PHASE_2_STEP_4_SYNC_DESIGN.md.

This package is the structural-sync side of Phase 2 step 4.
AI enrichment runs in a separate process; see PHASE_2_STEP_4_SYNC_DESIGN.md
§6 for the split.
"""

from primeqa.sync.engine import SyncEngine
from primeqa.sync.context import SyncContext
from primeqa.sync.result import PhaseResult
from primeqa.sync.exceptions import (
    SyncEngineError,
    EntityOrderViolation,
    PhaseExecutionError,
)

__all__ = [
    "SyncEngine",
    "SyncContext",
    "PhaseResult",
    "SyncEngineError",
    "EntityOrderViolation",
    "PhaseExecutionError",
]

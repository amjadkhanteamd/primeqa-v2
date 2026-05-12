"""SyncContext — per-sync-run state passed to every phase function.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SyncContext:
    """Per-sync-run state passed to every phase function.

    Phases receive this context to access the SF client, the
    database engine (for opening per-phase transactions), the
    sync_run row, and the connected_org row they're syncing.

    Phases should not mutate context fields directly; database
    state is mutated via the engine's per-phase transactions
    (the engine yields a connection to each phase via the
    _phase_transaction context manager).
    """

    sf_client: Any              # primeqa.integrations.sf_client.SalesforceClient
    engine: Any                 # SQLAlchemy Engine (tenant-scoped via search_path)
    sync_run_id: str            # UUID of the active sync_run row
    connected_org_id: str       # UUID of the org being synced
    tenant_schema: str          # e.g., 'tenant_1'
    logical_version_seq: int    # logical_versions.version_seq allocated for this run
                                # (used as valid_from_seq on entity INSERTs and
                                # as the supersession boundary on SCD Type 2 updates)

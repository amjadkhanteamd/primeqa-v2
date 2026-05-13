"""SyncContext — per-sync-run state passed to every phase function.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §3.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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

    svs_metadata_cache is the one exception to the "no mutation"
    rule: it's an explicit cross-phase inter-PHASE handoff. The
    PicklistValueSet phase fetches 616 SVS Metadata records
    (~6 min wall-clock at ~0.6s/call) and writes them into this
    cache; the PicklistValue phase reads from the cache instead
    of re-fetching the same data. Keyed by SVS FullName (e.g.,
    'AccountSource'). See corrections-log §9 for the deferral-
    then-resolution story.

    Cache is per-SyncContext (i.e., per-sync-run): a fresh
    context is constructed for each run, so the cache lifetime
    is bounded by the run's lifetime — no cross-run leakage.

    No GVS cache: GVS fetch is a single bulk Tooling call
    (~0.6s total regardless of N) so refetching in PV phase is
    cheap. SVS is the heavy path; that's where the optimization
    matters.
    """

    sf_client: Any              # primeqa.integrations.sf_client.SalesforceClient
    engine: Any                 # SQLAlchemy Engine (tenant-scoped via search_path)
    sync_run_id: str            # UUID of the active sync_run row
    connected_org_id: str       # UUID of the org being synced
    tenant_schema: str          # e.g., 'tenant_1'
    logical_version_seq: int    # logical_versions.version_seq allocated for this run
                                # (used as valid_from_seq on entity INSERTs and
                                # as the supersession boundary on SCD Type 2 updates)
    svs_metadata_cache: dict[str, dict] = field(default_factory=dict)
    # Cache of fetched SVS Metadata, keyed by SVS FullName (e.g.,
    # 'AccountSource' → the full Metadata.standardValue dict).
    # Populated by phase_picklist_value_set; consumed by
    # phase_picklist_value to skip refetching. Empty when PV phase
    # runs in isolation (e.g., a test or resumed sync); PV phase
    # falls back to direct fetch in that case. See corrections-log
    # §9.

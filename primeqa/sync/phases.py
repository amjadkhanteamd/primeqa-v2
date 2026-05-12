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

from typing import Any, Callable

from primeqa.sync.context import SyncContext
from primeqa.sync.fk_assertion import ENTITY_ORDER
from primeqa.sync.materialize import batched_materialize
from primeqa.sync.result import PhaseResult


# A phase function receives (ctx, conn) where conn is the SQLAlchemy
# Connection opened by engine._phase_transaction. All DB writes go
# through this conn so the entire phase is atomic. Per the
# connection-threading refactor (post 5ed6c84).
PhaseFunction = Callable[[SyncContext, Any], PhaseResult]


def _noop_phase(entity_type: str) -> PhaseFunction:
    """Return a no-op phase function for the given entity_type.

    The returned function does no Salesforce fetching, no DB writes,
    and returns an empty PhaseResult. Used during skeleton bring-up;
    real implementations replace these one cycle at a time. Accepts
    the conn parameter per the PhaseFunction contract but ignores
    it (no writes to perform).
    """

    def phase(ctx: SyncContext, conn: Any) -> PhaseResult:
        return PhaseResult(entity_type=entity_type)

    phase.__name__ = f"phase_{entity_type.lower()}"
    phase.__doc__ = f"No-op placeholder phase for entity_type={entity_type!r}."
    return phase


def _is_syncable_object(raw: dict) -> bool:
    """Filter Salesforce sObjects to those representing user-facing
    data objects.

    Excludes:
    - Non-queryable / non-searchable objects (meta types like
      AggregateResult, OutgoingEmail, RecentlyViewed, etc. — these
      cannot be SELECTed from)
    - Deprecated objects (no longer used; would clutter the model)
    - Custom Setting objects (configuration storage, not user data —
      distinct entity-modeling concern; out of scope for Object phase)

    Includes:
    - Standard objects (Account, Contact, Lead, Opportunity, Case, ...)
    - Custom objects (suffix __c)
    - Managed-package objects (NamespacePrefix__Object__c)
    - Platform objects representing users/permissions
      (User, Profile, PermissionSet) — these are sObjects at the
      Salesforce level even though our model also has separate User /
      Profile / PermissionSet entities. Their semantics in the model:
      Object entity captures the SCHEMA (table definition); User /
      Profile / PermissionSet entities capture the ROW-LEVEL data.
    """
    return (
        bool(raw.get("queryable"))
        and bool(raw.get("searchable"))
        and not bool(raw.get("deprecatedAndHidden"))
        and not bool(raw.get("customSetting"))
    )


def phase_object(ctx: SyncContext, conn: Any) -> PhaseResult:
    """Object phase — fetches all syncable sObjects and materializes
    them as Object entities.

    Per PHASE_2_STEP_4_SYNC_DESIGN.md §§4-5, 7. First phase in
    ENTITY_ORDER; no upstream dependencies.

    Filter: queryable AND searchable AND NOT deprecatedAndHidden
    AND NOT customSetting (see _is_syncable_object).

    Uses Salesforce API name (raw['name']) as external_id.
    Substrate-1's fetch_objects() returns ~700+ sObjects on a
    typical org; ~50-200 typically pass the filter.

    Calls batched_materialize once with the full syncable list.
    Internally chunked at 500 rows per pass per design doc §7.
    For typical sandbox object counts (<500 syncable), this is a
    single chunk = a single batched-INSERT statement (plus the
    SELECT, the queue UPSERT, etc.).

    All DB writes execute on `conn` (engine._phase_transaction's
    transaction); commit happens when this function returns and
    the engine exits the _phase_transaction context.
    """
    result = PhaseResult(entity_type="Object")
    raw_objects = ctx.sf_client.fetch_objects()
    syncable = [raw for raw in raw_objects if _is_syncable_object(raw)]
    if syncable:
        batched_materialize(
            ctx=ctx,
            conn=conn,
            entity_type="Object",
            raw_payloads=syncable,
            result=result,
        )
    return result


def phase_picklist_value_set(
    ctx: SyncContext, conn: Any,
) -> PhaseResult:
    """PicklistValueSet phase — GlobalValueSet source.

    Per corrections-log §8: PicklistValueSet entity_type unifies
    GVS + SVS. This phase handles the GVS source via
    fetch_global_value_sets; the SVS source is implemented in
    its own subsequent cycle and also materializes under
    entity_type='PicklistValueSet'.

    Runs after Object per ENTITY_ORDER. Field phase will reference
    PicklistValueSet entities for fields whose picklist values
    derive from a value set.

    No filter applied — every GlobalValueSet is user-defined and
    worth syncing. Unlike Object, there are no platform meta-objects
    to exclude.

    Sandbox at the time of writing has 0 GlobalValueSets (per
    sf_client.fetch_global_value_sets docstring); the empty path
    is the live-test coverage. Populated path is exercised by
    unit-test mocks against the documented Salesforce schema.
    """
    result = PhaseResult(entity_type="PicklistValueSet")
    raw_value_sets = ctx.sf_client.fetch_global_value_sets()
    if raw_value_sets:
        batched_materialize(
            ctx=ctx,
            conn=conn,
            entity_type="PicklistValueSet",
            raw_payloads=raw_value_sets,
            result=result,
        )
    return result


# One phase function per ENTITY_ORDER value. Kept aligned by
# construction below — see test_phase_registry_has_function_for_every_
# entity_order_value for the lock. Real phase implementations replace
# their _noop_phase entries one cycle at a time per design doc §9.
PHASE_REGISTRY: dict[str, PhaseFunction] = {
    entity_type: _noop_phase(entity_type) for entity_type in ENTITY_ORDER
}
PHASE_REGISTRY["Object"] = phase_object
PHASE_REGISTRY["PicklistValueSet"] = phase_picklist_value_set


def get_phase_function(entity_type: str) -> PhaseFunction:
    """Look up the phase function for entity_type.

    Raises:
        KeyError: if entity_type is not registered.
    """
    if entity_type not in PHASE_REGISTRY:
        raise KeyError(f"No phase registered for entity_type={entity_type!r}")
    return PHASE_REGISTRY[entity_type]

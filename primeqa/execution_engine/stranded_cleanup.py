"""Durable data-path cleanup (D-230, 2.4) — the write-ahead sink + the
crash-recovery reaper.

A live data run creates real Salesforce records (the target + provisioned
parents, F6.2) and tears them down reverse-order in-run (F6.1). If the run dies
mid-flight — a worker kill, a transport hang, an unhandled raise after
``client.create`` — a record can be left in the org. F6.1 wrote the audit rows at
FINALIZE (after teardown), so a crash before finalize left NO row and the record
was unreapable.

This module closes that gap with two pieces:

  - :class:`StrandedRecordSink` — write-ahead: the executor records each record
    **S4 itself creates** on the sink IMMEDIATELY (its own committed transaction,
    before the next SF call), ``cleaned=false`` + the ``environment_id`` the
    reaper needs; and flips ``cleaned=true`` when the in-run teardown deletes that
    record. Best-effort — a sink write failure is logged, never raised
    (durability is opportunistic; it must never break the run).

    **Residual (review #1):** records the org's own automation (a Flow/trigger)
    creates as a side effect are recorded only AFTER the read-back observes their
    ids (the executor cannot write-ahead an id it has not yet read), so a crash
    during the read-retry window strands an automation child unreapably. The S4
    trigger record stays reapable. Closing this needs reaper read-back
    reconciliation (reconstruct the side-effect SOQL from the durable trigger
    id) — a future increment; the narrow window is accepted for now.

  - :func:`reap_stranded_records` — finds rows ``cleaned=false`` older than a
    grace window (the run had time to finish), resolves the env's data-mutation
    client, DELETEs each from Salesforce (best-effort; an already-gone record's
    404 is success), and flips ``cleaned=true``. Per-tenant; scheduler-driven
    beside the s4 JOB reaper.

Both open their own ``get_tenant_connection`` per operation (each its own
committed transaction — the ``record_event`` / ``ExecutionJobStore`` idiom) so a
write commits before the run can crash, and the reaper never holds a connection
across Salesforce I/O.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

# The reaper only targets records whose run had time to finish — a run still in
# flight legitimately holds uncleaned rows. 15 min comfortably exceeds the s4 job
# heartbeat-timeout (10 min) so a row is reaped only after its run is truly dead.
_DEFAULT_STALE_MINUTES = 15
# Bound the per-tick batch so one tenant's backlog can't monopolize a tick.
_DEFAULT_BATCH = 200


class StrandedRecordSink:
    """Write-ahead audit sink for a run's created records (D-230). Bound to the
    tenant + environment (the run_id is supplied per call, so it always matches
    the executor's evidence run_id without hoisting run_id generation up the
    call stack). Each method is its own committed transaction so a create is
    durable before the next Salesforce call. Never raises — a failed write is
    logged; the run proceeds (a missed write only means a record might not be
    reapable, never a broken run)."""

    def __init__(self, tenant_id: int, environment_id: int):
        self._tenant_id = tenant_id
        self._environment_id = environment_id

    def created(self, run_id, sobject: str, record_id: str, created_seq: int) -> None:
        """Persist one created record (``cleaned=false``) in its own transaction,
        BEFORE the executor issues its next Salesforce call."""
        try:
            from primeqa.semantic.connection import get_tenant_connection
            with get_tenant_connection(self._tenant_id) as conn:
                conn.execute(text(
                    "INSERT INTO s4_created_records "
                    "(run_id, sobject, record_id, created_seq, cleaned, environment_id) "
                    "VALUES (CAST(:r AS uuid), :so, :rid, :seq, false, :env)"
                ), {"r": str(run_id), "so": sobject, "rid": record_id,
                    "seq": created_seq, "env": self._environment_id})
        except Exception as exc:  # best-effort — durability never breaks the run
            log.warning("StrandedRecordSink.created failed (tenant %s run %s rec %s): %s",
                        self._tenant_id, run_id, record_id, exc)

    def cleaned(self, run_id, record_id: str) -> None:
        """Mark a record the in-run teardown deleted as ``cleaned`` so the reaper
        skips it. Own transaction; best-effort."""
        try:
            from primeqa.semantic.connection import get_tenant_connection
            with get_tenant_connection(self._tenant_id) as conn:
                conn.execute(text(
                    "UPDATE s4_created_records SET cleaned = true "
                    "WHERE run_id = CAST(:r AS uuid) AND record_id = :rid"
                ), {"r": str(run_id), "rid": record_id})
        except Exception as exc:
            log.warning("StrandedRecordSink.cleaned failed (tenant %s run %s rec %s): %s",
                        self._tenant_id, run_id, record_id, exc)


def reap_stranded_records(
    tenant_id: int, *, stale_minutes: int = _DEFAULT_STALE_MINUTES,
    batch: int = _DEFAULT_BATCH, client_resolver=None,
) -> int:
    """Delete records a crashed run left stranded in Salesforce, one tenant.

    Selects ``cleaned=false`` rows with a non-NULL ``environment_id`` that are
    (a) older than ``stale_minutes``, (b) younger than the 7-day give-up window,
    and (c) whose environment has NO ACTIVE ``s4_execution_job`` (the run-liveness
    interlock — never reap out from under a live run; see step 1). Groups by
    environment, resolves a data-mutation client per env (``client_resolver(
    session, environment_id)``; defaults to the production resolver), DELETEs each
    record (best-effort — an already-gone record is success), and flips
    ``cleaned=true``. Returns the count of rows reaped (delete attempted). Never
    raises across a single record — one bad record never strands the rest.

    The DB read/marks use brief own-transaction connections; the live deletes
    hold NO DB connection (the D-129 invariant)."""
    from primeqa.semantic.connection import get_tenant_connection
    from sqlalchemy.orm import Session

    if client_resolver is None:
        from primeqa.execution_engine.run import resolve_data_mutation_client
        client_resolver = resolve_data_mutation_client

    # 1. Read the stranded rows (brief read-only TX), detach as plain tuples.
    #
    #    RUN-LIVENESS INTERLOCK (review #2): a record is reapable ONLY when no
    #    ACTIVE s4_execution_job exists for its environment — never reap out from
    #    under a live run. The s4 JOB reaper flips a crashed run's job to a
    #    terminal status at the heartbeat timeout (the synchronous worker does NOT
    #    stop on its own — the 'stale_minutes > heartbeat' reasoning alone is
    #    insufficient), so the cleanup reaper effectively waits for the run to be
    #    declared dead before reclaiming its records. The ``stale_minutes`` lower
    #    bound still guards the no-job (synchronous console) path. The UPPER bound
    #    (give-up window, review #3) drops chronically-unreapable rows out of the
    #    working set so a poison row (dead env) cannot be retried forever nor
    #    block newer rows behind it (review #4).
    give_up_days = 7
    with get_tenant_connection(tenant_id) as conn:
        rows = conn.execute(text(
            "SELECT cr.id, cr.sobject, cr.record_id, cr.environment_id "
            "FROM s4_created_records cr "
            "WHERE cr.cleaned = false AND cr.environment_id IS NOT NULL "
            f"AND cr.created_at < NOW() - INTERVAL '{int(stale_minutes)} minutes' "
            f"AND cr.created_at > NOW() - INTERVAL '{int(give_up_days)} days' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM s4_execution_jobs j "
            "  WHERE j.environment_id = cr.environment_id "
            "  AND j.status IN ('queued', 'claimed', 'running')) "
            "ORDER BY cr.created_at LIMIT :lim"
        ), {"lim": int(batch)}).mappings().all()
        stranded = [dict(r) for r in rows]
    if len(stranded) >= batch:
        # The batch is full — a backlog exists; surface it so truncation is
        # observable (review #4) rather than a silent partial sweep.
        log.warning("reap: tenant %s stranded-record batch is FULL (%d) — a "
                    "backlog exists; more will be swept next tick", tenant_id, batch)
    if not stranded:
        return 0

    # 2. Resolve one client per environment (brief TX each — a resolver needs a
    #    connection to read the env's connection/credentials).
    clients: dict[int, object] = {}
    for env_id in {r["environment_id"] for r in stranded}:
        try:
            with get_tenant_connection(tenant_id) as conn:
                # D-245 Phase 4 — REAPER GUARD (independent of the dispatch gate):
                # the reaper DELETEs, which is a mutation. Never reap an env the
                # run policy protects: skip read_only / disabled, and gate (skip +
                # log) a production env. Only a ``full``, non-production env is
                # reaped; everything else is left for a human / a policy change.
                pol = conn.execute(text(
                    "SELECT execution_policy, is_production FROM environments "
                    "WHERE id = :id"), {"id": env_id}).mappings().first()
                if (pol is None or pol["execution_policy"] != "full"
                        or pol["is_production"]):
                    log.warning(
                        "reap: tenant %s env %s SKIPPED — run policy protects it "
                        "(execution_policy=%s, is_production=%s); a delete is a "
                        "mutation and is not reaped here",
                        tenant_id, env_id,
                        pol["execution_policy"] if pol else None,
                        pol["is_production"] if pol else None)
                    clients[env_id] = None
                    continue
                clients[env_id] = client_resolver(Session(bind=conn), env_id)
        except Exception as exc:
            log.warning("reap: tenant %s env %s client resolve failed: %s",
                        tenant_id, env_id, exc)
            clients[env_id] = None

    # 3. Delete each stranded record (NO DB connection held across SF I/O), then
    #    mark it cleaned in its own brief TX.
    reaped = 0
    for r in stranded:
        client = clients.get(r["environment_id"])
        if client is None:
            continue
        try:
            client.delete(r["sobject"], r["record_id"])   # 404 (already gone) = ok
        except Exception as exc:
            # Best-effort: a transient delete failure leaves cleaned=false so the
            # NEXT tick retries — do not mark cleaned, do not raise.
            log.info("reap: tenant %s record %s/%s delete deferred: %s",
                     tenant_id, r["sobject"], r["record_id"], exc)
            continue
        try:
            with get_tenant_connection(tenant_id) as conn:
                conn.execute(text(
                    "UPDATE s4_created_records SET cleaned = true WHERE id = :id"),
                    {"id": r["id"]})
            reaped += 1
        except Exception as exc:
            log.warning("reap: tenant %s row %s mark-cleaned failed: %s",
                        tenant_id, r["id"], exc)
    if reaped:
        log.info("reap: tenant %s reclaimed %d stranded record(s)", tenant_id, reaped)
    return reaped


__all__ = ["StrandedRecordSink", "reap_stranded_records"]

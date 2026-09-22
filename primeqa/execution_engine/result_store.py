"""The S4 result store — persists run evidence (SPEC §4, D-108.2 slice 3).

One kind-agnostic, per-tenant table ``s4_execution_runs``: typed identity /
outcome columns (queryable) + an ``evidence`` JSONB carrying the per-step
captured trace (raw observation, the extensible part — F2). The DB DDL is in
``alembic/versions/tenant/20260527_0010_s4_execution_runs.py``; this module is
the Python projection + the persister.

**Persistence boundary.** The executor stays produce-only (returns an in-memory
:class:`RunEvidence`, no DB import). :func:`persist_run_evidence` is the only
writer — it maps the in-memory evidence to a row and persists it on a
caller-provided session (the substrate convention; slice 4 will share that
session with ``report_run_outcome``). Slice 2's no-DB unit tests stay untouched.
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Integer, String, Text, func, text)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

from primeqa.db import Base
from primeqa.execution_engine.evidence import ErrorSurface, RunEvidence, failure_signature
from primeqa.integrations.failure_taxonomy import category_for

#: The write-ahead outcome of a run that has been minted but not finalized
#: (round 4, AUD-028): the row exists BEFORE the first record is provisioned,
#: so a ``s4_created_records`` row can never name a run that has no row.
RUNNING = "running"
#: The predicate EVERY reader of ``s4_execution_runs`` carries — a running row
#: (finished_at NULL) is invisible to the product until finalize, which is the
#: visibility a run had before the write-ahead row existed. The gate
#: ``tests/unit/test_running_rows_invisible.py`` holds every reader to it.
FINALIZED = "finished_at IS NOT NULL"
#: The job-queue vocabulary for a run cut off by the worker's shutdown (D-341);
#: the interrupted run's error surface carries the same word.
WORKER_SHUTDOWN = "worker_shutdown"

# Reuse the run_outcome PG enum (created by the substrate-2 migration). Same
# pattern as substrate-2's models_db: mirror the type, create_type=False.
RUN_OUTCOME_ENUM = ENUM(
    'passed', 'failed', 'errored', 'skipped', RUNNING,
    name='run_outcome', create_type=False,
)


class S4ExecutionRun(Base):
    """One execution run's captured truth + grounded outcome.

    Per-tenant (no ``tenant_id`` — isolation by schema). Identity + outcome are
    typed columns; the per-step trace lives in ``evidence`` JSONB. Logical FKs
    to S2 rows (``recipe_id`` / ``claim_test_id``) are not DB-enforced — they
    are not unique in their home tables (compound PK with version_seq)."""

    __tablename__ = "s4_execution_runs"
    __test__ = False  # pytest collection: not a test class

    run_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    recipe_id = Column(UUID(as_uuid=True), nullable=False)
    recipe_version_seq = Column(Integer, nullable=False)
    claim_test_id = Column(UUID(as_uuid=True), nullable=False)
    claim_version_seq = Column(Integer, nullable=True)
    environment_id = Column(Integer, nullable=False)
    outcome = Column(RUN_OUTCOME_ENUM, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    # NULL exactly while ``outcome = 'running'`` (the CHECK in migration
    # 20260922_0010); every reader excludes such rows (``FINALIZED``).
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    evidence = Column(JSONB, nullable=False)
    # Phase 2 Slice A: the run's typed failure class (auth/permission/transient/
    # rate_limit/normalization/unknown) promoted from the top-level error surface,
    # + the SF errorCode of the erroring step — queryable beside the JSONB trace.
    # NULL for runs with no transport error (passed / assertion-failed / business
    # rejection). environment_id (above) maps to the org, so this is per-org for
    # free.
    failure_category = Column(String, nullable=True)
    sf_error_code = Column(String, nullable=True)
    # D-275 (S6 run-all, Slice 3.2): batch correlation. ``batch_id`` is minted at
    # run time by the run-all loop, stamping each probe-run of one invocation so
    # strict-AND grades ONE batch; NULL on every single-recipe run (dormant until
    # the run-all loop, Slice 3.3). ``source`` is the trigger discriminator (open
    # text — runall_probe today; shared-design with the deferred suite-run
    # grouping, DEFERRED_ITEMS §3). persist_run_evidence OMITS both from the INSERT
    # when not passed (the columns are never referenced for a single run — deploy
    # order-independent). Completeness semantics (expected probe membership) land
    # in Slice 4.
    batch_id = Column(UUID(as_uuid=True), nullable=True)
    source = Column(String, nullable=True)
    # Step 4: the recorded plan this run executed (NULL = not plan-driven).
    plan_id = Column(UUID(as_uuid=True), nullable=True)
    # D-419/D-421: the Salesforce username the run executed as (JWT sub).
    # NULL = not identity-scoped (NOT "ran as admin" — absence stays absence).
    executing_identity = Column(String, nullable=True)
    # Step 2: the run stamp (LLD_STEP_2 §a). NULL = unstamped (legacy / resolver
    # refused) — never backfilled. Omitted from the INSERT when absent, the
    # executing_identity discipline, so a stampless run never references them.
    connected_org_id = Column(UUID(as_uuid=True), nullable=True)
    org_version_seq = Column(Integer, nullable=True)


class S4CreatedRecord(Base):
    """One record S4 created during a run — the provisioning cleanup audit
    (F6.1, D-196). Per-tenant (no ``tenant_id`` — isolation by schema). One row
    per created record (target + provisioned parents); the executor tears them
    down reverse-order in-run, so ``cleaned`` is the future-reaper flag, not the
    in-run teardown result. DDL in
    ``alembic/.../20260608_0010_s4_created_records.py``."""

    __tablename__ = "s4_created_records"
    __test__ = False  # pytest collection: not a test class

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(UUID(as_uuid=True), nullable=False)
    sobject = Column(Text, nullable=False)
    record_id = Column(Text, nullable=False)
    created_seq = Column(Integer, nullable=False)
    cleaned = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    # D-230 (2.4): the environment the record was created against — written at
    # create time (write-ahead) so the crash-recovery reaper resolves the
    # data-mutation client without an s4_execution_runs join. NULL on pre-D-230
    # finalize-written rows (the reaper skips those).
    environment_id = Column(Integer, nullable=True)


def _run_failure(evidence: RunEvidence):
    """Phase 2 Slice A: derive the run-level ``(failure_category, sf_error_code)``
    from the evidence's top-level error surface.

    Delegates to :func:`primeqa.execution_engine.evidence.failure_signature` — the
    single derivation S6 shares (D-272 Slice 1), so a run reads the SAME category
    at persist and at interpret. Returns ``(None, None)`` for a run with no
    transport error (``passed`` / assertion-``failed`` / a business rejection)."""
    return failure_signature(evidence)


def persist_run_evidence(session, evidence: RunEvidence, *,
                         batch_id: uuid.UUID | None = None,
                         source: str | None = None,
                         plan_id=None):
    """Persist one :class:`RunEvidence` as an ``s4_execution_runs`` row.

    Maps the in-memory evidence to typed columns + an ``evidence`` JSONB trace,
    adds + flushes on the caller's ``session`` (no commit — the caller owns the
    transaction, the substrate convention), and returns the run's ``run_id``.

    D-230: the ``s4_created_records`` audit rows are no longer written here —
    the :class:`~primeqa.execution_engine.stranded_cleanup.StrandedRecordSink`
    writes each WRITE-AHEAD (its own committed transaction, the moment the record
    is created) so a crash before this finalize cannot strand a record without a
    reapable row. A finalize write would have been both too late (no durability)
    and a duplicate (the sink already wrote it).

    D-275 (Slice 3.2): ``batch_id`` / ``source`` are the run-all batch-correlation
    columns. Both default ``None`` and are **omitted from the INSERT when not
    passed** (the columns are referenced ONLY when a run-all caller stamps a batch
    — Slice 3.3), so this writer is byte-identical to pre-D-275 for every current
    single-run caller and is safe pre/post the additive migration. Completeness
    semantics (the most-recent-complete-batch rule) live in Slice 4.
    """
    failure_category, sf_error_code = _run_failure(evidence)
    # Omit-when-None: only stamp the batch columns a run-all caller actually
    # passes, so an unset column never enters the INSERT (deploy order-independent).
    batch_cols = {}
    if batch_id is not None:
        batch_cols["batch_id"] = batch_id
    if source is not None:
        batch_cols["source"] = source
    if plan_id is not None:
        batch_cols["plan_id"] = uuid.UUID(str(plan_id))
    # D-419: the run-as identity, same omit-when-None discipline — a NULL
    # column means "not identity-scoped", never "ran as admin", so a
    # non-identity run must not even reference the column.
    if evidence.executing_identity is not None:
        batch_cols["executing_identity"] = evidence.executing_identity
    # Step 2: the run stamp rides the evidence; written only when the select
    # bracket established it (both halves, or neither).
    if evidence.org_version_seq is not None and evidence.connected_org_id is not None:
        batch_cols["connected_org_id"] = uuid.UUID(str(evidence.connected_org_id))
        batch_cols["org_version_seq"] = int(evidence.org_version_seq)
    fields = dict(
        recipe_id=evidence.recipe_id,
        recipe_version_seq=evidence.recipe_version_seq,
        claim_test_id=evidence.claim_test_id,
        claim_version_seq=evidence.claim_version_seq,
        environment_id=evidence.environment_id,
        outcome=evidence.outcome,
        started_at=evidence.started_at,
        finished_at=evidence.finished_at,
        duration_ms=_duration_ms(evidence.started_at, evidence.finished_at),
        evidence=_evidence_trace(evidence),
        failure_category=failure_category,
        sf_error_code=sf_error_code,
        **batch_cols,
    )
    # Round 4 (AUD-028): a run that was OPENED before provisioning already has
    # its row (outcome 'running'); finalize completes THAT row. A run_id that
    # exists with a final outcome still raises IntegrityError on the INSERT —
    # the D-108.3 fail-loud layer is unchanged.
    existing = session.get(S4ExecutionRun, evidence.run_id) if hasattr(session, "get") else None
    if existing is not None and existing.outcome == RUNNING:
        for k, v in fields.items():
            setattr(existing, k, v)
        session.flush()
        return evidence.run_id
    row = S4ExecutionRun(run_id=evidence.run_id, **fields)
    session.add(row)
    session.flush()
    return evidence.run_id


# ---------------------------------------------------------------------------
# Round 4 (AUD-028): the run row before provisioning — open / interrupt / reap
# ---------------------------------------------------------------------------

def open_running_run(conn, *, run_id, recipe_id, recipe_version_seq, claim_test_id,
                     claim_version_seq, environment_id, started_at) -> None:
    """Write the run's row in the ``running`` state — BEFORE the first record is
    provisioned. FAIL-LOUD: the caller must not provision without it (a raise
    here reaches the executor before any Salesforce call, so nothing is created
    and nothing can be orphaned). The evidence carries the trace shape every
    reader parses, empty; finalize replaces the whole row."""
    conn.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, claim_version_seq, environment_id, outcome, started_at, "
        "finished_at, evidence) VALUES (CAST(:r AS uuid), CAST(:rc AS uuid), :rv, "
        "CAST(:c AS uuid), :cv, :e, CAST(:o AS run_outcome), :s, NULL, "
        "CAST(:ev AS jsonb))"),
        {"r": str(run_id), "rc": str(recipe_id), "rv": int(recipe_version_seq),
         "c": str(claim_test_id), "cv": claim_version_seq, "e": int(environment_id),
         "o": RUNNING, "s": started_at,
         "ev": '{"api_choice": "n/a", "steps": [], "error": null, "run_state": "running"}'})


def interrupted_evidence(opened: dict, exc: BaseException, *, finished_at=None) -> RunEvidence:
    """The errored evidence of an OPENED run that never reached finalize: the
    identity the write-ahead recorded, no steps, and an error surface naming
    the interruption — ``worker_shutdown`` for the worker's SIGTERM
    (KeyboardInterrupt, D-341), else the exception's class name."""
    shutdown = isinstance(exc, KeyboardInterrupt)
    return RunEvidence(
        run_id=opened["run_id"],
        recipe_id=opened["recipe_id"],
        recipe_version_seq=opened["recipe_version_seq"],
        claim_test_id=opened["claim_test_id"],
        claim_version_seq=opened["claim_version_seq"],
        environment_id=opened["environment_id"],
        api_choice="n/a",
        outcome="errored",
        started_at=opened["started_at"],
        finished_at=finished_at or datetime.now(timezone.utc),
        steps=(),
        error=ErrorSurface(
            phase="construct",
            error_type=WORKER_SHUTDOWN if shutdown else type(exc).__name__,
            message=("Worker received shutdown mid-run" if shutdown
                     else (str(exc)[:500] or type(exc).__name__))),
    )


def close_stale_running_runs(conn, *, stale_minutes: int) -> int:
    """The reaper's half: a run row still ``running`` past the heartbeat
    timeout belongs to a worker that died without a handler (SIGKILL, a hard
    crash). Close it as ``errored`` with a ``stale_timeout`` error surface — the
    same word the job reaper writes — so no row stays running forever. Returns
    the number closed."""
    res = conn.execute(text(
        "UPDATE s4_execution_runs SET outcome = CAST('errored' AS run_outcome), "
        "finished_at = now(), "
        "duration_ms = CAST(EXTRACT(EPOCH FROM (now() - started_at)) * 1000 AS integer), "
        "failure_category = :cat, "
        "evidence = jsonb_build_object('api_choice', 'n/a', 'steps', '[]'::jsonb, "
        "  'error', jsonb_build_object('phase', 'construct', 'error_type', 'stale_timeout', "
        "  'message', 'Execution timed out — the worker died mid-run without closing the run.')) "
        f"WHERE outcome::text = :running AND started_at < now() - make_interval(mins => {int(stale_minutes)})"),
        {"running": RUNNING, "cat": category_for("stale_timeout", None)})
    return res.rowcount or 0


def _evidence_trace(evidence: RunEvidence) -> dict:
    """Build the JSONB captured-trace dict from the in-memory evidence.

    Carries the per-step trace + the run's api_choice + top-level error surface
    — everything not promoted to a typed column. Datetimes are ISO strings
    (JSONB-safe); the typed run-level timestamps stay in their columns."""
    trace = {
        "api_choice": evidence.api_choice,
        "steps": [_step_dict(s) for s in evidence.steps],
        "error": (dataclasses.asdict(evidence.error)
                  if evidence.error is not None else None),
    }
    # D-419: identity rides the envelope ONLY when the run was identity-scoped
    # — mirroring the column's absence-stays-absence contract, and keeping
    # every non-run-as trace byte-identical to pre-run-as traces.
    if evidence.executing_identity is not None:
        trace["executing_identity"] = evidence.executing_identity
    # D-449: the run's temporal reference — present only when a symbolic
    # value was materialised this run (same absence-stays-absence contract).
    if getattr(evidence, "temporal_reference", None) is not None:
        trace["temporal_reference"] = evidence.temporal_reference
    return trace


def _step_dict(step) -> dict:
    """One step's trace dict. D-449: the realized-payload keys are stripped
    when None so every non-temporal step trace stays byte-identical to its
    pre-D-449 shape (the D-419 discipline); a temporal step carries BOTH the
    symbolic payload and the realized one."""
    d = _jsonable(dataclasses.asdict(step))
    for key in ("field_values_realized", "field_changes_realized"):
        if key in d and d[key] is None:
            d.pop(key)
    return d


def _jsonable(value):
    """Recursively coerce a dataclass-derived structure to JSONB-safe types:
    datetimes → ISO strings, tuples → lists. (UUIDs don't appear inside step
    evidence; they live in typed columns.)"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _duration_ms(started: datetime, finished: datetime) -> int:
    return int((finished - started).total_seconds() * 1000)

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
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Integer, String, Text, func, text)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

from primeqa.db import Base
from primeqa.execution_engine.evidence import RunEvidence, failure_signature

# Reuse the run_outcome PG enum (created by the substrate-2 migration). Same
# pattern as substrate-2's models_db: mirror the type, create_type=False.
RUN_OUTCOME_ENUM = ENUM(
    'passed', 'failed', 'errored', 'skipped',
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
    finished_at = Column(DateTime(timezone=True), nullable=False)
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


def persist_run_evidence(session, evidence: RunEvidence):
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
    """
    failure_category, sf_error_code = _run_failure(evidence)
    row = S4ExecutionRun(
        run_id=evidence.run_id,
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
    )
    session.add(row)
    session.flush()
    return evidence.run_id


def _evidence_trace(evidence: RunEvidence) -> dict:
    """Build the JSONB captured-trace dict from the in-memory evidence.

    Carries the per-step trace + the run's api_choice + top-level error surface
    — everything not promoted to a typed column. Datetimes are ISO strings
    (JSONB-safe); the typed run-level timestamps stay in their columns."""
    return {
        "api_choice": evidence.api_choice,
        "steps": [_jsonable(dataclasses.asdict(s)) for s in evidence.steps],
        "error": (dataclasses.asdict(evidence.error)
                  if evidence.error is not None else None),
    }


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

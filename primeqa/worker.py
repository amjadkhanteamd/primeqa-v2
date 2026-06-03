"""Background worker entrypoint.

Polls pipeline_runs for running jobs with pending stages, executes them,
sends heartbeats, and checks cancellation tokens between steps.
"""

import logging
import os
import time
import uuid

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

POLL_INTERVAL = 5
HEARTBEAT_INTERVAL = 30


def create_worker_context():
    from primeqa import db as dbmod
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not set")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    dbmod.init_db(database_url)
    db = dbmod.SessionLocal()
    from primeqa.execution.repository import (
        PipelineRunRepository, PipelineStageRepository,
        ExecutionSlotRepository, WorkerHeartbeatRepository,
    )
    from primeqa.execution.service import PipelineService
    return {
        "db": db,
        "run_repo": PipelineRunRepository(db),
        "stage_repo": PipelineStageRepository(db),
        "slot_repo": ExecutionSlotRepository(db),
        "heartbeat_repo": WorkerHeartbeatRepository(db),
        "service": PipelineService(
            PipelineRunRepository(db), PipelineStageRepository(db),
            ExecutionSlotRepository(db), WorkerHeartbeatRepository(db),
        ),
    }


def execute_stage(stage, ctx):
    """Dispatch a pipeline stage to its handler. Unimplemented stages pass
    through so execute-only runs (the common case) don't block on unused
    generate/store/metadata_refresh/jira_read.

    Emits stage_started / stage_finished events so the run detail log
    panel reflects progress. Events are written to both the in-process
    EventBus (live) and the run_events DB table (durable + cross-service).
    """
    from primeqa.runs.streams import emit_stage_started, emit_stage_finished

    stage_repo = ctx["stage_repo"]
    run = ctx["run_repo"].get_run(stage.run_id)
    tenant_id = run.tenant_id if run else None

    t0 = time.time()
    stage_repo.update_stage(stage.id, "running")
    ctx["heartbeat_repo"].update_heartbeat(
        ctx["worker_id"], current_run_id=stage.run_id, current_stage=stage.stage_name,
    )
    emit_stage_started(stage.run_id, stage.stage_name, tenant_id=tenant_id)

    try:
        if stage.stage_name == "execute":
            _run_execute_stage(stage, ctx)
        elif stage.stage_name == "record":
            _run_record_stage(stage, ctx)
        else:
            # metadata_refresh / jira_read / generate / store: still
            # stubs for execute_only runs; wire these when full run-type
            # support lands.
            pass
    except Exception as e:
        log.exception("stage %s on run %s failed: %s", stage.stage_name, stage.run_id, e)
        stage_repo.update_stage(stage.id, "failed", last_error=str(e)[:500])
        emit_stage_finished(
            stage.run_id, stage.stage_name, "failed",
            tenant_id=tenant_id,
            duration_ms=int((time.time() - t0) * 1000),
            error_summary=str(e)[:200],
        )
        return False

    stage_repo.update_stage(stage.id, "passed")
    emit_stage_finished(
        stage.run_id, stage.stage_name, "passed",
        tenant_id=tenant_id,
        duration_ms=int((time.time() - t0) * 1000),
    )
    return True


def _run_execute_stage(stage, ctx):
    """Run every test case on `run.source_ids` against the env's SF org.

    For each test case:
      1. Fetch its current test_case_version.steps
      2. Create a run_test_result row
      3. Invoke StepExecutor per step (SSE events already built in)
      4. Update counts + final status on run_test_result and pipeline_run
    """
    import time
    from primeqa.execution.executor import SalesforceExecutionClient, StepExecutor
    from primeqa.execution.repository import (
        RunTestResultRepository, RunStepResultRepository, RunCreatedEntityRepository,
    )
    from primeqa.execution.idempotency import IdempotencyManager
    from primeqa.core.repository import EnvironmentRepository, ConnectionRepository
    from primeqa.test_management.models import TestCase, TestCaseVersion
    from primeqa.metadata.worker_runner import _oauth_token
    from primeqa.runs.streams import emit_log, emit_test_started, emit_test_finished

    db = ctx["db"]
    run_repo = ctx["run_repo"]
    run = run_repo.get_run(stage.run_id)
    if not run:
        raise RuntimeError(f"run {stage.run_id} not found")
    tenant_id = run.tenant_id

    # Resolve the test_case_ids this run needs to execute.
    tc_ids = _resolve_test_case_ids(db, run)
    emit_log(run.id,
             f"Resolved {len(tc_ids)} test case(s) from source_type={run.source_type}",
             tenant_id=tenant_id, source_type=run.source_type)
    if not tc_ids:
        emit_log(run.id,
                 f"No test cases to execute (source_type={run.source_type})",
                 level="warn", tenant_id=tenant_id)
        run_repo.update_run_status(run.id, "running",
                                   total_tests=0, passed=0, failed=0, skipped=0)
        return

    env_repo = EnvironmentRepository(db)
    env = env_repo.get_environment(run.environment_id, run.tenant_id)
    if not env:
        raise RuntimeError(f"environment {run.environment_id} not found")

    conn_repo = ConnectionRepository(db)
    if not env.connection_id:
        raise RuntimeError("environment has no Salesforce connection")
    conn = conn_repo.get_connection_decrypted(env.connection_id, run.tenant_id)
    if not conn:
        raise RuntimeError("connection not found / could not decrypt")

    emit_log(run.id,
             f"Fetching Salesforce OAuth token for env #{env.id} ({env.name})",
             tenant_id=tenant_id, env_id=env.id)
    access_token = _oauth_token(env, conn["config"])
    # Persist the fresh token so other services can reuse within the window.
    env_repo.store_credentials(
        env.id,
        client_id=conn["config"].get("client_id", ""),
        client_secret=conn["config"].get("client_secret", ""),
        access_token=access_token,
    )

    sf = SalesforceExecutionClient(env.sf_instance_url, env.sf_api_version, access_token)
    step_repo = RunStepResultRepository(db)
    rtr_repo = RunTestResultRepository(db)
    entity_repo = RunCreatedEntityRepository(db)
    # IdempotencyManager takes entity_repo (not db) — it uses the repo to
    # dedupe created entities across steps within a run.
    idem = IdempotencyManager(entity_repo, sf_client=sf)

    # VR-aware capture-mode hint: executor's "smart" uses this to decide
    # when to snapshot state. Always True keeps us cautious on first wiring.
    def _has_vr(_obj_name): return True

    # Metadata-backed Name-createability lookup. Built once here with a
    # per-object cache so every `create` step reuses the same DB probe.
    # Returns True / False / None (unknown). See StepExecutor.__init__
    # docstring for why tri-state matters.
    from primeqa.metadata.repository import MetadataRepository as _MR
    _meta_repo = _MR(db)
    _meta_version_id = env.current_meta_version_id
    _name_cache = {}
    def _name_createable(sobject):
        if sobject in _name_cache:
            return _name_cache[sobject]
        result = None
        try:
            obj = _meta_repo.get_object_by_api_name(_meta_version_id, sobject)
            if obj:
                fields = _meta_repo.get_fields(_meta_version_id, obj.id)
                if not fields:
                    # Object known but ITS FIELDS aren't synced yet. We
                    # can't tell whether Name is createable; return None
                    # so the executor's unknown-branch preserves
                    # whatever the AI produced. (Previously this branch
                    # wrongly returned False and stripped legitimate
                    # AI-supplied Name on Opportunity, breaking creates
                    # with REQUIRED_FIELD_MISSING.)
                    result = None
                else:
                    name_field = next((f for f in fields if f.api_name == "Name"), None)
                    if name_field is not None:
                        result = bool(name_field.is_createable)
                    else:
                        # Fields synced, no Name column at all \u2014 confirmed
                        # not a Name-bearing object.
                        result = False
        except Exception:
            pass
        _name_cache[sobject] = result
        return result

    total = passed = failed = skipped = 0

    for tc_id in tc_ids:
        tc = db.query(TestCase).filter(
            TestCase.id == tc_id, TestCase.tenant_id == run.tenant_id,
            TestCase.deleted_at.is_(None),
        ).first()
        if not tc or not tc.current_version_id:
            skipped += 1
            emit_log(run.id,
                     f"Skipped test #{tc_id}: test case or current version missing",
                     level="warn", tenant_id=tenant_id, test_case_id=tc_id)
            continue

        # "Run verbatim" pins test cases to a specific version via
        # run.config.version_pin = {str(tc_id): version_id}. Without a
        # pin we fall back to the TC's current_version_id (normal path).
        pin_map = (run.config or {}).get("version_pin") or {}
        pinned_id = pin_map.get(str(tc.id)) or pin_map.get(tc.id)
        version_id = pinned_id or tc.current_version_id
        version = db.query(TestCaseVersion).filter(
            TestCaseVersion.id == version_id,
        ).first() if version_id else None
        if not version or not version.steps:
            skipped += 1
            emit_log(run.id,
                     f"Skipped test #{tc_id} '{tc.title}': no steps defined",
                     level="warn", tenant_id=tenant_id,
                     test_case_id=tc.id, title=tc.title)
            continue

        # ---- Pre-execution validation gate --------------------------------
        # If the stored validation_report flags critical issues and the run
        # config isn't opting into override, skip the TC with a clear
        # failure rather than wasting a Salesforce API burst that would
        # return a cryptic MALFORMED_ID anyway.
        report = version.validation_report or {}
        cfg = run.config or {}
        override = bool(cfg.get("skip_validation") or cfg.get("force_run"))
        if report.get("status") == "critical" and not override:
            first = next((i for i in report.get("issues", [])
                          if i.get("severity") == "critical"), {})
            summary = f"{report['summary'].get('critical', 0)} critical issue(s)"
            detail = first.get("message") or "See test case detail for issues."
            rtr = rtr_repo.create_result(
                run_id=run.id, test_case_id=tc.id,
                test_case_version_id=version.id,
                environment_id=env.id, status="error",
                total_steps=len(version.steps),
                failure_type="validation_blocked",
                failure_summary=(f"Blocked by static validation: {detail}")[:500],
            )
            failed += 1
            total += 1
            emit_log(run.id,
                     f"Test #{tc.id} '{tc.title}' blocked: {summary}. {detail}",
                     level="error", tenant_id=tenant_id,
                     test_case_id=tc.id)
            try:
                emit_test_finished(
                    run.id, tc.id, "error",
                    tenant_id=tenant_id,
                    error_summary=f"Validation blocked: {detail}"[:200],
                    title=tc.title,
                )
            except Exception:
                pass
            run_repo.update_run_status(
                run.id, "running",
                total_tests=total, passed=passed, failed=failed, skipped=skipped,
            )
            continue

        # Heartbeat per test case so the reaper doesn't kill the worker on
        # slow test suites.
        ctx["heartbeat_repo"].update_heartbeat(
            ctx["worker_id"], current_run_id=run.id, current_stage="execute",
        )

        rtr = rtr_repo.create_result(
            run_id=run.id, test_case_id=tc.id,
            test_case_version_id=version.id,
            environment_id=env.id, status="passed",
            total_steps=len(version.steps),
        )

        emit_test_started(run.id, tc.id, tenant_id=tenant_id,
                          total_steps=len(version.steps), title=tc.title)

        t0 = time.time()
        tc_failed_steps = 0
        tc_passed_steps = 0
        tc_status = "passed"
        failure_summary = None
        failure_type = None

        try:
            executor = StepExecutor(
                sf_client=sf, run_id=run.id,
                capture_mode=env.capture_mode or "smart",
                step_result_repo=step_repo,
                entity_repo=entity_repo,
                idempotency_mgr=idem,
                meta_vr_lookup=_has_vr,
                name_createable_lookup=_name_createable,
                tenant_id=tenant_id,
            )
            for step_def in version.steps:
                step, status = executor.execute_step(
                    rtr.id, step_def, test_case_id=tc.id,
                )
                if status == "passed":
                    tc_passed_steps += 1
                else:
                    tc_failed_steps += 1
                    tc_status = "failed" if status == "failed" else "error"
                    failure_summary = step.error_message if hasattr(step, "error_message") else None
                    failure_type = "step_error"
                    # AUDIT FIX (2026-04-19): persist failure state +
                    # fire feedback signal IMMEDIATELY, in-loop. If the
                    # worker is OOM-killed / SIGTERM'd between here and
                    # the post-loop update_result, the DB still reflects
                    # reality (previously left the rtr in initial
                    # 'passed' + no feedback signal). In-loop write is
                    # idempotent with the post-loop write at line 306.
                    try:
                        rtr_repo.update_result(rtr.id, {
                            "status": tc_status,
                            "passed_steps": tc_passed_steps,
                            "failed_steps": tc_failed_steps,
                            "failure_summary": (failure_summary or "")[:500],
                            "failure_type": failure_type,
                            "duration_ms": int((time.time() - t0) * 1000),
                        })
                    except Exception as uex:
                        log.exception("in-loop rtr update failed: %s", uex)
                    try:
                        from primeqa.intelligence.llm import feedback as _fb
                        _fb.capture(
                            tenant_id=tenant_id,
                            signal_type=_fb.SIGNAL_EXECUTION_FAILED,
                            severity="high",
                            detail={
                                "error": (failure_summary or "")[:300],
                                "failure_type": failure_type,
                            },
                            generation_batch_id=getattr(tc, "generation_batch_id", None),
                            test_case_id=tc.id,
                            test_case_version_id=version.id,
                            ttl_days=14,
                        )
                    except Exception:
                        pass
                    break  # stop on first non-pass (classic SF test harness behavior)
        except Exception as e:
            tc_status = "error"
            failure_summary = f"{type(e).__name__}: {e}"
            failure_type = "unexpected_error"
            log.exception("test case %s on run %s crashed: %s", tc.id, run.id, e)

        # Atomic rtr update via repo (not direct ORM mutation) \u2014 previous
        # attempts set attributes on a potentially-expired instance after
        # multiple commits inside execute_step, so the assignments silently
        # dropped. update_result re-fetches, mutates, commits.
        duration_ms = int((time.time() - t0) * 1000)
        rtr_repo.update_result(rtr.id, {
            "status": tc_status,
            "passed_steps": tc_passed_steps,
            "failed_steps": tc_failed_steps,
            "failure_summary": (failure_summary or "")[:500] if failure_summary else None,
            "failure_type": failure_type,
            "duration_ms": duration_ms,
        })

        # Feedback loop: when a TC ends in error/failed, feed the error
        # text back so the next generation in this tenant learns from
        # the miss (Phase 4 / migration 033).
        #
        # Guard against double-fire: step failures already emitted a signal
        # in-loop (see ~L313). The post-loop capture is only needed for the
        # crash branch (failure_type == "unexpected_error") where the loop
        # was short-circuited by an exception before it could fire. Without
        # this guard, every failed TC was producing two identical signals,
        # doubling the noise in feedback_rules aggregation.
        if (tc_status in ("failed", "error")
                and failure_summary
                and failure_type != "step_error"):
            try:
                from primeqa.intelligence.llm import feedback as _fb
                _fb.capture(
                    tenant_id=tenant_id,
                    signal_type=_fb.SIGNAL_EXECUTION_FAILED,
                    severity="high",
                    detail={
                        "error": (failure_summary or "")[:300],
                        "failure_type": failure_type or "unknown",
                    },
                    generation_batch_id=getattr(tc, "generation_batch_id", None),
                    test_case_id=tc.id,
                    test_case_version_id=version.id,
                    ttl_days=14,
                )
            except Exception:
                pass

        total += 1
        if tc_status == "passed":
            passed += 1
        else:
            failed += 1

        # Write incremental totals after each test case so a worker crash
        # leaves partial results visible in the UI and in the run record.
        run_repo.update_run_status(
            run.id, "running",
            total_tests=total, passed=passed, failed=failed, skipped=skipped,
        )

        # Per-test SSE + durable event
        try:
            emit_test_finished(
                run.id, tc.id, tc_status,
                tenant_id=tenant_id,
                duration_ms=duration_ms,
                passed_steps=tc_passed_steps,
                failed_steps=tc_failed_steps,
                error_summary=(failure_summary or "")[:200] if failure_summary else None,
                title=tc.title,
            )
        except Exception:
            pass

    emit_log(run.id,
             f"Execute finished: {total} tests ({passed} passed, {failed} failed, {skipped} skipped)",
             level="info" if failed == 0 else "warn",
             tenant_id=tenant_id,
             total=total, passed=passed, failed=failed, skipped=skipped)
    log.info("run %s: executed %d tests (%d passed, %d failed, %d skipped)",
             run.id, total, passed, failed, skipped)


def _run_record_stage(stage, ctx):
    """Finalize the run: status from counts, cleanup, SSE notification."""
    run = ctx["run_repo"].get_run(stage.run_id)
    if not run:
        return
    # complete_run / fail_run release the env slot, flip status,
    # and emit the run_status SSE event.
    if run.failed == 0:
        ctx["service"].complete_run(run.id)
    else:
        ctx["service"].fail_run(run.id, error_message=f"{run.failed} test(s) failed")


def _resolve_test_case_ids(db, run):
    """Expand run.source_ids + run.source_type into a flat test_case_id list."""
    from primeqa.test_management.models import TestCase, SuiteTestCase

    source_type = run.source_type
    source_ids = run.source_ids or []

    if source_type in ("test_cases", "rerun"):
        return list(source_ids)

    if source_type == "suite":
        rows = db.query(SuiteTestCase).filter(
            SuiteTestCase.suite_id.in_(source_ids),
        ).all()
        return list({r.test_case_id for r in rows})

    if source_type == "requirements":
        rows = db.query(TestCase).filter(
            TestCase.tenant_id == run.tenant_id,
            TestCase.requirement_id.in_(source_ids),
            TestCase.deleted_at.is_(None),
        ).all()
        return [r.id for r in rows]

    if source_type == "release":
        from primeqa.release.models import ReleaseTestPlanItem
        items = db.query(ReleaseTestPlanItem).filter(
            ReleaseTestPlanItem.release_id.in_(source_ids),
        ).all()
        return list({i.test_case_id for i in items})

    # Unknown source_type: empty list rather than crash
    log.warning("unknown source_type=%s on run %s", source_type, run.id)
    return []


def process_run(run, ctx):
    """Process a single running pipeline run — advance through its stages."""
    stage_repo = ctx["stage_repo"]
    run_repo = ctx["run_repo"]
    service = ctx["service"]

    while True:
        fresh_run = run_repo.get_run(run.id)
        if fresh_run.status == "cancelled":
            log.info(f"Run {run.id} cancelled")
            return

        stage = stage_repo.get_next_pending_stage(run.id)
        if not stage:
            service.complete_run(run.id)
            return

        try:
            success = execute_stage(stage, ctx)
            if not success:
                raise RuntimeError("Stage execution returned failure")
        except Exception as e:
            if stage.attempt < stage.max_attempts:
                stage_repo.update_stage(stage.id, "failed", last_error=str(e))
                stage_repo.increment_attempt(stage.id)
                continue
            else:
                stage_repo.update_stage(stage.id, "failed", last_error=str(e))
                service.fail_run(run.id, error_message=f"Stage {stage.stage_name} failed: {e}")
                return


# ----------------------------------------------------------------------
# §23 enrichment queue worker
# ----------------------------------------------------------------------
#
# The substrate-1 sync engine enqueues ai_enrichment_queue rows —
# `embedding` for every entity, `summary` for Flow + ValidationRule
# (SUMMARY_ENABLED_ENTITY_TYPES). enrichment_tick drains that queue:
# embeddings via the Voyage API (batched, up to 128/call), summaries
# via the Anthropic LLM gateway (per-row, Haiku). It's the 4th
# worker_tick item, called every poll interval.
#
# ai_enrichment_queue lives in the TENANT schema (no tenant_id
# column), so the worker discovers tenant_<N> schemas and runs each
# subtick scoped to one tenant — SET search_path + SET app.tenant_id
# (the latter required: entities carries a tenant_id CHECK assertion
# that re-validates on UPDATE).
#
# Bounded per tick: <=128 embedding rows + <=5 summary rows PER
# TENANT. The poll loop keeps calling, so a large backlog drains over
# many ticks without any single tick running long enough to starve
# the pipeline / metadata / generation ticks.

from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from sqlalchemy import text as _sa_text

from primeqa.intelligence.embeddings import (
    EMBEDDING_MODEL_TAG,
    VoyageError,
    embed_batch,
)
from primeqa.intelligence.llm.limits import record_embedding_usage
from primeqa.sync import readiness

EMBEDDING_BATCH_LIMIT = 128       # Voyage's per-request input cap
SUMMARY_BATCH_LIMIT = 5           # per-row LLM calls — keep ticks short
STALL_THRESHOLD_MINUTES = 10      # in_progress older than this -> reaped
ENRICHMENT_MAX_ATTEMPTS = 5       # retryable failures past this -> permanent


# ----------------------------------------------------------------------
# §24 — TickResult: per-tick outcome breakdown
# ----------------------------------------------------------------------

@dataclass
class OrgDelta:
    """Per-org tally of queue-row outcomes for a single tick."""
    succeeded: int = 0
    failed_retryable: int = 0
    failed_permanent: int = 0


@dataclass
class TickResult:
    """Outcome of a single subtick (embedding or summary).

    ``claimed`` is the total rows claimed at the start of the tick;
    ``per_org`` accumulates per-org outcome counts as the subtick
    processes each row. ``per_org`` is keyed by
    ``str(connected_orgs.id)`` — the uuid stringified so the same
    key shape works whether the worker pulled the id from a fetch
    or threaded it through claim metadata.
    """
    claimed: int = 0
    per_org: Dict[str, OrgDelta] = field(default_factory=dict)

    def credit(
        self,
        org_id: Optional[str],
        *,
        succeeded: int = 0,
        failed_retryable: int = 0,
        failed_permanent: int = 0,
    ) -> None:
        """Add to the per-org tally. A NULL ``org_id`` is dropped
        on the floor (caller has already logged the gap)."""
        if org_id is None:
            return
        d = self.per_org.setdefault(org_id, OrgDelta())
        d.succeeded += succeeded
        d.failed_retryable += failed_retryable
        d.failed_permanent += failed_permanent

    @property
    def did_work(self) -> bool:
        return self.claimed > 0

    @property
    def affected_orgs(self) -> Set[str]:
        return set(self.per_org)

    def succeeded_for(self, org_id: str) -> int:
        return self.per_org.get(org_id, OrgDelta()).succeeded

    def failed_permanent_for(self, org_id: str) -> int:
        return self.per_org.get(org_id, OrgDelta()).failed_permanent


def _discover_tenant_schemas(session) -> list:
    """Return [(schema_name, tenant_id), ...] for every tenant_<N>
    schema in the database.

    Discovered from information_schema rather than shared.tenants so
    the worker doesn't depend on that registry being in sync with the
    actual schemas — and so it works in the test DB, where
    shared.tenants is empty but the tenant_1 schema is fully
    populated.
    """
    rows = session.execute(_sa_text("""
        SELECT schema_name FROM information_schema.schemata
        WHERE schema_name ~ '^tenant_[0-9]+$'
        ORDER BY schema_name
    """)).fetchall()
    out = []
    for (name,) in rows:
        try:
            out.append((name, int(name[len("tenant_"):])))
        except ValueError:
            continue
    return out


def _set_tenant_context(session, schema_name: str, tenant_id: int) -> None:
    """Scope the session to one tenant: search_path for unqualified
    table resolution + app.tenant_id for the entities tenant CHECK
    assertion.

    Issues SET on the current transaction's connection AND installs a
    SQLAlchemy ``after_begin`` event listener that re-applies SET on
    every subsequent transaction this session begins. The listener
    matters because SQLAlchemy releases the connection back to the
    pool on ``session.commit()``; the next ``session.execute()`` may
    check out a different (or recycled) connection that doesn't carry
    the previous SET — even within a single ``enrichment_tick``
    iteration, the per-tick worker session crosses several transaction
    boundaries (``_reap_stalled`` commits, ``_claim_batch`` commits,
    the main subtick commits, the readiness section commits). Per-
    transaction re-application makes the tenant context resilient to
    connection-pool swaps + ``pool_recycle`` resets.
    """
    from sqlalchemy import event

    session.execute(_sa_text(f'SET search_path TO "{schema_name}", public'))
    session.execute(
        _sa_text("SET app.tenant_id = :tid"), {"tid": str(tenant_id)},
    )

    # One listener per session — _tenant_context_listener_installed is
    # a sentinel so repeat _set_tenant_context calls on the same
    # session don't stack listeners.
    if getattr(session, "_tenant_context_listener_installed", False):
        return
    session._tenant_context_listener_installed = True

    _schema = schema_name
    _tid = str(tenant_id)

    @event.listens_for(session, "after_begin")
    def _reapply_tenant_context(_session, _trans, conn):
        conn.execute(_sa_text(f'SET search_path TO "{_schema}", public'))
        conn.execute(_sa_text(f"SET app.tenant_id = '{_tid}'"))


def _reap_stalled(session, primitive_type: str) -> None:
    """Return in_progress rows stuck > STALL_THRESHOLD_MINUTES to
    pending so a crashed/killed worker's claims aren't lost forever.
    Covered by the ai_enrichment_queue_stalled_idx partial index."""
    session.execute(_sa_text("""
        UPDATE ai_enrichment_queue
        SET status = 'pending', started_at = NULL
        WHERE primitive_type = :pt
          AND status = 'in_progress'
          AND started_at < NOW() - make_interval(mins => :mins)
    """), {"pt": primitive_type, "mins": STALL_THRESHOLD_MINUTES})
    session.commit()


def _claim_batch(session, primitive_type: str, limit: int,
                 entity_types: Optional[list] = None) -> list:
    """Atomically claim up to `limit` pending/failed_retryable rows of
    `primitive_type` — FOR UPDATE SKIP LOCKED, safe across concurrent
    workers. Flips them to in_progress, stamps started_at, clears
    completed_at (a re-claimed failed_retryable row had it set), bumps
    attempts. Returns the claimed rows as dicts.

    Claims `failed_retryable` alongside `pending` so transient
    failures get re-tried on a later tick; the attempts counter +
    _mark_failed's ENRICHMENT_MAX_ATTEMPTS cap stops infinite retry.
    Optional entity_types filter — the summary subtick passes
    Flow+ValidationRule defensively.
    """
    et_filter = ""
    params = {"pt": primitive_type, "lim": limit}
    if entity_types:
        et_filter = "AND entity_type = ANY(:ets)"
        params["ets"] = list(entity_types)
    rows = session.execute(_sa_text(f"""
        UPDATE ai_enrichment_queue
        SET status = 'in_progress',
            started_at = NOW(),
            completed_at = NULL,
            attempts = attempts + 1
        WHERE id IN (
            SELECT id FROM ai_enrichment_queue
            WHERE status IN ('pending', 'failed_retryable')
              AND primitive_type = :pt
              {et_filter}
            ORDER BY enqueued_at
            LIMIT :lim
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, entity_type, entity_id, attempts
    """), params).fetchall()
    session.commit()
    return [
        {"queue_id": r[0], "entity_type": r[1],
         "entity_id": str(r[2]), "attempts": r[3]}
        for r in rows
    ]


def _mark_succeeded(session, queue_id) -> None:
    session.execute(_sa_text("""
        UPDATE ai_enrichment_queue
        SET status = 'succeeded', completed_at = NOW(), error_text = NULL
        WHERE id = :id
    """), {"id": queue_id})


def _mark_failed(session, queue_id, retryable: bool, err: str,
                 attempts: int) -> str:
    """Mark a claimed row failed. A retryable failure becomes
    failed_retryable (re-tried on a later tick) UNLESS attempts have
    hit ENRICHMENT_MAX_ATTEMPTS, at which point it's failed_permanent.
    A non-retryable failure is failed_permanent immediately.

    Returns the status string written (``'failed_retryable'`` or
    ``'failed_permanent'``) so the subtick can credit the right
    TickResult counter without re-deriving the classification."""
    if retryable and attempts < ENRICHMENT_MAX_ATTEMPTS:
        status = "failed_retryable"
    else:
        status = "failed_permanent"
    session.execute(_sa_text("""
        UPDATE ai_enrichment_queue
        SET status = :st, completed_at = NOW(), error_text = :err
        WHERE id = :id
    """), {"st": status, "err": (err or "")[:2000], "id": queue_id})
    return status


def _vec_literal(vec) -> str:
    """pgvector text form '[v1,v2,...]' — no spaces (SQLAlchemy text()
    chokes on '::' so callers CAST(:lit AS vector))."""
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


def _write_embedding(session, entity_id: str, vec) -> None:
    """Write the embedding to entities.embedding for the exact entity
    version that was enqueued. The entities tenant CHECK assertion
    re-validates on this UPDATE — _set_tenant_context must have run."""
    session.execute(_sa_text("""
        UPDATE entities
        SET embedding = CAST(:vec AS vector),
            embedding_model = :model,
            embedding_generated_at = NOW()
        WHERE id = :id
    """), {"vec": _vec_literal(vec), "model": EMBEDDING_MODEL_TAG,
           "id": entity_id})


def _write_summary(session, entity_type: str, entity_id: str,
                   summary_text: str, summary_vec, model: str,
                   prompt_version: str) -> None:
    """Write summary_text + summary_embedding + summary_model +
    summary_prompt_version + summary_generated_at to the detail table.
    `table` is a fixed map from a CHECK-constrained entity_type — not
    user input — so the f-string interpolation is safe."""
    table = ("flow_details" if entity_type == "Flow"
             else "validation_rule_details")
    session.execute(_sa_text(f"""
        UPDATE {table}
        SET summary_text = :txt,
            summary_embedding = CAST(:vec AS vector),
            summary_model = :model,
            summary_prompt_version = :pv,
            summary_generated_at = NOW()
        WHERE entity_id = :id
    """), {"txt": summary_text, "vec": _vec_literal(summary_vec),
           "model": model, "pv": prompt_version, "id": entity_id})


def _fetch_semantic_texts(session, entity_ids: list) -> dict:
    """Batch-read entities.semantic_text for the claimed entity_ids.
    Returns {entity_id(str): semantic_text}."""
    if not entity_ids:
        return {}
    rows = session.execute(_sa_text("""
        SELECT id, semantic_text FROM entities
        WHERE id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": entity_ids}).fetchall()
    return {str(eid): st for (eid, st) in rows}


def _fetch_org_ids(session, entity_ids: list) -> Dict[str, Optional[str]]:
    """Batch-read ``entities.last_synced_from_org_id`` for the claimed
    entity_ids. Returns ``{entity_id(str): org_id(str) | None}``.

    The org_id is needed to credit per-org TickResult deltas to the
    right ``sync_runs`` row (queue rows have no direct sync_run
    linkage; the transitive ``entity → connected_org → sync_run``
    chain is the only path).

    An entity row with a NULL ``last_synced_from_org_id`` shouldn't
    normally exist (the structural materializer always stamps it).
    If we see one, log at WARNING and let the caller drop the row's
    delta on the floor — better than a crash, better than crediting
    an arbitrary run.
    """
    if not entity_ids:
        return {}
    rows = session.execute(_sa_text("""
        SELECT id, last_synced_from_org_id FROM entities
        WHERE id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": entity_ids}).fetchall()
    out: Dict[str, Optional[str]] = {}
    for eid, org_id in rows:
        eid_s = str(eid)
        if org_id is None:
            log.warning(
                "_fetch_org_ids: entity_id=%s has no last_synced_from_org_id; "
                "succeeded queue row will not be credited to any sync_run. "
                "This indicates an entity materialized outside the normal "
                "sync flow.",
                eid_s,
            )
            out[eid_s] = None
        else:
            out[eid_s] = str(org_id)
    return out


def _fetch_summary_context(session, entity_type: str,
                           entity_id: str) -> Optional[dict]:
    """Assemble the prompt context for one entity, or None if the
    entity / detail row isn't found.

    Pulls sf_api_name + semantic_text + the attributes JSONB + detail-
    table columns, mapped to the keys the entity_summary_{flow,
    validation_rule} prompts expect. Those prompts are defensive
    (every key optional), so a sparse context still yields a usable
    summary.
    """
    if entity_type == "Flow":
        row = session.execute(_sa_text("""
            SELECT e.sf_api_name, e.semantic_text, e.attributes,
                   fd.flow_type, fd.trigger_type, fd.is_active,
                   obj.sf_api_name AS trigger_obj
            FROM entities e
            JOIN flow_details fd ON fd.entity_id = e.id
            LEFT JOIN entities obj
                ON obj.id = fd.triggers_on_object_entity_id
            WHERE e.id = :id
        """), {"id": entity_id}).fetchone()
        if row is None:
            return None
        attrs = row[2] or {}
        meta = attrs.get("Metadata") or {}
        return {
            "name": row[0],
            "semantic_text": row[1],
            "process_type": row[3],
            "trigger_type": row[4],
            "is_active": row[5],
            "triggers_on_object": row[6],
            "description": meta.get("description") or attrs.get("description"),
        }
    if entity_type == "ValidationRule":
        row = session.execute(_sa_text("""
            SELECT e.sf_api_name, e.semantic_text, e.attributes,
                   obj.sf_api_name AS obj_name
            FROM entities e
            JOIN validation_rule_details vrd ON vrd.entity_id = e.id
            LEFT JOIN entities obj ON obj.id = vrd.object_entity_id
            WHERE e.id = :id
        """), {"id": entity_id}).fetchone()
        if row is None:
            return None
        attrs = row[2] or {}
        meta = attrs.get("Metadata") or {}
        return {
            "name": row[0],
            "semantic_text": row[1],
            "object": row[3],
            "error_message": meta.get("errorMessage"),
            "formula_text": meta.get("errorConditionFormula"),
            "error_display_field": meta.get("errorDisplayField"),
        }
    return None


def _embedding_subtick(session, tenant_id: int) -> TickResult:
    """Claim a batch of embedding rows for one tenant, embed via the
    Voyage API, write entities.embedding, mark the queue rows.

    Returns a :class:`TickResult` with ``claimed`` count and a per-org
    breakdown of ``succeeded`` / ``failed_retryable`` /
    ``failed_permanent`` for the readiness wiring downstream.
    """
    result = TickResult()
    _reap_stalled(session, "embedding")
    claimed = _claim_batch(session, "embedding", EMBEDDING_BATCH_LIMIT)
    result.claimed = len(claimed)
    if not claimed:
        return result

    entity_ids = [r["entity_id"] for r in claimed]
    texts_by_id = _fetch_semantic_texts(session, entity_ids)
    org_by_id = _fetch_org_ids(session, entity_ids)

    # Partition: rows with usable semantic_text get embedded; rows with
    # NULL/blank text are a no-op success — defensive against a sync
    # bug that left semantic_text empty (re-running the sync is the
    # fix, not a permanent enrichment failure).
    embeddable = []
    for r in claimed:
        st = texts_by_id.get(r["entity_id"])
        if st and st.strip():
            embeddable.append((r, st))
        else:
            _mark_succeeded(session, r["queue_id"])
            result.credit(org_by_id.get(r["entity_id"]), succeeded=1)

    if not embeddable:
        session.commit()
        return result

    def _credit_fail(rows, retryable: bool, err: str):
        for (rr, _) in rows:
            status = _mark_failed(session, rr["queue_id"], retryable,
                                  err, rr["attempts"])
            if status == "failed_retryable":
                result.credit(org_by_id.get(rr["entity_id"]),
                              failed_retryable=1)
            else:
                result.credit(org_by_id.get(rr["entity_id"]),
                              failed_permanent=1)

    try:
        vectors = embed_batch(
            [t for (_, t) in embeddable], input_type="document",
        )
    except VoyageError as e:
        _credit_fail(embeddable, e.retryable, str(e))
        session.commit()
        return result

    if len(vectors) != len(embeddable):
        # Voyage should return one vector per input — treat a count
        # mismatch as a transient anomaly worth a retry.
        _credit_fail(
            embeddable, True,
            f"Voyage returned {len(vectors)} vectors for "
            f"{len(embeddable)} inputs",
        )
        session.commit()
        return result

    for (r, _), vec in zip(embeddable, vectors):
        _write_embedding(session, r["entity_id"], vec)
        _mark_succeeded(session, r["queue_id"])
        result.credit(org_by_id.get(r["entity_id"]), succeeded=1)
    session.commit()

    # Rate-limit accounting — one llm_usage_log row per Voyage batch.
    try:
        record_embedding_usage(
            tenant_id, batch_size=len(embeddable),
            model=EMBEDDING_MODEL_TAG,
            context={"primitive": "embedding"},
        )
    except Exception as e:
        log.warning("record_embedding_usage failed (tenant %s): %s",
                    tenant_id, e)
    return result


def _summary_subtick(session, tenant_id: int) -> TickResult:
    """Claim a small batch of summary rows for one tenant, generate a
    plain-English summary via the LLM gateway, embed it, write the
    detail-table summary_* fields.

    Returns a :class:`TickResult` with ``claimed`` count and a per-org
    breakdown of ``succeeded`` / ``failed_retryable`` /
    ``failed_permanent`` for the readiness wiring downstream.
    """
    result = TickResult()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # No key -> can't summarise. Leave rows pending (don't claim);
        # log so the gap is visible.
        log.warning("enrichment summary subtick: ANTHROPIC_API_KEY not "
                    "set; skipping summary enrichment")
        return result

    _reap_stalled(session, "summary")
    claimed = _claim_batch(session, "summary", SUMMARY_BATCH_LIMIT,
                           entity_types=["Flow", "ValidationRule"])
    result.claimed = len(claimed)
    if not claimed:
        return result

    org_by_id = _fetch_org_ids(session, [r["entity_id"] for r in claimed])

    def _credit_fail(r, retryable: bool, err: str):
        status = _mark_failed(session, r["queue_id"], retryable,
                              err, r["attempts"])
        if status == "failed_retryable":
            result.credit(org_by_id.get(r["entity_id"]),
                          failed_retryable=1)
        else:
            result.credit(org_by_id.get(r["entity_id"]),
                          failed_permanent=1)

    from primeqa.intelligence.llm.gateway import llm_call, LLMError

    for r in claimed:
        et = r["entity_type"]
        context = _fetch_summary_context(session, et, r["entity_id"])
        if context is None:
            _credit_fail(r, False, "entity / detail row not found")
            continue

        task = ("entity_summary_validation_rule"
                if et == "ValidationRule" else "entity_summary_flow")
        try:
            resp = llm_call(task=task, tenant_id=tenant_id,
                            api_key=api_key, context=context)
        except LLMError as e:
            # LLMError carries .status, not .retryable. Auth + content
            # errors are terminal; everything else (rate_limited,
            # provider_error, quota_exceeded) is worth a retry.
            retryable = e.status not in ("auth_error", "content_error")
            _credit_fail(r, retryable, f"{e.status}: {e.message}")
            continue
        except Exception as e:
            _credit_fail(r, True, f"{type(e).__name__}: {e}")
            continue

        summary_text = (resp.parsed_content or "").strip()
        if not summary_text:
            _credit_fail(r, True, "LLM returned an empty summary")
            continue

        # Embed the generated summary -> detail-table summary_embedding.
        try:
            summary_vec = embed_batch(
                [summary_text], input_type="document",
            )[0]
        except VoyageError as e:
            _credit_fail(r, e.retryable, f"summary embedding failed: {e}")
            continue

        _write_summary(session, et, r["entity_id"], summary_text,
                       summary_vec, model=resp.model,
                       prompt_version=resp.prompt_version)
        _mark_succeeded(session, r["queue_id"])
        result.credit(org_by_id.get(r["entity_id"]), succeeded=1)

    session.commit()
    return result


def enrichment_tick(db_factory=None) -> bool:
    """Drain a bounded slice of ai_enrichment_queue across all tenants.

    For each tenant_<N> schema: open a short-lived session scoped to
    that tenant (search_path + app.tenant_id), run the embedding
    subtick then the summary subtick. Bounded per tenant per tick so a
    big backlog drains over many ticks without starving the other
    worker_tick items.

    Returns True iff ANY tenant did work — lets a caller distinguish
    "queue had work" from "queue drained" (the integration test loops
    on this).

    `db_factory()` returns a fresh Session. Defaults to the worker's
    SessionLocal; the integration test injects a factory bound to the
    live test engine.
    """
    if db_factory is None:
        from primeqa.db import SessionLocal
        db_factory = SessionLocal

    # Tenant discovery is a public-schema query — run it before any
    # per-tenant search_path is set.
    disc = db_factory()
    try:
        tenants = _discover_tenant_schemas(disc)
    finally:
        disc.close()

    did_work = False
    for schema_name, tenant_id in tenants:
        session = db_factory()
        try:
            _set_tenant_context(session, schema_name, tenant_id)
            emb_result = _embedding_subtick(session, tenant_id)
            sum_result = _summary_subtick(session, tenant_id)
            if emb_result.did_work or sum_result.did_work:
                did_work = True

            # §24 — feature readiness wiring. For each org touched by
            # this tick, credit the active sync_run's enrichment
            # counters, recompute connected_orgs.ai_enrichment_status,
            # and finalize the run if it reached 'complete'.
            #
            # Orgs are unioned across both subticks so an org touched
            # only by embeddings still gets its readiness recomputed
            # in case the summary side has nothing pending (e.g., an
            # org with zero Flow + zero VR — embedding completion is
            # sufficient to reach 'complete').
            affected = emb_result.affected_orgs | sum_result.affected_orgs
            for org_id in affected:
                readiness.increment_run_counters(
                    session, org_id,
                    embeddings_delta=emb_result.succeeded_for(org_id),
                    summaries_delta=sum_result.succeeded_for(org_id),
                    summaries_failed_delta=(
                        sum_result.failed_permanent_for(org_id)
                    ),
                )
                readiness.apply_org_status(session, org_id)
                readiness.maybe_finalize_run(session, org_id)
            if affected:
                session.commit()
        except Exception as e:
            log.warning("enrichment_tick: tenant %s failed: %s",
                        schema_name, e)
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            session.close()
    return did_work


def _default_s3_api_key_resolver(db_factory):
    """Worker-side api_key resolution (D-106.4 slice-3 amendment): the Anthropic
    key is environment -> connection-scoped (Fernet-decrypted via the v1
    connection store), so resolve it through the v1 repos. Returns a closure
    `(tenant_id, environment_id) -> api_key`."""
    from primeqa.core.repository import ConnectionRepository, EnvironmentRepository

    def _resolve(tenant_id, environment_id):
        if environment_id is None:
            raise ValueError(f"s3 job for tenant {tenant_id} has no environment_id")
        db = db_factory()
        try:
            env = EnvironmentRepository(db).get_environment(environment_id, tenant_id)
            if env is None or not env.llm_connection_id:
                raise ValueError(
                    f"environment {environment_id} has no llm_connection_id")
            conn = ConnectionRepository(db).get_connection_decrypted(
                env.llm_connection_id, tenant_id)
            return (conn or {}).get("config", {}).get("api_key", "")
        finally:
            db.close()

    return _resolve


def s3_generation_tick(db_factory=None, *, tool_turn_fn=None, api_key_resolver=None) -> dict:
    """Drive the substrate-3 generation queue (D-106.4 slice 3): one job per
    tenant per tick off ``s3_generation_jobs`` (per-tenant). Discovers tenant
    schemas (the enrichment pattern) and delegates to the resilient per-tenant
    loop. The queue is empty until the slice-4 enqueue ships, so this no-ops
    today. ``api_key_resolver`` defaults to the worker-side v1-repo resolver;
    tests inject a stub."""
    if db_factory is None:
        from primeqa.db import SessionLocal
        db_factory = SessionLocal

    disc = db_factory()
    try:
        tenants = _discover_tenant_schemas(disc)
    finally:
        disc.close()
    tenant_ids = [tid for _, tid in tenants]
    if not tenant_ids:
        return {}

    if api_key_resolver is None:
        api_key_resolver = _default_s3_api_key_resolver(db_factory)
    from primeqa.generation.consumer import run_s3_generation_tick
    return run_s3_generation_tick(
        tenant_ids, api_key_resolver=api_key_resolver, tool_turn_fn=tool_turn_fn)


def _default_s4_client_resolver(db_factory):
    """Worker-side Tooling-client resolution (D-132): the SF token is
    environment -> connection-scoped (the D-106.4 credential path), resolved via
    the v1 store. Returns a closure ``(tenant_id, environment_id) ->
    ToolingReadClient``. Opens + closes a v1 session per resolve, so no connection
    is held into the async run's execute bracket (D-129)."""
    from primeqa.execution_engine.credentials import resolve_tooling_client

    def _resolve(tenant_id, environment_id):
        if environment_id is None:
            raise ValueError(f"s4 job for tenant {tenant_id} has no environment_id")
        db = db_factory()
        try:
            return resolve_tooling_client(db, environment_id)
        finally:
            db.close()

    return _resolve


def s4_execution_tick(db_factory=None, *, client_resolver=None) -> dict:
    """Drive the substrate-4 execution queue (D-132): one job per tenant per tick
    off ``s4_execution_jobs`` (per-tenant). Discovers tenant schemas + delegates to
    the resilient per-tenant loop. The queue is empty until an enqueue source ships
    (deferred), so this no-ops today. ``client_resolver`` defaults to the
    worker-side Tooling resolver; tests inject a stub."""
    if db_factory is None:
        from primeqa.db import SessionLocal
        db_factory = SessionLocal

    disc = db_factory()
    try:
        tenants = _discover_tenant_schemas(disc)
    finally:
        disc.close()
    tenant_ids = [tid for _, tid in tenants]
    if not tenant_ids:
        return {}

    if client_resolver is None:
        client_resolver = _default_s4_client_resolver(db_factory)
    from primeqa.execution_engine.consumer import run_s4_execution_tick
    return run_s4_execution_tick(tenant_ids, client_resolver=client_resolver)


def worker_tick(ctx):
    """Single poll iteration: drive pipeline runs AND metadata syncs."""
    # 1) Pipeline runs (existing)
    run_repo = ctx["run_repo"]
    runs = run_repo.get_running_runs()
    for run in runs:
        stage = ctx["stage_repo"].get_next_pending_stage(run.id)
        if stage:
            process_run(run, ctx)

    # 2) Metadata sync jobs (migration 025)
    # Process at most one per tick to keep pipeline work responsive. The
    # metadata sync is a long-running SF API burst, so one worker claims it
    # and iterates categories; other workers (if any) still serve runs.
    try:
        from primeqa.metadata.worker_runner import poll_and_run_once
        poll_and_run_once(ctx["db"], ctx["worker_id"])
    except Exception as e:
        log.warning("metadata worker tick failed: %s", e)

    # 3) Generation jobs (migration 044). One per tick keeps cost under
    # control — each job is a 15-60s LLM round-trip. The claim uses
    # SELECT FOR UPDATE SKIP LOCKED so multiple workers never race the
    # same row. Session used for claim is separate from the session
    # process_job opens internally (process_job uses a short-lived
    # Session per call to avoid stepping on the long-lived worker
    # ctx session).
    try:
        from primeqa.db import SessionLocal
        from primeqa.intelligence.generation_jobs import (
            claim_next_queued_job, process_job,
        )
        claim_db = SessionLocal()
        try:
            gen_job = claim_next_queued_job(claim_db)
        finally:
            claim_db.close()
        if gen_job is not None:
            process_job(gen_job, db_factory=SessionLocal)
    except Exception as e:
        log.warning("generation worker tick failed: %s", e)

    # 3b) S3 generation jobs (D-106.4). Per-tenant queue (s3_generation_jobs),
    # mirroring the v1 generation tick but per-tenant. One job per tenant per
    # tick; resilient per-tenant. The queue is empty until the slice-4 enqueue
    # ships, so this no-ops today.
    try:
        s3_generation_tick()
    except Exception as e:
        log.warning("s3 generation worker tick failed: %s", e)

    # 3c) S4 execution jobs (D-132). Per-tenant queue (s4_execution_jobs); one job
    # per tenant per tick through the async run path. The queue is empty until an
    # enqueue source ships (deferred), so this no-ops today.
    try:
        s4_execution_tick()
    except Exception as e:
        log.warning("s4 execution worker tick failed: %s", e)

    # 4) Enrichment queue (§23). Drains ai_enrichment_queue across all
    # tenant schemas — embeddings via the Voyage API (batched up to
    # 128/call), summaries via the Anthropic gateway (per-row Haiku).
    # Bounded per tenant per tick (<=128 embeddings + <=5 summaries)
    # so a large backlog drains over many ticks without starving the
    # ticks above. Own short-lived per-tenant sessions, like
    # process_job — never touches ctx["db"].
    try:
        from primeqa.db import SessionLocal
        enrichment_tick(db_factory=SessionLocal)
    except Exception as e:
        log.warning("enrichment worker tick failed: %s", e)


def run_worker():
    """Main worker loop.

    Observability contract (audit 2026-04-19):
    - SIGTERM handler sets died_reason='SIGTERM' before the normal
      finally block runs. Railway sends SIGTERM on deploy / restart —
      this distinguishes 'clean redeploy' from 'actual crash'.
    - Uncaught exception is caught in the outer except and written as
      died_reason with the truncated exception string before re-raise.
    - Structured start + stop log lines (JSON-ish key=value) so
      anything scraping logs can filter on `worker_lifecycle`.
    - KeyboardInterrupt still does the clean shutdown path.
    """
    import signal
    import sys

    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    pid = os.getpid()
    log.info("worker_lifecycle=start worker_id=%s pid=%s", worker_id, pid)
    print(f"Worker {worker_id} starting (pid={pid})...")

    ctx = create_worker_context()
    ctx["worker_id"] = worker_id
    ctx["heartbeat_repo"].register_worker(worker_id)

    # State we want to persist on death — avoid recreating context.
    _shutdown_reason = {"value": None}

    def _sigterm(signum, frame):
        _shutdown_reason["value"] = f"SIGTERM (signal {signum})"
        log.warning("worker_lifecycle=sigterm worker_id=%s signal=%s", worker_id, signum)
        # Let the main loop's KeyboardInterrupt handling run (raise it).
        raise KeyboardInterrupt(f"SIGTERM {signum}")

    signal.signal(signal.SIGTERM, _sigterm)
    # Some Railway setups also send SIGHUP on reconfig; treat it the same.
    try:
        signal.signal(signal.SIGHUP, _sigterm)
    except (AttributeError, ValueError):
        pass  # Windows / non-main-thread

    last_heartbeat = time.time()

    try:
        while True:
            worker_tick(ctx)
            if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                ctx["heartbeat_repo"].update_heartbeat(worker_id)
                last_heartbeat = time.time()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        reason = _shutdown_reason["value"] or "KeyboardInterrupt"
        log.info("worker_lifecycle=stop worker_id=%s reason=%s", worker_id, reason)
        print(f"Worker {worker_id} shutting down: {reason}")
        ctx["heartbeat_repo"].mark_dead(worker_id, died_reason=reason)
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"[:255]
        log.exception("worker_lifecycle=crash worker_id=%s reason=%s", worker_id, reason)
        try:
            ctx["heartbeat_repo"].mark_dead(worker_id, died_reason=reason)
        except Exception:
            pass
        raise
    finally:
        try:
            ctx["db"].close()
        except Exception:
            pass


if __name__ == "__main__":
    run_worker()

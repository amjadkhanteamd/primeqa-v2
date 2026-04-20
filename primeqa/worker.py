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

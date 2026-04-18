"""Server-Sent Events for live run updates.

Two separate concerns:

1. **Event bus** (`EventBus`) — in-process pub/sub. Worker publishes
   `step_started` / `step_finished` / `run_status` events per run_id.
   Flask SSE endpoint subscribes and drains to the HTTP response.
   Deliberately single-process for R1; swap for Redis pubsub when we scale.

2. **Polling fallback** — if no events arrive within `poll_interval`,
   the endpoint snapshots the run from DB and sends a synthetic
   `run_snapshot` event so the UI still refreshes on long steps.

This keeps behaviour correct even when:
 - Worker runs on a different dyno (events fired but not visible here)
 - Client reconnects mid-run (snapshot fills the gap)
 - Browser proxy buffers (heartbeats keep the stream hot)
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import defaultdict
from typing import Any, Dict, Generator, Iterable, List, Optional

log = logging.getLogger(__name__)


SSE_HEARTBEAT_SEC = 15     # comment line to keep proxies from closing
SSE_SNAPSHOT_SEC = 5       # DB snapshot if no bus event within this
SSE_MAX_DURATION_SEC = 600 # hard cap per connection (client reconnects after)


class EventBus:
    """Per-run event fanout. Thread-safe. One instance per Flask process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # run_id -> list of queues (one per subscriber)
        self._subs: Dict[int, List["queue.Queue"]] = defaultdict(list)

    def publish(self, run_id: int, event: Dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs.get(run_id, []))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                log.warning("event bus queue full for run=%s; dropping event", run_id)

    def subscribe(self, run_id: int, max_queue: int = 256) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue(maxsize=max_queue)
        with self._lock:
            self._subs[run_id].append(q)
        return q

    def unsubscribe(self, run_id: int, q: "queue.Queue") -> None:
        with self._lock:
            subs = self._subs.get(run_id)
            if not subs:
                return
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                self._subs.pop(run_id, None)


# Module-level singleton. Worker/web/scheduler imports the same object because
# Python import cache is per-process.
BUS = EventBus()


# ---- SSE generator ---------------------------------------------------------

def _sse_format(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _heartbeat() -> str:
    return f": ping {int(time.time())}\n\n"


def stream_run_events(run_id: int, snapshot_fn,
                      tail_events_fn=None, *,
                      initial_events_fn=None,
                      heartbeat_sec: int = SSE_HEARTBEAT_SEC,
                      snapshot_sec: int = SSE_SNAPSHOT_SEC,
                      events_poll_sec: float = 1.0,
                      max_duration_sec: int = SSE_MAX_DURATION_SEC) -> Generator[str, None, None]:
    """Generator producing SSE frames for one run.

    Delivery channels, in priority order:
      1. In-process BUS (sub-second when web + worker share a process).
      2. DB `run_events` tail via `tail_events_fn(since_id)` returning a
         list of event dicts. This is what makes cross-service delivery
         work on Railway \u2014 worker writes to DB, web tails it.
      3. DB snapshot via `snapshot_fn()` every `snapshot_sec` \u2014 covers
         cumulative state (status, counts) that isn't an event.

    On connect: `initial_events_fn()` returns the last N events already
    logged for the run so page refresh repopulates the log panel.

    The generator exits when:
      - run reaches a terminal status and snapshot emitted
      - connection times out (`max_duration_sec`)
      - `SIGPIPE` / client disconnect (handled by Flask)
    """
    q = BUS.subscribe(run_id)
    last_event_id = 0
    try:
        # Initial snapshot \u2014 gives the client current status + counts
        try:
            snap = snapshot_fn()
            yield _sse_format("run_snapshot", snap)
        except Exception as e:
            log.warning("initial snapshot failed for run=%s: %s", run_id, e)

        # Initial backfill of events so the log panel shows history on refresh
        if initial_events_fn is not None:
            try:
                events = initial_events_fn() or []
                for ev in events:
                    if ev.get("id", 0) > last_event_id:
                        last_event_id = ev["id"]
                    yield _sse_format(ev.get("kind", "log"), ev)
            except Exception as e:
                log.warning("initial events load failed for run=%s: %s", run_id, e)

        started = time.time()
        last_snapshot = started
        last_events_poll = 0.0
        terminal = False

        while True:
            elapsed = time.time() - started
            if elapsed > max_duration_sec:
                yield _sse_format("stream_ending", {"reason": "max_duration"})
                break

            # Short wait on BUS so we can interleave DB tails
            wait = min(heartbeat_sec, max(events_poll_sec, 0.25))
            try:
                event = q.get(timeout=wait)
                yield _sse_format(event.get("type", "event"), event.get("data") or event)
                if event.get("type") == "run_status" and event.get("data", {}).get("terminal"):
                    terminal = True
            except queue.Empty:
                now = time.time()

                # Poll DB for events the worker wrote but the BUS didn't
                # carry (i.e. cross-service case).
                if tail_events_fn is not None and now - last_events_poll >= events_poll_sec:
                    try:
                        new_events = tail_events_fn(last_event_id) or []
                        for ev in new_events:
                            if ev.get("id", 0) > last_event_id:
                                last_event_id = ev["id"]
                            yield _sse_format(ev.get("kind", "log"), ev)
                            if ev.get("kind") == "run_status" and ev.get("context", {}).get("terminal"):
                                terminal = True
                        last_events_poll = now
                    except Exception as e:
                        log.warning("event tail failed for run=%s: %s", run_id, e)

                # Periodic snapshot for status/count convergence
                if now - last_snapshot >= snapshot_sec:
                    try:
                        snap = snapshot_fn()
                        yield _sse_format("run_snapshot", snap)
                        last_snapshot = now
                        status = (snap or {}).get("status")
                        if status in ("completed", "failed", "cancelled"):
                            terminal = True
                    except Exception as e:
                        log.warning("snapshot failed for run=%s: %s", run_id, e)
                yield _heartbeat()

            if terminal:
                # Drain any remaining DB events so the log ends cleanly
                if tail_events_fn is not None:
                    try:
                        new_events = tail_events_fn(last_event_id) or []
                        for ev in new_events:
                            yield _sse_format(ev.get("kind", "log"), ev)
                    except Exception:
                        pass
                try:
                    yield _sse_format("run_snapshot", snapshot_fn())
                except Exception:
                    pass
                yield _sse_format("stream_ending", {"reason": "terminal"})
                break
    finally:
        BUS.unsubscribe(run_id, q)


# ---- Convenience helpers for executor/worker -------------------------------
#
# Each emit_* helper does two things:
#   1. Publishes to the in-process BUS (sub-second latency within a single
#      process \u2014 e.g. when web and worker run in the same flask dev server).
#   2. Persists to the run_events table via record_event() so that a
#      **different** process (Railway's separate web service) can pick up
#      the event by polling the DB. This is how cross-service real-time
#      delivery works without a Redis/broker dependency.
#
# record_event() is resilient: it swallows DB errors and logs a warning.
# Callers should not depend on event persistence for correctness \u2014 the
# durable source of truth remains pipeline_runs / pipeline_stages /
# run_test_results / run_step_results. Events are purely for UX.


def record_event(run_id: int, tenant_id: int, kind: str, message: str,
                 *, level: str = "info", context: Optional[dict] = None) -> None:
    """Write one row to run_events. Opens a FRESH, non-scoped session so
    the caller's transaction/session lifecycle isn't affected.

    Critical subtlety: `primeqa.db.SessionLocal` is a scoped_session that
    returns the thread-local session on each call. If we used it here, a
    subsequent session.close() would close the caller's session too and
    detach every instance they're holding \u2014 which was the actual cause
    of "Instance <PipelineRun> is not bound to a Session" we saw in
    production after calling emit_* from worker/service code.
    """
    try:
        from sqlalchemy.orm import Session
        from primeqa.db import engine
        from primeqa.execution.models import RunEvent
        session = Session(bind=engine)
        try:
            ev = RunEvent(
                run_id=run_id, tenant_id=tenant_id,
                kind=kind, level=level, message=message,
                context=context or {},
            )
            session.add(ev)
            session.commit()
        finally:
            session.close()
    except Exception as e:
        log.warning("record_event failed run=%s kind=%s: %s", run_id, kind, e)


def _emit(run_id: int, event_type: str, data: dict, *,
          tenant_id: Optional[int] = None, message: Optional[str] = None,
          level: str = "info", persist: bool = True) -> None:
    """Single entry point: publish to BUS and (optionally) persist to DB.

    `message` is the human-readable line for the log panel and download.
    When None, it's derived from the event type + data.
    """
    BUS.publish(run_id, {"type": event_type, "data": data})
    if persist and tenant_id is not None:
        msg = message or _default_message(event_type, data)
        record_event(run_id, tenant_id, event_type, msg, level=level, context=data)


def _default_message(event_type: str, data: dict) -> str:
    if event_type == "stage_started":
        return f"Stage {data.get('stage_name', '?')} started"
    if event_type == "stage_finished":
        status = data.get("status", "?")
        dur = data.get("duration_ms")
        dur_s = f" ({dur}ms)" if dur else ""
        return f"Stage {data.get('stage_name', '?')} {status}{dur_s}"
    if event_type == "test_started":
        return f"Test #{data.get('test_case_id', '?')} started ({data.get('total_steps', '?')} steps)"
    if event_type == "test_finished":
        status = data.get("status", "?")
        dur = data.get("duration_ms")
        dur_s = f" in {dur / 1000:.1f}s" if dur else ""
        err = f" \u2014 {data.get('error_summary')}" if data.get("error_summary") else ""
        return f"Test #{data.get('test_case_id', '?')} {status}{dur_s}{err}"
    if event_type == "step_started":
        return f"Step {data.get('step_order', '?')} {data.get('action', '?')} on {data.get('target_object', '?')}"
    if event_type == "step_finished":
        status = data.get("status", "?")
        dur = data.get("duration_ms")
        dur_s = f" ({dur}ms)" if dur else ""
        err = f" \u2014 {data.get('error_summary')}" if data.get("error_summary") else ""
        return f"Step {data.get('step_order', '?')} {status}{dur_s}{err}"
    if event_type == "run_status":
        return f"Run {data.get('status', '?')}"
    if event_type == "log":
        return str(data.get("message") or "")
    return f"{event_type}: {data}"


def emit_stage_started(run_id: int, stage_name: str, *,
                       tenant_id: Optional[int] = None) -> None:
    _emit(run_id, "stage_started",
          {"stage_name": stage_name},
          tenant_id=tenant_id)


def emit_stage_finished(run_id: int, stage_name: str, status: str, *,
                        tenant_id: Optional[int] = None,
                        duration_ms: Optional[int] = None,
                        error_summary: Optional[str] = None) -> None:
    data: Dict[str, Any] = {"stage_name": stage_name, "status": status}
    if duration_ms is not None:
        data["duration_ms"] = duration_ms
    if error_summary:
        data["error_summary"] = error_summary
    level = "error" if status in ("failed", "error") else "info"
    _emit(run_id, "stage_finished", data, tenant_id=tenant_id, level=level)


def emit_test_started(run_id: int, test_case_id: int, *,
                      tenant_id: Optional[int] = None,
                      total_steps: Optional[int] = None,
                      title: Optional[str] = None) -> None:
    data: Dict[str, Any] = {"test_case_id": test_case_id}
    if total_steps is not None:
        data["total_steps"] = total_steps
    if title:
        data["title"] = title
    _emit(run_id, "test_started", data, tenant_id=tenant_id)


def emit_step_started(run_id: int, test_case_id: int, step_order: int,
                      *, tenant_id: Optional[int] = None, **kwargs) -> None:
    data = {"test_case_id": test_case_id, "step_order": step_order, **kwargs}
    _emit(run_id, "step_started", data, tenant_id=tenant_id)


def emit_step_finished(run_id: int, test_case_id: int, step_order: int,
                       status: str, *, tenant_id: Optional[int] = None,
                       **kwargs) -> None:
    data = {"test_case_id": test_case_id, "step_order": step_order,
            "status": status, **kwargs}
    level = "error" if status in ("failed", "error") else "info"
    _emit(run_id, "step_finished", data, tenant_id=tenant_id, level=level)


def emit_test_finished(run_id: int, test_case_id: int, status: str, *,
                       tenant_id: Optional[int] = None, **kwargs) -> None:
    data = {"test_case_id": test_case_id, "status": status, **kwargs}
    level = "error" if status in ("failed", "error") else "info"
    _emit(run_id, "test_finished", data, tenant_id=tenant_id, level=level)


def emit_run_status(run_id: int, status: str, *, terminal: bool = False,
                    tenant_id: Optional[int] = None, **kwargs) -> None:
    data: Dict[str, Any] = {"status": status, "terminal": terminal, **kwargs}
    level = "error" if status in ("failed",) else "info"
    _emit(run_id, "run_status", data, tenant_id=tenant_id, level=level)


def emit_log(run_id: int, message: str, *, level: str = "info",
             tenant_id: Optional[int] = None, **context) -> None:
    """Free-form worker milestone line for the log panel."""
    data = {"message": message, **context}
    _emit(run_id, "log", data, tenant_id=tenant_id, level=level,
          message=message)

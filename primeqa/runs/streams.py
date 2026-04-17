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


def stream_run_events(run_id: int, snapshot_fn, *,
                      heartbeat_sec: int = SSE_HEARTBEAT_SEC,
                      snapshot_sec: int = SSE_SNAPSHOT_SEC,
                      max_duration_sec: int = SSE_MAX_DURATION_SEC) -> Generator[str, None, None]:
    """Generator producing SSE frames for one run.

    `snapshot_fn()` is a callable returning the latest `{"status", "summary",
    "tests": [...]}` dict from DB; used as a polling fallback.

    The generator exits when:
      - run reaches a terminal status and snapshot emitted
      - connection times out (`max_duration_sec`)
      - `SIGPIPE` / client disconnect (handled by Flask)
    """
    q = BUS.subscribe(run_id)
    try:
        # Send an initial snapshot so the client has state to render immediately
        try:
            snap = snapshot_fn()
            yield _sse_format("run_snapshot", snap)
        except Exception as e:
            log.warning("initial snapshot failed for run=%s: %s", run_id, e)

        started = time.time()
        last_activity = started
        last_snapshot = started
        terminal = False

        while True:
            elapsed = time.time() - started
            if elapsed > max_duration_sec:
                yield _sse_format("stream_ending", {"reason": "max_duration"})
                break

            # Wait up to heartbeat_sec for an event
            try:
                event = q.get(timeout=heartbeat_sec)
                yield _sse_format(event.get("type", "event"), event.get("data") or event)
                last_activity = time.time()
                if event.get("type") == "run_status" and event.get("data", {}).get("terminal"):
                    terminal = True
            except queue.Empty:
                # No bus activity \u2013 either send heartbeat or fall back to snapshot
                now = time.time()
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
                # One last snapshot after terminal so the UI shows final state
                try:
                    yield _sse_format("run_snapshot", snapshot_fn())
                except Exception:
                    pass
                yield _sse_format("stream_ending", {"reason": "terminal"})
                break
    finally:
        BUS.unsubscribe(run_id, q)


# ---- Convenience helpers for executor/worker -------------------------------

def emit_step_started(run_id: int, test_case_id: int, step_order: int, **kwargs) -> None:
    BUS.publish(run_id, {"type": "step_started",
                         "data": {"test_case_id": test_case_id, "step_order": step_order, **kwargs}})


def emit_step_finished(run_id: int, test_case_id: int, step_order: int,
                       status: str, **kwargs) -> None:
    BUS.publish(run_id, {"type": "step_finished",
                         "data": {"test_case_id": test_case_id, "step_order": step_order,
                                  "status": status, **kwargs}})


def emit_test_finished(run_id: int, test_case_id: int, status: str, **kwargs) -> None:
    BUS.publish(run_id, {"type": "test_finished",
                         "data": {"test_case_id": test_case_id, "status": status, **kwargs}})


def emit_run_status(run_id: int, status: str, *, terminal: bool = False, **kwargs) -> None:
    BUS.publish(run_id, {"type": "run_status",
                         "data": {"status": status, "terminal": terminal, **kwargs}})

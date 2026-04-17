"""Lightweight in-process observability: request timing + slow-query log.

Wire via `install(app)` from `primeqa/app.py`. No external dependencies —
every counter lives in memory and is exposed at `/api/_internal/health`.
If the process restarts the counters reset; that's deliberate for now
(full APM is out of scope).
"""

import logging
import time
from collections import deque
from threading import Lock

from flask import Response, g, jsonify, request
from sqlalchemy import event

log = logging.getLogger("primeqa.obs")

SLOW_QUERY_MS = 300
RECENT_WINDOW = 500  # rolling window size for p50/p95


class _Stats:
    def __init__(self) -> None:
        self.lock = Lock()
        self.durations_ms: deque = deque(maxlen=RECENT_WINDOW)
        self.requests_total = 0
        self.errors_total = 0
        self.slow_queries_total = 0

    def record_request(self, duration_ms: float, is_error: bool) -> None:
        with self.lock:
            self.durations_ms.append(duration_ms)
            self.requests_total += 1
            if is_error:
                self.errors_total += 1

    def record_slow_query(self) -> None:
        with self.lock:
            self.slow_queries_total += 1

    def snapshot(self) -> dict:
        with self.lock:
            samples = sorted(self.durations_ms)
            n = len(samples)
            def pctl(p: float) -> float:
                if not samples:
                    return 0.0
                idx = min(n - 1, max(0, int(p * n) - 1))
                return round(samples[idx], 2)
            return {
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "error_rate": round(self.errors_total / self.requests_total, 4)
                              if self.requests_total else 0.0,
                "slow_queries_total": self.slow_queries_total,
                "latency_ms": {
                    "p50": pctl(0.50),
                    "p95": pctl(0.95),
                    "samples": n,
                },
            }


STATS = _Stats()


def install(app) -> None:
    """Attach hooks to a Flask app + SQLAlchemy engine."""

    @app.before_request
    def _start_timer():
        g._obs_start = time.perf_counter()

    @app.after_request
    def _finish_timer(response: Response):
        start = getattr(g, "_obs_start", None)
        if start is None:
            return response
        duration_ms = (time.perf_counter() - start) * 1000.0
        is_error = response.status_code >= 500
        STATS.record_request(duration_ms, is_error)
        response.headers["X-Response-Time-ms"] = f"{duration_ms:.1f}"
        if duration_ms > 1000:
            log.warning("slow_request route=%s status=%s ms=%.1f",
                        request.path, response.status_code, duration_ms)
        return response

    # SQLAlchemy slow query listener — wire lazily so we catch the real engine
    try:
        from primeqa import db as dbmod
        if dbmod.engine is not None:
            _attach_sql_listener(dbmod.engine)
        else:
            # Defer: app.py calls init_db() before register_blueprint; if not,
            # the listener just won't fire.
            log.info("observability: engine not yet initialised; slow-query listener skipped")
    except Exception as e:
        log.warning("observability: SQLAlchemy listener attach failed: %s", e)

    @app.route("/api/_internal/health")
    def _internal_health():
        return jsonify(STATS.snapshot()), 200


def _attach_sql_listener(engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        context._obs_query_start = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        start = getattr(context, "_obs_query_start", None)
        if start is None:
            return
        duration_ms = (time.perf_counter() - start) * 1000.0
        if duration_ms >= SLOW_QUERY_MS:
            STATS.record_slow_query()
            log.warning("slow_query ms=%.1f sql=%s", duration_ms,
                        (statement or "").replace("\n", " ")[:240])

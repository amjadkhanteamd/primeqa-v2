"""The API request log (round 4, part D — AUD-034, the decision memo's ruling).

The audit could not say who calls 30 of the 61 ``/api`` rules because nothing
recorded a request: there is no CI configuration in the repository, no access
log, and Railway keeps the live deployment's output only. Deleting a route
because this repository does not name it would be deciding from absence. So
every ``/api`` request is recorded here — route, declared tier, caller kind,
and count — kept 30 days, and NO route is pruned on this evidence yet.

What a row carries, and nothing else: the time, the tenant and user id and role
(from the token the gate already read), the CALLER KIND (``anonymous`` /
``cookie`` / ``bearer`` / ``webhook``), the method, the url_map RULE (never the
concrete path — an id in a path is data), the endpoint, the status, the
duration, and the rule's declared minimum tier (``primeqa.core.authz_gates``).
Never a body, never a query string, never a header, never a token.

How it writes: the hook appends to a bounded in-process queue and returns; a
daemon thread flushes the queue every ``FLUSH_SECONDS`` as ONE multi-row
INSERT on the public engine, and once more at interpreter exit. A request is
never slowed by the log's write (Railway's ~400 ms round trip would otherwise
be paid on every call) and the log can never break a request: every failure
here is logged and dropped. The cost of that shape, stated: a hard kill loses
the rows of at most one flush window (the disclosed loss), and a queue that
fills (``MAX_QUEUE``) drops the oldest — counted in ``STATS``.
"""
from __future__ import annotations

import atexit
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

from flask import g, request
from sqlalchemy import text

log = logging.getLogger(__name__)

FLUSH_SECONDS = 2.0
MAX_QUEUE = 5000
RETENTION_DAYS = 30
TABLE = "api_request_log"

_INSERT = text(
    f"INSERT INTO {TABLE} (at, tenant_id, user_id, role, caller_kind, method, rule, endpoint, "
    "status, duration_ms, min_tier) VALUES (:at, :tenant_id, :user_id, :role, :caller_kind, "
    ":method, :rule, :endpoint, :status, :duration_ms, :min_tier)")
PRUNE_SQL = text(f"DELETE FROM {TABLE} WHERE at < now() - make_interval(days => :days)")


class _Stats:
    def __init__(self):
        self.queued = 0
        self.written = 0
        self.dropped = 0
        self.flush_failures = 0


STATS = _Stats()
_queue: deque = deque(maxlen=MAX_QUEUE)
_lock = threading.Lock()
_flusher_started = False


def caller_kind() -> str:
    """How the caller presented itself — read from the request, never from a
    token's content."""
    if request.headers.get("X-Signature") or request.headers.get("X-Hub-Signature-256"):
        return "webhook"
    if request.headers.get("Authorization", "").startswith("Bearer "):
        return "bearer"
    if request.cookies.get("access_token"):
        return "cookie"
    return "anonymous"


def record(*, rule: str, method: str, endpoint: str, status: int, duration_ms: float,
           user: dict | None, kind: str, min_tier) -> dict:
    """One row's dict (pure; the queue is fed by ``_hook``)."""
    return {
        "at": datetime.now(timezone.utc),
        "tenant_id": (user or {}).get("tenant_id"),
        "user_id": (user or {}).get("id"),
        "role": (user or {}).get("role"),
        "caller_kind": kind,
        "method": method,
        "rule": rule,
        "endpoint": endpoint,
        "status": int(status),
        "duration_ms": int(duration_ms),
        "min_tier": min_tier,
    }


def enqueue(row: dict) -> None:
    with _lock:
        if len(_queue) == _queue.maxlen:
            STATS.dropped += 1                    # the oldest goes; counted, never silent
        _queue.append(row)
        STATS.queued += 1


def drain() -> list:
    with _lock:
        rows = list(_queue)
        _queue.clear()
    return rows


def flush(engine=None) -> int:
    """Write everything queued as one INSERT. Returns rows written. Never
    raises: a failed flush is logged, counted, and the rows are dropped (a
    retry would re-queue behind live traffic and could double-write)."""
    rows = drain()
    if not rows:
        return 0
    try:
        if engine is None:
            from primeqa import db as dbmod
            engine = dbmod.engine
        if engine is None:
            raise RuntimeError("no public engine")
        with engine.begin() as conn:
            conn.execute(_INSERT, rows)
        STATS.written += len(rows)
        return len(rows)
    except Exception as exc:  # noqa: BLE001 — the log never breaks anything
        STATS.flush_failures += 1
        log.warning("api_request_log flush failed (%d rows dropped): %s", len(rows), exc)
        return 0


def _flusher():
    while True:
        time.sleep(FLUSH_SECONDS)
        flush()


def _start_flusher() -> None:
    global _flusher_started
    if _flusher_started:
        return
    _flusher_started = True
    t = threading.Thread(target=_flusher, name="api-request-log-flusher", daemon=True)
    t.start()
    atexit.register(flush)


def install(app) -> None:
    """Attach the hook. The declared tier of every rule is computed ONCE here
    from the live app (the same walk the audit's oracle does)."""
    from primeqa.core.authz_gates import min_tier_by_rule
    try:
        tiers = min_tier_by_rule(app)
    except Exception as exc:  # noqa: BLE001
        log.warning("api_request_log: tiers unavailable (%s); rows carry min_tier NULL", exc)
        tiers = {}

    @app.before_request
    def _mark_start():
        g._rl_start = time.perf_counter()

    @app.after_request
    def _hook(response):
        try:
            if not request.path.startswith("/api/"):
                return response
            rule = request.url_rule.rule if request.url_rule is not None else "(no rule)"
            start = getattr(g, "_rl_start", None)
            duration = (time.perf_counter() - start) * 1000.0 if start else 0.0
            enqueue(record(
                rule=rule, method=request.method, endpoint=request.endpoint or "(none)",
                status=response.status_code, duration_ms=duration,
                user=getattr(request, "user", None), kind=caller_kind(),
                min_tier=tiers.get((rule, request.method))))
        except Exception as exc:  # noqa: BLE001
            log.warning("api_request_log hook failed: %s", exc)
        return response

    _start_flusher()


def prune(conn, *, days: int = RETENTION_DAYS) -> int:
    """Delete rows older than ``days`` (the scheduler's daily tick). Returns
    the number removed."""
    return conn.execute(PRUNE_SQL, {"days": int(days)}).rowcount or 0


__all__ = ["install", "flush", "prune", "record", "caller_kind", "enqueue", "drain", "STATS",
           "RETENTION_DAYS", "TABLE"]

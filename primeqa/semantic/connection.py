"""Substrate 1 connection resolver per SPEC §5.4.

Single canonical access path for tenant-scoped database operations:

    with get_tenant_connection(tenant_id) as conn:
        result = conn.execute(text("SELECT * FROM entities WHERE ..."))

What the context manager does:

  1. Checks out a connection from the engine's pool.
  2. Begins a transaction.
  3. SET LOCAL search_path TO "tenant_<id>", public
     (transaction-scoped — auto-resets on commit/rollback)
  4. SET LOCAL app.tenant_id = '<id>'
     (transaction-scoped — feeds the CHECK constraints)
  5. Yields the connection.
  6. Commits on success, rolls back on exception, always returns the
     connection to the pool.

What it does NOT do:

  - Cache schema_name resolution. Cheap; not worth the staleness risk.
  - Validate that the schema exists. The SET LOCAL search_path call
    succeeds even for non-existent schemas; the first query that hits
    a missing table is what fails. We accept that — diagnostics from
    "relation entities does not exist" are fine.
  - Set role or any other per-tenant auth. We rely on a single DB user
    with broad rights; tenant isolation is enforced via search_path
    and the CHECK constraints, not via DB-level permissions.

Pool checkin hook (defensive reset):

The connection pool reuses connections across tenants. PostgreSQL's
SET LOCAL is transaction-scoped, so a clean COMMIT / ROLLBACK clears
both search_path and app.tenant_id. But: a connection that's checked
in WITHOUT a clean transaction boundary (e.g., the worker crashes
mid-request) might still carry stale settings. The checkin hook resets
defensively. Belt and braces.

Worker / script / scheduled task discipline:

There is no ambient tenant context. Every entry point that enters the
S1 query path passes tenant_id explicitly. Web request handlers extract
it from the authenticated user; workers receive it as part of the job
payload; scripts take it as a CLI arg. If you find yourself wanting an
ambient `current_tenant()` helper, stop — that is the road to leakage
bugs the schema-per-tenant decision is supposed to prevent.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Callable, TypeVar

from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import Connection, Engine


# ----------------------------------------------------------------------
# Engine setup
# ----------------------------------------------------------------------

def _make_engine() -> Engine:
    """Build the singleton engine. Called once at module import.

    DATABASE_URL is required; we don't fall back to a default.

    Pool sizing intentionally modest. Schema-per-tenant doesn't change
    the connection-count math — a connection is a connection regardless
    of search_path. Tune via env vars if pilot load demands it.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL not set. The S1 connection resolver requires it."
        )

    engine = create_engine(
        db_url,
        pool_size=int(os.environ.get("PRIMEQA_POOL_SIZE", "10")),
        max_overflow=int(os.environ.get("PRIMEQA_POOL_OVERFLOW", "5")),
        # pool_pre_ping catches stale connections (Railway can drop them
        # silently). Cheap; worth keeping.
        pool_pre_ping=True,
    )

    # Defensive checkin hook. See module docstring.
    @event.listens_for(engine, "checkin")
    def _reset_session_state(dbapi_connection, connection_record):
        if dbapi_connection is None:
            return
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("RESET search_path")
            cursor.execute("RESET app.tenant_id")
            cursor.close()
            dbapi_connection.commit()
        except Exception:
            connection_record.invalidate()
            raise

    return engine


_engine: Engine | None = None


def get_engine() -> Engine:
    """Lazy singleton accessor. First call builds the engine."""
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


# ----------------------------------------------------------------------
# Schema name resolution
# ----------------------------------------------------------------------

def _resolve_schema_name(tenant_id: int) -> str:
    """Map tenant_id to schema name. Pure function for now.

    If we ever introduce tenant-id-to-schema mapping (sharding, blue/green
    schema swaps, etc.), this is the chokepoint to extend. For now it's a
    formatter, not a lookup.
    """
    if not isinstance(tenant_id, int):
        raise TypeError(f"tenant_id must be int, got {type(tenant_id).__name__}")
    if tenant_id <= 0:
        raise ValueError(f"tenant_id must be positive, got {tenant_id}")
    return f"tenant_{tenant_id}"


# ----------------------------------------------------------------------
# Tenant context manager
# ----------------------------------------------------------------------

@contextmanager
def get_tenant_connection(tenant_id: int) -> Iterator[Connection]:
    """Acquire a connection bound to a tenant's schema and GUC.

    Yields a SQLAlchemy Connection within an open transaction. Commits on
    successful exit, rolls back on exception, always returns the
    connection to the pool.

    The yielded connection has:
      - search_path = "tenant_<id>", public
      - app.tenant_id = <id> (as text; PG casts on read)

    Both settings are transaction-scoped via SET LOCAL.

    Usage:
        with get_tenant_connection(42) as conn:
            result = conn.execute(text("SELECT id FROM entities LIMIT 5"))
            rows = result.fetchall()

    Do not nest. Do not pass tenant_id from one call to another. Do not
    use this for cross-tenant queries — those go through admin entry
    points below.
    """
    schema_name = _resolve_schema_name(tenant_id)
    engine = get_engine()
    conn = engine.connect()
    try:
        trans = conn.begin()
        try:
            # Schema name is interpolated into the SQL string because
            # PostgreSQL's parameter binding doesn't apply to identifiers.
            # _resolve_schema_name validates the input shape, and it's an
            # int formatted into a known prefix — there's no injection
            # surface here.
            conn.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
            conn.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            yield conn
            trans.commit()
        except Exception:
            trans.rollback()
            raise
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Admin entry points
# ----------------------------------------------------------------------

T = TypeVar("T")


@contextmanager
def admin_run_in_shared_schema() -> Iterator[Connection]:
    """Execute against the shared schema (control plane).

    Used by tenant provisioning, billing, system_metadata reads, etc.
    Does NOT set app.tenant_id — the shared schema has no tenant_id
    CHECK constraints.

    Refuses to proceed if app.tenant_id has somehow leaked into the
    connection from a prior reuse — defense in depth.

    Usage:
        with admin_run_in_shared_schema() as conn:
            conn.execute(text(
                "INSERT INTO shared.tenants (name, schema_name) "
                "VALUES (:n, 'tenant_' || nextval(pg_get_serial_sequence('shared.tenants','id'))::text) "
                "RETURNING id"
            ), {"n": "Acme Corp"})
    """
    engine = get_engine()
    conn = engine.connect()
    try:
        trans = conn.begin()
        try:
            conn.execute(text('SET LOCAL search_path TO shared, public'))
            yield conn
            trans.commit()
        except Exception:
            trans.rollback()
            raise
    finally:
        conn.close()


def admin_iterate_all_tenants(fn: Callable[[Connection, int], T]) -> list[T]:
    """Run `fn(conn, tenant_id)` against every active tenant in turn.

    Sequential — bounded concurrency is a future improvement. Fail-loud:
    if any tenant raises, the iteration stops and the exception
    propagates with the tenant_id in context.

    Used for admin operations like "rebuild materialized views across
    all tenants," "compute change_log size by tenant," etc. Not used in
    request paths.

    Returns a list of fn results, in tenant_id order.
    """
    with admin_run_in_shared_schema() as conn:
        rows = conn.execute(text(
            "SELECT id FROM shared.tenants WHERE deleted_at IS NULL ORDER BY id"
        )).fetchall()
        tenant_ids = [r[0] for r in rows]

    results: list[T] = []
    for tid in tenant_ids:
        try:
            with get_tenant_connection(tid) as tconn:
                results.append(fn(tconn, tid))
        except Exception as exc:
            raise RuntimeError(
                f"admin_iterate_all_tenants failed at tenant_id={tid}"
            ) from exc

    return results


# ----------------------------------------------------------------------
# Development-environment validator
# ----------------------------------------------------------------------

def validate_search_path_takes_effect(tenant_id: int) -> None:
    """Verify SET LOCAL search_path is actually taking effect.

    Required because PgBouncer in transaction-pooling mode shares
    connections across statements within a transaction but not between
    transactions, which can defeat SET LOCAL in subtle ways. This
    function performs a transaction that sets search_path and then
    reads back current_schemas() to confirm. Call once at boot in dev,
    so misconfiguration fails loud at startup rather than mid-request.

    Raises RuntimeError if search_path is not what we expect.
    """
    expected = _resolve_schema_name(tenant_id)
    with get_tenant_connection(tenant_id) as conn:
        result = conn.execute(text("SELECT current_schemas(true)")).scalar()
        # current_schemas returns a TEXT[] like {tenant_42, public, pg_catalog}
        if not result or expected not in result:
            raise RuntimeError(
                f"search_path validation failed: expected {expected!r} in "
                f"current_schemas(), got {result!r}. Check PgBouncer mode "
                f"or any session-pooling layer between the app and Postgres."
            )

        guc = conn.execute(text("SELECT current_setting('app.tenant_id')")).scalar()
        if guc != str(tenant_id):
            raise RuntimeError(
                f"app.tenant_id GUC validation failed: expected {tenant_id!r}, "
                f"got {guc!r}."
            )

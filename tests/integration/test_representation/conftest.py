"""Integration-test fixtures for substrate-2 (Test Representation).

Per Track D-β.2 (Phase 1.5). Substrate-2 introduces a **local-PG
test convention** distinct from substrate-1's prefix-cleanup pattern
against the live Railway DB. Substrate-2's write-flow tests need
real per-test transactional rollback, which requires real DB
transactional isolation — not available in substrate-1's
prefix-based cleanup model.

Local setup:

  1. Install PostgreSQL 16 via Homebrew (already done per project
     working memory):

       brew install postgresql@16

  2. Start the service (one-time):

       LC_ALL=en_US.UTF-8 \\
         /usr/local/opt/postgresql@16/bin/pg_ctl \\
         start -D /usr/local/var/postgresql@16 -l /tmp/pg16.log

  3. The fixtures below create the test database, run the
     migration chain, and provide per-test transactional sessions.

Environment variables:

  SUBSTRATE_2_TEST_DB_URL  — full SQLAlchemy URL. Default:
      ``postgresql://localhost/primeqa_test_substrate2``
  SUBSTRATE_2_KEEP_TEST_DB — if set (any truthy value), the test
      database is preserved across pytest runs. Default: drop at
      session end for clean reruns.

If PostgreSQL is unreachable on the resolved URL, every integration
test in this directory skips with a clear pytest.skip message.
Unit tests under ``tests/unit/test_representation/`` are NOT
affected — they don't import these fixtures.

Why local PG rather than testcontainers or substrate-1's pattern:
substrate-1 cleans up by prefix-matching entity names; substrate-2
has no such name field (rows are UUID-identified) and write-flow
tests need transactional rollback for per-test isolation. Local
PG is the lowest-overhead solution that satisfies both
constraints.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_TEST_DB_URL = "postgresql://localhost/primeqa_test_substrate2"
"""Default URL — empty database, owned by the current OS user via
peer auth (the Homebrew default). Override via
``SUBSTRATE_2_TEST_DB_URL`` for CI or alternate setups."""

TEST_TENANT_ID = 1
"""substrate-2 tables live in tenant schemas (the migration is
under ``alembic/versions/tenant/``). All integration tests target
``tenant_1``. Cross-tenant testing is out of scope at v1."""

_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Test-DB URL fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """The URL of the substrate-2 test database."""
    # An explicit override wins verbatim (CI / operator owns uniqueness there).
    override = os.environ.get("SUBSTRATE_2_TEST_DB_URL")
    if override:
        return override
    # TEST-3 (wave-0): derive a UNIQUE-per-session DB name so two concurrent
    # sessions never share ``primeqa_test_substrate2`` and never
    # pg_terminate_backend + DROP each other's live database. Base kept as a
    # PREFIX so any name-substring safety check still matches.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    return f"{DEFAULT_TEST_DB_URL}_{worker}_{os.getpid()}"


# ---------------------------------------------------------------------------
# Reachability probe — skip cleanly when PG is down
# ---------------------------------------------------------------------------


def _admin_url(test_db_url: str) -> str:
    """Derive an admin URL by pointing at the ``postgres`` database
    rather than the test database. Used for CREATE / DROP DATABASE
    operations (which can't run inside the target DB itself)."""
    # Strip the database-name suffix from the URL and replace with
    # ``/postgres``.
    if "/" in test_db_url.rsplit("@", 1)[-1]:
        prefix, _ = test_db_url.rsplit("/", 1)
    else:
        prefix = test_db_url
    return f"{prefix}/postgres"


def _pg_reachable(admin_url: str) -> tuple[bool, str]:
    """Return ``(True, "")`` if PG is reachable at ``admin_url``,
    else ``(False, error_message)``."""
    try:
        eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True, ""
    except OperationalError as e:
        return False, str(e)
    except Exception as e:  # pragma: no cover - defensive
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Session-scoped DB setup
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def db_setup(test_db_url: str):
    """Ensure the substrate-2 test DB exists and is fully migrated.

    Runs once per pytest session:
      1. Probes PG reachability via the admin URL; skips all
         integration tests if PG is down.
      2. CREATE DATABASE primeqa_test_substrate2 (if absent).
      3. Runs ``alembic`` for the tenant_1 schema against the
         test DB.
      4. Yields control to the test session.
      5. At teardown: optionally drops the database (controlled
         by ``SUBSTRATE_2_KEEP_TEST_DB``).
    """
    admin_url = _admin_url(test_db_url)
    reachable, err = _pg_reachable(admin_url)
    if not reachable:
        pytest.skip(
            f"local PostgreSQL not reachable at {admin_url!r}: {err}. "
            f"Start it via "
            f"`LC_ALL=en_US.UTF-8 pg_ctl start -D <data-dir>` "
            f"or set SUBSTRATE_2_TEST_DB_URL to a reachable server.",
            allow_module_level=True,
        )

    db_name = test_db_url.rsplit("/", 1)[-1]
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"),
            {"n": db_name},
        ).first()
        if not existing:
            # CREATE DATABASE can't take parameters via SQLAlchemy
            # bind — interpolate the validated db_name.
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    # Substrate-1 migrations require the ``vector`` (pgvector) and
    # ``pgcrypto`` extensions. Create them inside the test DB
    # before running migrations.
    target_engine = create_engine(test_db_url, isolation_level="AUTOCOMMIT")
    with target_engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    target_engine.dispose()

    # Apply the migration chain — shared first (control-plane
    # tables required by tenant migrations), then tenant_1.
    _run_alembic_upgrade(test_db_url, mode="shared")
    _run_alembic_upgrade(
        test_db_url, mode="tenant", tenant_id=TEST_TENANT_ID,
    )

    yield

    if not _truthy(os.environ.get("SUBSTRATE_2_KEEP_TEST_DB")):
        # Drop the test DB on session end. Disconnect any open
        # connections first (a stale connection from the engine
        # fixture would block DROP DATABASE).
        admin_engine = create_engine(
            admin_url, isolation_level="AUTOCOMMIT",
        )
        with admin_engine.connect() as conn:
            conn.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ), {"n": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin_engine.dispose()


def _truthy(val: str | None) -> bool:
    return bool(val) and val.lower() not in ("0", "false", "no", "")


def _run_alembic_upgrade(
    db_url: str, *, mode: str, tenant_id: int | None = None,
) -> None:
    """Run ``alembic upgrade head`` against ``db_url`` with the
    given mode. Captures + surfaces stderr on failure for clean
    diagnosis."""
    cmd = ["alembic", "-x", f"mode={mode}"]
    if tenant_id is not None:
        cmd += ["-x", f"tenant_id={tenant_id}"]
    # The migrations are organized into two branches (``shared`` and
    # ``tenant``) — bare ``alembic upgrade head`` is ambiguous. Use
    # the mode as the branch name.
    cmd += ["upgrade", f"{mode}@head"]
    result = subprocess.run(
        cmd,
        cwd=str(_REPO_ROOT),
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Surface BOTH stdout and stderr so multi-stage failures are
        # diagnosable. Alembic writes progress to stdout and errors
        # to stderr.
        raise RuntimeError(
            f"alembic upgrade head failed "
            f"(mode={mode}, tenant_id={tenant_id}):\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Engine fixture (session scope)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def engine(test_db_url: str, db_setup) -> Engine:
    """SQLAlchemy engine for the substrate-2 test database.

    Every new connection automatically sets
    ``search_path = "tenant_1", public`` via a connect event
    listener, so unqualified table names (e.g., ``test_claims``)
    resolve into the tenant_1 schema where Track A's migration
    placed them.

    Session-scoped: one engine per pytest session, reused across
    tests. Per-test isolation comes from the function-scoped
    :func:`session` fixture's transactional rollback.
    """
    eng = create_engine(test_db_url, pool_pre_ping=True)

    @event.listens_for(eng, "connect")
    def _set_search_path(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        # SET search_path (session-level, not LOCAL) so the
        # setting survives across nested transactions on this
        # connection. Commit so psycopg2's implicit transaction
        # doesn't hold the change inside an uncommitted state
        # — SQLAlchemy then opens its own transaction with the
        # session-level setting already in effect.
        cur.execute(f'SET search_path TO "tenant_{TEST_TENANT_ID}", public')
        cur.close()
        dbapi_connection.commit()

    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Session fixture (function scope, transactional rollback)
# ---------------------------------------------------------------------------


@pytest.fixture
def session(engine: Engine):
    """Yields a SQLAlchemy :class:`Session` inside a transaction
    that ROLLS BACK at teardown.

    Pattern: open a connection, begin a transaction, bind a Session
    to that connection. The Session's flush/commit calls operate
    within the outer transaction. At fixture teardown, the outer
    transaction is rolled back, undoing every write the test
    performed.

    This is the cleanest per-test isolation pattern for write-flow
    tests: no manual cleanup, no UUID tracking, no DELETE
    sweeps. The Coordinator's ``write_claim`` calls
    ``session.flush()`` but not ``session.commit()``; the test's
    outer transaction owns commit semantics, and the fixture's
    rollback at teardown is the test's commit-replacement.
    """
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        yield db
    finally:
        db.close()
        # The transaction may have been auto-rolled-back if the
        # test raised a DB-level error (e.g., IntegrityError on
        # the concurrent-collision tests). Guard the rollback so
        # the fixture teardown stays clean.
        if transaction.is_active:
            transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Defensive: ensure the substrate-2 schema is actually populated
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _verify_substrate2_schema(engine, db_setup):
    """Defensive check that runs once per session: verifies the
    six substrate-2 tables are queryable in ``tenant_1``. If the
    migration silently failed to create them, surface a clear
    error rather than letting every test fail on ``relation
    does not exist``."""
    expected = {
        "test_claims",
        "test_recipes",
        "test_provenance",
        "test_claim_coverage",
        "test_recipe_runtime_state",
        "test_requirement_links",
    }
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = :s"
        ), {"s": f"tenant_{TEST_TENANT_ID}"}).fetchall()
    present = {r[0] for r in rows}
    missing = expected - present
    if missing:
        pytest.fail(
            f"substrate-2 tables missing from tenant_{TEST_TENANT_ID} "
            f"after alembic upgrade head: {sorted(missing)}. "
            f"Inspect the migration output."
        )

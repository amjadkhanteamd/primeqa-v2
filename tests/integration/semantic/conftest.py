"""Integration-test fixtures for substrate-1's query interface
(``primeqa/semantic/query.py``).

Mirrors substrate-2's **local-PG** convention (see
``tests/integration/test_representation/conftest.py``), not substrate-1's
prefix-cleanup-against-live-Railway convention: the query-interface tests seed
``logical_versions`` / ``entities`` / ``edges`` rows and need real
transactional rollback for per-test isolation.

Local setup (one-time):

    brew install postgresql@16
    LC_ALL=en_US.UTF-8 \\
      /usr/local/opt/postgresql@16/bin/pg_ctl \\
      start -D /usr/local/var/postgresql@16 -l /tmp/pg16.log

The fixtures create the test database, run the full migration chain (shared +
tenant — the tenant branch includes S1's phase-0/1 foundation tables), and
provide a per-test transactional ``Connection`` already scoped to ``tenant_1``
(``search_path`` + ``app.tenant_id`` via ``SET LOCAL``, exactly as
``get_tenant_connection`` does at runtime) that rolls back at teardown.

Environment variables:

  SEMANTIC_QUERY_TEST_DB_URL  — full SQLAlchemy URL. Default:
      ``postgresql://localhost/primeqa_test_semantic``
  SEMANTIC_QUERY_KEEP_TEST_DB — if truthy, preserve the DB across runs.

If PostgreSQL is unreachable, every test here skips with a clear message.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError


DEFAULT_TEST_DB_URL = "postgresql://localhost/primeqa_test_semantic"
TEST_TENANT_ID = 1
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Test-DB URL + reachability
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_db_url() -> str:
    return os.environ.get("SEMANTIC_QUERY_TEST_DB_URL", DEFAULT_TEST_DB_URL)


def _admin_url(test_db_url: str) -> str:
    """Point at the ``postgres`` maintenance DB for CREATE / DROP DATABASE."""
    if "/" in test_db_url.rsplit("@", 1)[-1]:
        prefix, _ = test_db_url.rsplit("/", 1)
    else:
        prefix = test_db_url
    return f"{prefix}/postgres"


def _pg_reachable(admin_url: str) -> tuple[bool, str]:
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


def _truthy(val: str | None) -> bool:
    return bool(val) and val.lower() not in ("0", "false", "no", "")


def _run_alembic_upgrade(db_url: str, *, mode: str, tenant_id: int | None = None) -> None:
    cmd = ["alembic", "-x", f"mode={mode}"]
    if tenant_id is not None:
        cmd += ["-x", f"tenant_id={tenant_id}"]
    cmd += ["upgrade", f"{mode}@head"]
    result = subprocess.run(
        cmd, cwd=str(_REPO_ROOT),
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade failed (mode={mode}, tenant_id={tenant_id}):\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Session-scoped DB setup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def db_setup(test_db_url: str):
    admin_url = _admin_url(test_db_url)
    reachable, err = _pg_reachable(admin_url)
    if not reachable:
        pytest.skip(
            f"local PostgreSQL not reachable at {admin_url!r}: {err}. "
            f"Start it (e.g. `pg_ctl start -D <data-dir>`) or set "
            f"SEMANTIC_QUERY_TEST_DB_URL to a reachable server.",
            allow_module_level=True,
        )

    db_name = test_db_url.rsplit("/", 1)[-1]
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name},
        ).first()
        if not existing:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    # S1 migrations require pgvector + pgcrypto.
    target_engine = create_engine(test_db_url, isolation_level="AUTOCOMMIT")
    with target_engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    target_engine.dispose()

    _run_alembic_upgrade(test_db_url, mode="shared")
    _run_alembic_upgrade(test_db_url, mode="tenant", tenant_id=TEST_TENANT_ID)

    yield

    if not _truthy(os.environ.get("SEMANTIC_QUERY_KEEP_TEST_DB")):
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ), {"n": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _verify_s1_schema(engine, db_setup):
    """Defensive: confirm the three S1 foundation tables exist in tenant_1."""
    expected = {"entities", "edges", "logical_versions"}
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = :s"
        ), {"s": f"tenant_{TEST_TENANT_ID}"}).fetchall()
    missing = expected - {r[0] for r in rows}
    if missing:
        pytest.fail(
            f"S1 tables missing from tenant_{TEST_TENANT_ID} after alembic "
            f"upgrade: {sorted(missing)}. Inspect the migration output."
        )


# ---------------------------------------------------------------------------
# Engine + per-test transactional connection (scoped + rolled back)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def engine(test_db_url: str, db_setup) -> Engine:
    eng = create_engine(test_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(engine: Engine) -> Connection:
    """A ``tenant_1``-scoped connection inside a transaction that ROLLS BACK at
    teardown. Sets ``search_path`` + ``app.tenant_id`` via ``SET LOCAL`` — the
    same scoping ``get_tenant_connection`` applies at runtime, so the S1 CHECK
    constraints (``tenant_id = current_setting('app.tenant_id')``) are
    satisfied. Every seeded row is undone by the rollback — no cleanup."""
    connection = engine.connect()
    trans = connection.begin()
    connection.execute(text(f'SET LOCAL search_path TO "tenant_{TEST_TENANT_ID}", public'))
    connection.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(TEST_TENANT_ID)})
    try:
        yield connection
    finally:
        if trans.is_active:
            trans.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Seeder — inserts versions / entities / edges within the test transaction
# ---------------------------------------------------------------------------

class _Seeder:
    """Minimal seeding over the test ``conn``. version_name is auto-suffixed
    unique per seeder; tenant_id defaults from the ``app.tenant_id`` GUC."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._suffix = uuid4().hex[:8]
        self._n = 0

    def version(self, name: str | None = None, version_type: str = "manual_checkpoint") -> int:
        self._n += 1
        vname = f"{name or 'v'}_{self._suffix}_{self._n}"
        return int(self._conn.execute(
            text(
                "INSERT INTO logical_versions (version_name, version_type) "
                "VALUES (:n, :t) RETURNING version_seq"
            ),
            {"n": vname, "t": version_type},
        ).scalar())

    def entity(
        self, entity_type: str, sf_api_name: str, valid_from_seq: int,
        valid_to_seq: int | None = None, attributes: dict | None = None,
        sf_id: str | None = None, display_name: str | None = None,
    ) -> UUID:
        rid = self._conn.execute(
            text(
                "INSERT INTO entities "
                "(entity_type, sf_id, sf_api_name, display_name, attributes, "
                " valid_from_seq, valid_to_seq, last_synced_at) "
                "VALUES (:et, :sfid, :api, :dn, CAST(:attrs AS jsonb), "
                " :vf, :vt, NOW()) RETURNING id"
            ),
            {
                "et": entity_type, "sfid": sf_id, "api": sf_api_name,
                "dn": display_name or sf_api_name,
                "attrs": json.dumps(attributes or {}),
                "vf": valid_from_seq, "vt": valid_to_seq,
            },
        ).scalar()
        return _coerce_uuid(rid)

    def edge(
        self, source_id: UUID, target_id: UUID, edge_type: str,
        edge_category: str, valid_from_seq: int, valid_to_seq: int | None = None,
        properties: dict | None = None,
    ) -> UUID:
        rid = self._conn.execute(
            text(
                "INSERT INTO edges "
                "(source_entity_id, target_entity_id, edge_type, edge_category, "
                " properties, valid_from_seq, valid_to_seq) "
                "VALUES (CAST(:s AS uuid), CAST(:t AS uuid), :et, :ec, "
                " CAST(:props AS jsonb), :vf, :vt) RETURNING id"
            ),
            {
                "s": str(source_id), "t": str(target_id), "et": edge_type,
                "ec": edge_category, "props": json.dumps(properties or {}),
                "vf": valid_from_seq, "vt": valid_to_seq,
            },
        ).scalar()
        return _coerce_uuid(rid)


def _coerce_uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


@pytest.fixture
def seed(conn: Connection) -> _Seeder:
    return _Seeder(conn)

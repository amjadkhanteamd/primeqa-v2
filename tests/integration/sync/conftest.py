"""Integration harness for the substrate-1 sync-job queue (D-151, cutover S0.2).

Governance DB only — no Salesforce, no S1 entities. Stands up
``primeqa_test_governance`` (alembic shared + tenant @head, which now includes
``20260604_0010`` / ``20260604_0020``, so ``s1_sync_jobs`` materializes), points
``DATABASE_URL`` at it, and **safety-asserts** it is not the Railway URL. The
``SyncJobStore`` runs the production path (``get_tenant_connection``), so the
binding must resolve to the local test DB.

A trimmed copy of ``tests/integration/generation/conftest.py``'s DB machinery
(the established per-suite governance harness) — minus the S1 seed + the LLM
builders, which the queue tests don't need (``connected_org_id`` is a loose UUID,
no FK). Skips cleanly if PG is unreachable.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

DEFAULT_TEST_DB_URL = "postgresql://localhost/primeqa_test_governance"
TEST_TENANT_ID = 1
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _admin_url(url: str) -> str:
    prefix = url.rsplit("/", 1)[0] if "/" in url.rsplit("@", 1)[-1] else url
    return f"{prefix}/postgres"


def _pg_reachable(admin_url: str) -> tuple[bool, str]:
    try:
        eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        eng.dispose()
        return True, ""
    except OperationalError as e:
        return False, str(e)
    except Exception as e:  # pragma: no cover
        return False, f"{type(e).__name__}: {e}"


def _truthy(v) -> bool:
    return bool(v) and str(v).lower() not in ("0", "false", "no", "")


def _alembic(db_url: str, *, mode: str, tenant_id=None) -> None:
    cmd = ["alembic", "-x", f"mode={mode}"]
    if tenant_id is not None:
        cmd += ["-x", f"tenant_id={tenant_id}"]
    cmd += ["upgrade", f"{mode}@head"]
    r = subprocess.run(cmd, cwd=str(_REPO_ROOT),
                       env={**os.environ, "DATABASE_URL": db_url},
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"alembic {mode} failed:\n{r.stdout}\n{r.stderr}")


@pytest.fixture(scope="session")
def test_db_url() -> str:
    # An explicit override wins verbatim (CI / operator owns uniqueness there).
    override = os.environ.get("GOVERNANCE_TEST_DB_URL")
    if override:
        return override
    # TEST-3 (wave-0): derive a UNIQUE-per-session DB name so two concurrent
    # pytest sessions never share ``primeqa_test_governance`` — the generation
    # suite uses the SAME base name, so without this, one session's teardown
    # (pg_terminate_backend + DROP DATABASE) tears down the other's live DB.
    # The base stays a PREFIX so any name-substring safety check still matches.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    return f"{DEFAULT_TEST_DB_URL}_{worker}_{os.getpid()}"


@pytest.fixture(scope="session", autouse=True)
def db_setup(test_db_url: str):
    admin_url = _admin_url(test_db_url)
    ok, err = _pg_reachable(admin_url)
    if not ok:
        pytest.skip(f"local PostgreSQL not reachable at {admin_url!r}: {err}",
                    allow_module_level=True)

    db_name = test_db_url.rsplit("/", 1)[-1]
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not c.execute(text("SELECT 1 FROM pg_database WHERE datname=:n"),
                         {"n": db_name}).first():
            c.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin.dispose()

    tgt = create_engine(test_db_url, isolation_level="AUTOCOMMIT")
    with tgt.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    tgt.dispose()

    _alembic(test_db_url, mode="shared")
    _alembic(test_db_url, mode="tenant", tenant_id=TEST_TENANT_ID)

    # Point get_tenant_connection (production path) at the local test DB and
    # reset the cached engine so it rebinds. SAFETY: never the Railway URL.
    os.environ["DATABASE_URL"] = test_db_url
    from primeqa.semantic import connection as _conn
    _conn._engine = None
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as c:
        eff = str(c.engine.url)
    assert "primeqa_test_governance" in eff or test_db_url.rsplit("/", 1)[-1] in eff, \
        f"SAFETY ABORT: get_tenant_connection bound to {eff!r}, not the local test DB"

    yield

    if not _truthy(os.environ.get("GOVERNANCE_KEEP_TEST_DB")):
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                           "WHERE datname=:n AND pid<>pg_backend_pid()"), {"n": db_name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()


@pytest.fixture(autouse=True)
def _rebind_production_engine(db_setup, test_db_url):
    """Re-assert the production DB binding before EVERY test — robust to
    cross-suite co-running (mirrors the generation suite's guard). No-op when the
    binding is already correct."""
    from primeqa.semantic import connection as _conn
    os.environ["DATABASE_URL"] = test_db_url
    want_db = test_db_url.rsplit("/", 1)[-1]
    if _conn._engine is not None and _conn._engine.url.database != want_db:
        _conn._engine.dispose()
        _conn._engine = None
    yield


@pytest.fixture(autouse=True)
def clean_jobs(db_setup):
    """Truncate the sync-job queue before each test (no S1 seed to preserve —
    ``connected_org_id`` is a loose UUID)."""
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text("DELETE FROM s1_sync_jobs"))
    yield


@pytest.fixture
def store():
    """A ``SyncJobStore`` bound to the governance tenant."""
    from primeqa.sync.jobs import SyncJobStore
    return SyncJobStore(TEST_TENANT_ID)

"""Governance-DB integration harness for the S4 execution-engine (D-130).

Re-uses the generation conftest's session-scoped ``db_setup`` — it creates the
local test DB and runs ``alembic upgrade tenant@head`` (which applies the
``s4_execution_jobs`` migration) and **skips at module level when local
PostgreSQL is unreachable**, so these tests are DB-gated exactly like the S3 jobs
tests. ``clean_jobs`` truncates the S4 queue before each test.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.generation.conftest import (  # noqa: F401  (fixtures reused)
    TEST_TENANT_ID,
    db_setup,
    test_db_url,
)


@pytest.fixture(autouse=True)
def clean_jobs(db_setup):
    """Truncate the S4 execution-job queue before each test (the S1 seed + the
    rest of the schema persist; only the queue is reset)."""
    from primeqa.semantic.connection import get_tenant_connection

    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text("DELETE FROM s4_execution_job_attempts"))
        conn.execute(text("DELETE FROM s4_execution_jobs"))
    yield

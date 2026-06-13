"""Integration harness for ``primeqa/intelligence`` (D-232 quarantine, …).

Re-uses the generation conftest's session-scoped ``db_setup`` — it creates the
local test DB, runs ``alembic upgrade tenant@head`` (so the ``claim_quarantine``
migration applies), points ``DATABASE_URL`` at it, and skips at module level when
local PostgreSQL is unreachable. Own-connection stores (the QuarantineStore) use
``get_tenant_connection`` exactly as the execution-engine integration tests do.
"""
from tests.integration.generation.conftest import (  # noqa: F401  (fixtures reused)
    TEST_TENANT_ID,
    db_setup,
    test_db_url,
)

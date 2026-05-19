"""Smoke test for the substrate-2 integration test fixtures.

Verifies the conftest's local-PG setup works: engine connects,
search_path is set to tenant_1, the six substrate-2 tables are
queryable, and per-test transactional rollback isolates writes.
"""
from __future__ import annotations

from sqlalchemy import text


def test_engine_connects(engine) -> None:
    """The session-scoped engine connects to local PG."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
        assert result == 1


def test_search_path_is_tenant_1(engine) -> None:
    """The connect-event listener sets search_path so
    unqualified table names resolve into tenant_1."""
    with engine.connect() as conn:
        rows = conn.execute(text("SHOW search_path")).fetchall()
        # search_path is a comma-separated list as a single string.
        path = rows[0][0]
        assert "tenant_1" in path


def test_substrate_2_tables_present_in_tenant_1(engine) -> None:
    """All six substrate-2 tables created by Track A migration are
    present in tenant_1 after alembic upgrade head."""
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
            "SELECT tablename FROM pg_tables WHERE schemaname = 'tenant_1'"
        )).fetchall()
    present = {r[0] for r in rows}
    assert expected.issubset(present), (
        f"missing: {expected - present}"
    )


def test_session_rollback_isolation(session) -> None:
    """A row inserted in this test's session should NOT be
    visible in subsequent tests (transactional rollback)."""
    from uuid import uuid4

    eid = uuid4()
    session.execute(text(
        "INSERT INTO test_requirement_links "
        "(test_id, external_system, external_key, link_kind, "
        " linked_at, linked_by) "
        "VALUES (:t, 'jira', :k, 'generated_from', NOW(), 'smoke')"
    ), {"t": str(eid), "k": "SMOKE-1"})
    session.flush()
    rows = session.execute(text(
        "SELECT external_key FROM test_requirement_links "
        "WHERE test_id = :t"
    ), {"t": str(eid)}).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "SMOKE-1"


def test_session_rollback_isolation_part_2(engine) -> None:
    """The SMOKE-1 row inserted in
    :func:`test_session_rollback_isolation` should NOT be visible
    here — the transaction in that test was rolled back at
    teardown."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT external_key FROM test_requirement_links "
            "WHERE external_key = 'SMOKE-1'"
        )).fetchall()
    assert rows == [], (
        f"transactional rollback failed: SMOKE-1 row leaked across tests"
    )

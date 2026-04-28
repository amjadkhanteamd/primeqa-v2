"""Top-level conftest for primeqa-v2 test suite.

Fixtures here are available to all tests. Integration-test-specific
fixtures (DB connections, tenant isolation) live in
tests/integration/conftest.py to keep unit tests fast and dependency-free.
"""
import pytest


@pytest.fixture
def sample_uuid():
    """A stable UUID for use in pure-function tests."""
    from uuid import UUID
    return UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def sample_uuids():
    """A small pool of stable UUIDs for tests that need multiple."""
    from uuid import UUID
    return [
        UUID(f"{i:08x}-1111-1111-1111-111111111111") for i in range(1, 11)
    ]

"""Concurrent-write collision tests for ``write_claim``.

Per Track D-β.2 + SPEC §7.7. The substrate's compound PK
``(test_id, version_seq)`` on ``test_claims`` is the concurrency
guard: two callers attempting to add version N+1 to the same
test_id will collide on the PK; whichever transaction commits
second receives :class:`IntegrityError`.

Track D-β.2 doesn't add retry logic — the Coordinator's docstring
documents this as the caller's responsibility. These tests
confirm the collision behavior is observable so callers (and
operations like S3's regeneration loop) can detect and retry.
"""
from __future__ import annotations

import uuid as _u

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from primeqa.test_representation import (
    SemanticTransactionCoordinator,
    TestClaim,
)

from ._builders import (
    empty_conditions,
    make_value_claim,
)


def test_compound_pk_violation_on_duplicate_version_seq(session) -> None:
    """Simulate a concurrent race: v1 is in place; an external
    process pre-inserts v2 directly (simulating a parallel
    writer); the Coordinator's attempt to write v2 collides on
    the PK and raises :class:`IntegrityError`."""
    coord = SemanticTransactionCoordinator()
    body_v1 = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v1, semantic_conditions=empty_conditions(),
    )

    # Simulate a parallel writer: directly insert a (closed)
    # v2 row via the ORM. We deliberately leave v1's valid_to
    # as NULL so the Coordinator's prior lookup still returns
    # v1. The parallel row occupies version_seq=2 but has a
    # non-NULL valid_to so it's excluded from the prior-version
    # query. When the Coordinator then tries to INSERT its own
    # v2 (computing new_version_seq from prior=v1), it collides
    # with the parallel row on the compound PK.
    from datetime import datetime, timezone
    parallel_v2 = TestClaim(
        test_id=r1.test_id,
        version_seq=2,
        valid_to=datetime.now(timezone.utc),  # closed row
        archetype="data_behavior",
        claim_kind="value-claim",
        asserted_truth={
            "body_schema_version": 1,
            "kind": "value-claim",
        },
        semantic_conditions={
            "body_schema_version": 1,
            "kind": "conditions",
            "conditions": [],
        },
        identity_hash="parallel-writer-hash",
        identity_hash_version=1,
        status="draft",
    )
    session.add(parallel_v2)
    session.flush()

    # Now the Coordinator's attempt to write v2 hits the PK
    # collision because both v2 rows have version_seq=2. The
    # Coordinator's prior-version lookup re-reads v1 closed
    # status, but its INSERT for version_seq=prior.version_seq+1
    # (=2) collides with the parallel writer's row.
    body_v2 = make_value_claim(subject=body_v1.subject, value="Finance")
    with pytest.raises(IntegrityError):
        coord.write_claim(
            session,
            actor="human", test_id=r1.test_id,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=body_v2,
            semantic_conditions=empty_conditions(),
        )


def test_concurrent_collision_documents_retry_responsibility(
    session,
) -> None:
    """Document the contract: IntegrityError is the substrate's
    explicit signal for concurrent-collision. Callers retry
    (typically: re-read current version, recompute hash, decide
    whether to proceed) per SPEC §7.7.

    This is a documentation test — the assertion is structural,
    confirming the Coordinator's docstring mentions
    concurrent-write semantics so future maintainers find the
    contract."""
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    doc = SemanticTransactionCoordinator.write_claim.__doc__ or ""
    assert "Concurrent-write" in doc or "concurrent" in doc.lower()
    assert "IntegrityError" in doc or "compound primary key" in doc.lower()

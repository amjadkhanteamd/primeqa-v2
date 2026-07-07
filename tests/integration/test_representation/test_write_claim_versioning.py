"""Versioning + supersession integration tests for ``write_claim``.

Per Track D-β.2 + D-057 + SPEC §6.1 + §6.5. Verifies:
  - Sequential versions: v1 closed (valid_to set) when v2 arrives;
    v2 inserted with valid_to=NULL.
  - Coverage rederivation: prior coverage rows deleted, fresh
    coverage inserted at each write per SPEC §6.5.
  - Multi-version chains (v1 → v2 → v3) close all prior versions.
  - Hash-preserving operational evolution (S8): version_seq
    advances but identity_hash stays the same and status is
    preserved.
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.test_representation import (
    SemanticTransactionCoordinator,
    TestClaim,
    TestClaimCoverage,
    TestProvenance,
)

from ._builders import (
    empty_conditions,
    ib,
    make_value_claim,
)


# ---------------------------------------------------------------------------
# Two-version sequence
# ---------------------------------------------------------------------------


def test_v2_supersedes_v1(session) -> None:
    coord = SemanticTransactionCoordinator()
    body_v1 = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v1, semantic_conditions=empty_conditions(),
    )
    body_v2 = make_value_claim(subject=body_v1.subject, value="Finance")
    r2 = coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )

    # Two rows for this test_id; only the v2 row is current.
    rows = session.query(TestClaim).filter(
        TestClaim.test_id == r1.test_id,
    ).order_by(TestClaim.version_seq).all()
    assert [r.version_seq for r in rows] == [1, 2]
    assert rows[0].valid_to is not None  # v1 closed
    assert rows[1].valid_to is None       # v2 current
    assert r2.version_seq == 2
    assert r2.identity_hash != r1.identity_hash


def test_coverage_rederived_on_v2(session) -> None:
    """When v2 is written with a DIFFERENT subject, v1's coverage
    row is deleted and v2's coverage row is inserted. Coverage
    is current-only per SPEC §6.5."""
    coord = SemanticTransactionCoordinator()
    body_v1 = make_value_claim(value="Tech")  # subject1
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v1, semantic_conditions=empty_conditions(),
    )
    body_v2 = make_value_claim(subject=ib(external_id="X.Y"), value="Tech")
    # Different subject_id (fresh UUID) → coverage diverges.
    r2 = coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )

    # Only one coverage row (the new subject) is present.
    coverage = session.query(TestClaimCoverage).filter(
        TestClaimCoverage.claim_test_id == r1.test_id,
    ).all()
    assert len(coverage) == 1
    assert coverage[0].entity_id == body_v2.subject.entity_id


def test_two_provenance_events_for_two_versions(session) -> None:
    coord = SemanticTransactionCoordinator()
    body_v1 = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v1, semantic_conditions=empty_conditions(),
    )
    body_v2 = make_value_claim(subject=body_v1.subject, value="Finance")
    coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    events = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == r1.test_id,
    ).order_by(TestProvenance.event_at,
               TestProvenance.event_data["new_version_seq"].as_integer()).all()
    assert [e.event_kind for e in events] == [
        "claim_created", "claim_edited",
    ]


# ---------------------------------------------------------------------------
# Three-version sequence
# ---------------------------------------------------------------------------


def test_three_version_chain_closes_all_prior(session) -> None:
    coord = SemanticTransactionCoordinator()
    body_v1 = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v1, semantic_conditions=empty_conditions(),
    )
    body_v2 = make_value_claim(subject=body_v1.subject, value="Finance")
    coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    body_v3 = make_value_claim(subject=body_v1.subject, value="Energy")
    r3 = coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v3, semantic_conditions=empty_conditions(),
    )

    rows = session.query(TestClaim).filter(
        TestClaim.test_id == r1.test_id,
    ).order_by(TestClaim.version_seq).all()
    assert [r.version_seq for r in rows] == [1, 2, 3]
    # Only v3 carries valid_to=NULL.
    assert sum(1 for r in rows if r.valid_to is None) == 1
    assert rows[-1].valid_to is None
    assert r3.version_seq == 3


# ---------------------------------------------------------------------------
# S8 hash-preserving operational evolution
# ---------------------------------------------------------------------------


def test_s8_hash_preserving_creates_v2_with_same_hash_preserved_status(
    session,
) -> None:
    """S8 writes a new version with the same body → same hash.
    Approved status carried forward (no re-approval needed)."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    # Manually approve v1.
    session.execute(text(
        "UPDATE test_claims SET status = 'approved' "
        "WHERE test_id = :t AND version_seq = 1"
    ), {"t": str(r1.test_id)})
    session.flush()

    # S8 writes v2 with the SAME body. Same hash, version_seq
    # advances, status preserved.
    r2 = coord.write_claim(
        session,
        actor="s8", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert r2.version_seq == 2
    assert r2.identity_hash == r1.identity_hash
    assert r2.status == "approved"

    # v1 closed; v2 current.
    rows = session.query(TestClaim).filter(
        TestClaim.test_id == r1.test_id,
    ).order_by(TestClaim.version_seq).all()
    assert rows[0].valid_to is not None
    assert rows[1].valid_to is None
    assert rows[0].identity_hash == rows[1].identity_hash

"""Authority-matrix integration tests for ``write_claim``.

Per Track D-β.2 + D-061 + SPEC §7.4 + §7.7. Parallel of the unit
matrix in ``test_authority.py`` but executes against a real DB
so the no-op path's "no new row + no provenance event" claim is
verified at the storage layer.

Full matrix: {human, s3, s8} × {no_prior, prior + hash_unchanged,
prior + hash_changed}.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    AuthorityViolationError,
    SemanticTransactionCoordinator,
    TestClaim,
    TestProvenance,
)

from ._builders import (
    empty_conditions,
    make_value_claim,
)


def _row_count(session, table: str) -> int:
    return session.execute(
        text(f"SELECT COUNT(*) FROM {table}"),
    ).scalar()


def _write_v1(session, actor: str = "human"):
    """Helper: write an initial claim version. Returns the
    result + the original body so callers can compose v2."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim(value="Tech")
    result = coord.write_claim(
        session,
        actor=actor, test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    return result, body


# ---------------------------------------------------------------------------
# No-prior path: every actor permitted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["human", "s3", "s8"])
def test_no_prior_each_actor_creates_draft(session, actor: str) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    result = coord.write_claim(
        session,
        actor=actor, test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert result.version_seq == 1
    assert result.status == "draft"
    assert result.was_noop is False


# ---------------------------------------------------------------------------
# Human + prior cases
# ---------------------------------------------------------------------------


def test_human_hash_unchanged_preserves_status(session) -> None:
    """Human re-writes a claim with hash unchanged → status
    preserved. The Approve flow lands in D-γ; for now drive it
    by manually setting status before the re-write."""
    coord = SemanticTransactionCoordinator()
    result1, body = _write_v1(session, actor="human")
    # Manually approve v1 (D-γ adds promote(); for now SQL).
    session.execute(text(
        "UPDATE test_claims SET status = 'approved' "
        "WHERE test_id = :t AND version_seq = 1"
    ), {"t": str(result1.test_id)})
    session.flush()
    # Re-write same content → hash unchanged.
    result2 = coord.write_claim(
        session,
        actor="human", test_id=result1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert result2.version_seq == 2
    assert result2.status == "approved"  # preserved!


def test_human_hash_changed_resets_to_draft(session) -> None:
    coord = SemanticTransactionCoordinator()
    result1, body = _write_v1(session, actor="human")
    session.execute(text(
        "UPDATE test_claims SET status = 'approved' "
        "WHERE test_id = :t AND version_seq = 1"
    ), {"t": str(result1.test_id)})
    session.flush()
    # Same subject, different value → hash changes.
    new_body = make_value_claim(subject=body.subject, value="Finance")
    result2 = coord.write_claim(
        session,
        actor="human", test_id=result1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=new_body, semantic_conditions=empty_conditions(),
    )
    assert result2.version_seq == 2
    assert result2.status == "draft"  # reset per D-059 §6.3.9 Rule 2
    assert result2.identity_hash != result1.identity_hash


# ---------------------------------------------------------------------------
# S3 + prior cases — including the no-op SPEC §7.7 case
# ---------------------------------------------------------------------------


def test_s3_hash_unchanged_is_noop(session) -> None:
    """The SPEC §7.7 no-op case: S3 regenerates with the same
    hash. No new row; no provenance event; existing version
    metadata returned."""
    coord = SemanticTransactionCoordinator()
    result1, body = _write_v1(session, actor="human")
    claim_rows_before = _row_count(session, "test_claims")
    provenance_before = _row_count(session, "test_provenance")

    result2 = coord.write_claim(
        session,
        actor="s3", test_id=result1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body,  # same body → same hash
        semantic_conditions=empty_conditions(),
    )

    assert result2.was_noop is True
    assert result2.test_id == result1.test_id
    assert result2.version_seq == result1.version_seq  # 1
    assert result2.identity_hash == result1.identity_hash
    assert result2.status == result1.status  # "draft" preserved

    # No new rows.
    assert _row_count(session, "test_claims") == claim_rows_before
    assert _row_count(session, "test_provenance") == provenance_before


def test_s3_hash_changed_creates_claim_regenerated_event(session) -> None:
    coord = SemanticTransactionCoordinator()
    result1, body = _write_v1(session, actor="human")
    new_body = make_value_claim(subject=body.subject, value="Finance")
    result2 = coord.write_claim(
        session,
        actor="s3", test_id=result1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=new_body, semantic_conditions=empty_conditions(),
    )
    assert result2.version_seq == 2
    assert result2.status == "draft"
    events = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == result1.test_id,
    ).order_by(TestProvenance.event_at,
               TestProvenance.event_data["new_version_seq"].as_integer()).all()
    # v1 created + v2 regenerated.
    assert [e.event_kind for e in events] == [
        "claim_created", "claim_regenerated",
    ]


# ---------------------------------------------------------------------------
# S8 + prior cases — the operational vs semantic distinction
# ---------------------------------------------------------------------------


def test_s8_hash_unchanged_preserves_status(session) -> None:
    """S8's authorized mutation path: hash-preserving operational
    evolution. Status preserved (no re-approval needed)."""
    coord = SemanticTransactionCoordinator()
    result1, body = _write_v1(session, actor="human")
    session.execute(text(
        "UPDATE test_claims SET status = 'approved' "
        "WHERE test_id = :t AND version_seq = 1"
    ), {"t": str(result1.test_id)})
    session.flush()
    # Same body → same hash. S8 writes a new version (e.g., for
    # operational metadata refresh — not modeled at v1; the
    # claim itself doesn't change semantically).
    result2 = coord.write_claim(
        session,
        actor="s8", test_id=result1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert result2.version_seq == 2
    assert result2.status == "approved"  # PRESERVED!


def test_s8_hash_changed_REJECTED_no_db_writes(session) -> None:
    """The core governance check: S8 cannot autonomously diverge
    a claim's semantics. The write fails with no DB writes."""
    coord = SemanticTransactionCoordinator()
    result1, body = _write_v1(session, actor="human")
    claim_rows_before = _row_count(session, "test_claims")
    provenance_before = _row_count(session, "test_provenance")
    coverage_before = _row_count(session, "test_claim_coverage")

    new_body = make_value_claim(subject=body.subject, value="Finance")
    with pytest.raises(AuthorityViolationError):
        coord.write_claim(
            session,
            actor="s8", test_id=result1.test_id,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=new_body,
            semantic_conditions=empty_conditions(),
        )

    # Nothing changed in the DB.
    assert _row_count(session, "test_claims") == claim_rows_before
    assert _row_count(session, "test_provenance") == provenance_before
    assert _row_count(session, "test_claim_coverage") == coverage_before

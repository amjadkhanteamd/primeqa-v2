"""Tests for ``promote_claim_to_approved``.

Per Track D-ε + SPEC §6.6 + §7. Covers:
  - Authority: humans only; s3 / s8 / s4 rejected.
  - Status transitions: draft → approved, deprecated → approved
    (un-deprecation), approved → approved (no-op).
  - In-place mutation: no ``version_seq`` bump.
  - Version specificity: a promote on v1 doesn't affect v2's
    status.
  - Provenance event ``claim_approved`` emitted with the
    expected ``event_data``.
"""
from __future__ import annotations

import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    AuthorityViolationError,
    SemanticTransactionCoordinator,
    TestClaim,
    TestProvenance,
)

from ._builders import empty_conditions, make_value_claim
from ._fixtures import update_claim_status


def _write_draft_claim(session, coord, value: str = "Tech"):
    body = make_value_claim(value=value)
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    return r.test_id, r.version_seq, body


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_draft_to_approved(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq, _ = _write_draft_claim(session, coord)
    result = coord.promote_claim_to_approved(
        session, actor="human", test_id=test_id, version_seq=version_seq,
    )
    assert result.status == "approved"
    assert result.version_seq == version_seq
    assert result.test_id == test_id


def test_promote_emits_provenance_event(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq, _ = _write_draft_claim(session, coord)
    coord.promote_claim_to_approved(
        session, actor="human", test_id=test_id, version_seq=version_seq,
    )
    events = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == test_id,
        TestProvenance.event_kind == "claim_approved",
    ).all()
    assert len(events) == 1
    evt = events[0]
    assert evt.event_actor == "human"
    assert evt.event_data["prior_status"] == "draft"
    assert evt.event_data["new_status"] == "approved"
    assert evt.event_data["version_seq"] == version_seq


def test_deprecated_to_approved_undeprecation(session) -> None:
    """Un-deprecation is allowed; the audit trail keeps both
    events visible."""
    coord = SemanticTransactionCoordinator()
    test_id, version_seq, _ = _write_draft_claim(session, coord)
    update_claim_status(
        session, test_id=test_id, version_seq=version_seq,
        new_status="deprecated",
    )
    result = coord.promote_claim_to_approved(
        session, actor="human", test_id=test_id, version_seq=version_seq,
    )
    assert result.status == "approved"


# ---------------------------------------------------------------------------
# No-op
# ---------------------------------------------------------------------------


def test_already_approved_is_noop(session) -> None:
    """No new provenance event; ``updated_at`` unchanged."""
    coord = SemanticTransactionCoordinator()
    test_id, version_seq, _ = _write_draft_claim(session, coord)
    first = coord.promote_claim_to_approved(
        session, actor="human", test_id=test_id, version_seq=version_seq,
    )
    time.sleep(0.05)  # give DB clock a chance to advance
    second = coord.promote_claim_to_approved(
        session, actor="human", test_id=test_id, version_seq=version_seq,
    )
    assert second.status == "approved"
    assert second.updated_at == first.updated_at
    events = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == test_id,
        TestProvenance.event_kind == "claim_approved",
    ).all()
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["s3", "s8", "s4"])
def test_non_human_actors_rejected(session, actor: str) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq, _ = _write_draft_claim(session, coord)
    with pytest.raises(AuthorityViolationError) as exc_info:
        coord.promote_claim_to_approved(
            session, actor=actor,  # type: ignore[arg-type]
            test_id=test_id, version_seq=version_seq,
        )
    assert "human" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Missing target
# ---------------------------------------------------------------------------


def test_nonexistent_test_id_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    with pytest.raises(ValueError):
        coord.promote_claim_to_approved(
            session, actor="human",
            test_id=uuid4(), version_seq=1,
        )


def test_nonexistent_version_seq_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _, _ = _write_draft_claim(session, coord)
    with pytest.raises(ValueError):
        coord.promote_claim_to_approved(
            session, actor="human",
            test_id=test_id, version_seq=99,
        )


# ---------------------------------------------------------------------------
# In-place mutation + version specificity
# ---------------------------------------------------------------------------


def test_promote_does_not_bump_version_seq(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq, _ = _write_draft_claim(session, coord)
    coord.promote_claim_to_approved(
        session, actor="human", test_id=test_id, version_seq=version_seq,
    )
    rows = session.query(TestClaim).filter(
        TestClaim.test_id == test_id,
    ).all()
    assert len(rows) == 1
    assert rows[0].version_seq == version_seq
    assert rows[0].status == "approved"


def test_promote_v1_does_not_affect_v2(session) -> None:
    """Write v1 (draft), v2 (draft); promote v1 → v1 approved,
    v2 unchanged."""
    coord = SemanticTransactionCoordinator()
    test_id, _, body_v1 = _write_draft_claim(session, coord, value="Tech")
    body_v2 = make_value_claim(subject=body_v1.subject, value="Finance")
    coord.write_claim(
        session,
        actor="human", test_id=test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    coord.promote_claim_to_approved(
        session, actor="human", test_id=test_id, version_seq=1,
    )
    v1 = coord.get_claim_version(session, test_id, 1)
    v2 = coord.get_claim_version(session, test_id, 2)
    assert v1.status == "approved"
    assert v2.status == "draft"

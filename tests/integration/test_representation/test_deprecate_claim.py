"""Tests for ``deprecate_claim``.

Per Track D-ε + SPEC §6.6. Covers:
  - Authority: humans only; s3 / s8 / s4 rejected.
  - Status transitions: draft → deprecated, approved →
    deprecated, deprecated → deprecated (no-op).
  - ``reason`` required (non-empty).
  - Reason captured in provenance ``event_data``, NOT on the
    test_claims row.
  - In-place mutation; ``version_seq`` unchanged.
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


def _write_draft_claim(session, coord):
    body = make_value_claim()
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    return r.test_id, r.version_seq


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_draft_to_deprecated(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq = _write_draft_claim(session, coord)
    result = coord.deprecate_claim(
        session, actor="human",
        test_id=test_id, version_seq=version_seq,
        reason="superseded by SR-1234",
    )
    assert result.status == "deprecated"
    assert result.version_seq == version_seq


def test_approved_to_deprecated(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq = _write_draft_claim(session, coord)
    coord.promote_claim_to_approved(
        session, actor="human",
        test_id=test_id, version_seq=version_seq,
    )
    result = coord.deprecate_claim(
        session, actor="human",
        test_id=test_id, version_seq=version_seq,
        reason="business need ended",
    )
    assert result.status == "deprecated"


def test_deprecation_emits_provenance_with_reason(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq = _write_draft_claim(session, coord)
    coord.deprecate_claim(
        session, actor="human",
        test_id=test_id, version_seq=version_seq,
        reason="obsolete after migration",
    )
    events = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == test_id,
        TestProvenance.event_kind == "claim_deprecated",
    ).all()
    assert len(events) == 1
    evt = events[0]
    assert evt.event_data["reason"] == "obsolete after migration"
    assert evt.event_data["new_status"] == "deprecated"
    assert evt.event_data["prior_status"] == "draft"


def test_reason_in_provenance_not_on_test_claims_row(session) -> None:
    """Per D-ε-5: reason lives in ``test_provenance.event_data``,
    NOT on the ``test_claims`` row. Queries against
    ``test_claims`` won't surface the reason; audit tooling
    reads provenance for it."""
    coord = SemanticTransactionCoordinator()
    test_id, version_seq = _write_draft_claim(session, coord)
    coord.deprecate_claim(
        session, actor="human",
        test_id=test_id, version_seq=version_seq,
        reason="X-rationale",
    )
    # Confirm: no column on test_claims carries the reason.
    row = session.query(TestClaim).filter(
        TestClaim.test_id == test_id,
        TestClaim.version_seq == version_seq,
    ).one()
    # __dict__ doesn't include SQLAlchemy internals via vars(); use
    # the table's column names instead.
    col_names = [c.name for c in row.__table__.columns]
    assert "reason" not in col_names
    # The reason IS in provenance.
    evt = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == test_id,
        TestProvenance.event_kind == "claim_deprecated",
    ).one()
    assert evt.event_data["reason"] == "X-rationale"


# ---------------------------------------------------------------------------
# No-op
# ---------------------------------------------------------------------------


def test_already_deprecated_is_noop(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq = _write_draft_claim(session, coord)
    first = coord.deprecate_claim(
        session, actor="human",
        test_id=test_id, version_seq=version_seq,
        reason="first",
    )
    time.sleep(0.05)
    second = coord.deprecate_claim(
        session, actor="human",
        test_id=test_id, version_seq=version_seq,
        reason="second",  # different reason — still no-op
    )
    assert second.status == "deprecated"
    assert second.updated_at == first.updated_at
    events = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == test_id,
        TestProvenance.event_kind == "claim_deprecated",
    ).all()
    assert len(events) == 1  # only the first deprecation


# ---------------------------------------------------------------------------
# Reason validation
# ---------------------------------------------------------------------------


def test_empty_reason_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq = _write_draft_claim(session, coord)
    with pytest.raises(ValueError):
        coord.deprecate_claim(
            session, actor="human",
            test_id=test_id, version_seq=version_seq,
            reason="",
        )


def test_whitespace_only_reason_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq = _write_draft_claim(session, coord)
    with pytest.raises(ValueError):
        coord.deprecate_claim(
            session, actor="human",
            test_id=test_id, version_seq=version_seq,
            reason="   \n  ",
        )


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["s3", "s8", "s4"])
def test_non_human_actors_rejected(session, actor: str) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, version_seq = _write_draft_claim(session, coord)
    with pytest.raises(AuthorityViolationError):
        coord.deprecate_claim(
            session, actor=actor,  # type: ignore[arg-type]
            test_id=test_id, version_seq=version_seq,
            reason="why",
        )


# ---------------------------------------------------------------------------
# Missing target
# ---------------------------------------------------------------------------


def test_nonexistent_claim_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    with pytest.raises(ValueError):
        coord.deprecate_claim(
            session, actor="human",
            test_id=uuid4(), version_seq=1,
            reason="x",
        )

"""Tests for ``unlink_requirement``.

Per Track D-δ + SPEC §9. Covers:
  - Happy path: link then unlink; returns the deleted row's
    snapshot.
  - Idempotency on missing: ``None`` return when no matching
    link exists.
  - Targeted unlink: removing one link leaves others
    untouched.
  - Authority: ``actor='s4'`` rejected; others permitted.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from primeqa.test_representation import (
    AuthorityViolationError,
    RequirementLinkResult,
    SemanticTransactionCoordinator,
    TestRequirementLink,
)

from ._builders import empty_conditions, make_value_claim


def _write_a_test(coord, session):
    body = make_value_claim()
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    return r.test_id


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_link_then_unlink_returns_snapshot(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id = _write_a_test(coord, session)
    linked = coord.link_requirement(
        session, actor="s3", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from", external_version="v1",
    )
    result = coord.unlink_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    assert isinstance(result, RequirementLinkResult)
    assert result.test_id == test_id
    assert result.external_version == "v1"
    assert result.linked_by == linked.linked_by


def test_unlink_actually_deletes_row(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id = _write_a_test(coord, session)
    coord.link_requirement(
        session, actor="s3", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    coord.unlink_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    assert session.query(TestRequirementLink).filter(
        TestRequirementLink.test_id == test_id,
    ).count() == 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_unlink_nonexistent_returns_none(session) -> None:
    """Idempotent on missing: desired state is "absent" and
    nothing exists, so the operation is a no-op returning
    ``None``."""
    coord = SemanticTransactionCoordinator()
    test_id = _write_a_test(coord, session)
    result = coord.unlink_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="NONEXISTENT",
        link_kind="generated_from",
    )
    assert result is None


def test_unlink_twice_is_idempotent(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id = _write_a_test(coord, session)
    coord.link_requirement(
        session, actor="s3", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    first = coord.unlink_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    assert first is not None
    second = coord.unlink_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    assert second is None


# ---------------------------------------------------------------------------
# Targeted unlink
# ---------------------------------------------------------------------------


def test_unlink_only_removes_targeted_link(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id = _write_a_test(coord, session)
    # Two links from the same test.
    coord.link_requirement(
        session, actor="s3", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    coord.link_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-2",
        link_kind="verifies",
    )
    # Unlink only PROJ-1.
    coord.unlink_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    remaining = session.query(TestRequirementLink).filter(
        TestRequirementLink.test_id == test_id,
    ).all()
    assert len(remaining) == 1
    assert remaining[0].external_key == "PROJ-2"


def test_unlink_targets_specific_link_kind(session) -> None:
    """Same test + same external_key but two link_kinds.
    Unlinking one kind leaves the other in place."""
    coord = SemanticTransactionCoordinator()
    test_id = _write_a_test(coord, session)
    coord.link_requirement(
        session, actor="s3", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    coord.link_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="verifies",
    )
    coord.unlink_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    remaining = session.query(TestRequirementLink).filter(
        TestRequirementLink.test_id == test_id,
    ).all()
    assert len(remaining) == 1
    assert remaining[0].link_kind == "verifies"


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


def test_s4_actor_rejected(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id = _write_a_test(coord, session)
    coord.link_requirement(
        session, actor="s3", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    with pytest.raises(AuthorityViolationError) as exc_info:
        coord.unlink_requirement(
            session, actor="s4", test_id=test_id,
            external_system="jira", external_key="PROJ-1",
            link_kind="generated_from",
        )
    assert "s4" in str(exc_info.value)


@pytest.mark.parametrize("actor", ["human", "s3", "s8"])
def test_human_s3_s8_actors_all_succeed(session, actor: str) -> None:
    coord = SemanticTransactionCoordinator()
    test_id = _write_a_test(coord, session)
    coord.link_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key=f"PROJ-{actor}",
        link_kind="generated_from",
    )
    result = coord.unlink_requirement(
        session, actor=actor,  # type: ignore[arg-type]
        test_id=test_id,
        external_system="jira", external_key=f"PROJ-{actor}",
        link_kind="generated_from",
    )
    assert result is not None

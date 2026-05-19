"""Tests for ``link_requirement`` — substrate-2 / external boundary.

Per Track D-δ + SPEC §9 + D-063. Covers:
  - Happy path: link a test to JIRA-1 as ``generated_from``.
  - Multi-link: one test → multiple external systems / keys /
    link kinds simultaneously.
  - Idempotency: same PK + same external_version → no-op
    (``was_noop=True``).
  - Update: same PK + different external_version → UPDATE
    external_version only; ``linked_by`` + ``linked_at``
    preserved (original authorship is authoritative).
  - Authority: ``actor='s4'`` raises
    :class:`AuthorityViolationError`; human / s3 / s8 succeed.
  - Validation: unknown ``external_system`` raises
    :class:`ValueError`; nonexistent ``test_id`` raises
    :class:`OntologyViolationError`.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from primeqa.test_representation import (
    AuthorityViolationError,
    OntologyViolationError,
    SemanticTransactionCoordinator,
    TestRequirementLink,
)

from ._builders import empty_conditions, make_value_claim


def _write_a_test(coord, session) -> "tuple[uuid4, int]":
    """Helper: write a fresh claim; return (test_id, version_seq)."""
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


def test_first_link_inserts_row(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = _write_a_test(coord, session)
    result = coord.link_requirement(
        session,
        actor="s3", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    assert result.test_id == test_id
    assert result.external_system == "jira"
    assert result.external_key == "PROJ-1"
    assert result.link_kind == "generated_from"
    assert result.was_noop is False
    assert result.linked_by == "s3"
    # Row is persisted.
    persisted = session.query(TestRequirementLink).filter(
        TestRequirementLink.test_id == test_id,
    ).all()
    assert len(persisted) == 1


def test_external_version_persisted(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = _write_a_test(coord, session)
    result = coord.link_requirement(
        session,
        actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="verifies",
        external_version="v2.3",
    )
    assert result.external_version == "v2.3"


def test_multi_link_from_one_test(session) -> None:
    """A single test can link to multiple external requirements
    AND have multiple link_kinds against the same requirement."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = _write_a_test(coord, session)
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
    coord.link_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",  # same key
        link_kind="related_to",  # different kind
    )
    rows = session.query(TestRequirementLink).filter(
        TestRequirementLink.test_id == test_id,
    ).all()
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# Idempotency + update semantics
# ---------------------------------------------------------------------------


def test_relink_with_same_external_version_is_noop(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = _write_a_test(coord, session)
    first = coord.link_requirement(
        session, actor="s3", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from", external_version="v1",
    )
    assert first.was_noop is False
    second = coord.link_requirement(
        session, actor="human",  # different actor
        test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from", external_version="v1",
    )
    assert second.was_noop is True
    # Original authorship preserved despite different actor on
    # the second call.
    assert second.linked_by == "s3"
    assert second.linked_at == first.linked_at


def test_relink_with_different_external_version_updates(session) -> None:
    """Same PK + different external_version: UPDATE
    external_version only; linked_by + linked_at preserved
    (capture original authorship)."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = _write_a_test(coord, session)
    first = coord.link_requirement(
        session, actor="s3", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from", external_version="v1",
    )
    second = coord.link_requirement(
        session, actor="human", test_id=test_id,
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from", external_version="v2",
    )
    assert second.was_noop is False
    assert second.external_version == "v2"
    assert second.linked_by == "s3"      # original preserved
    assert second.linked_at == first.linked_at


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


def test_s4_actor_rejected(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = _write_a_test(coord, session)
    with pytest.raises(AuthorityViolationError) as exc_info:
        coord.link_requirement(
            session, actor="s4", test_id=test_id,
            external_system="jira", external_key="PROJ-1",
            link_kind="generated_from",
        )
    assert "s4" in str(exc_info.value)


@pytest.mark.parametrize("actor", ["human", "s3", "s8"])
def test_human_s3_s8_actors_all_succeed(session, actor: str) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = _write_a_test(coord, session)
    result = coord.link_requirement(
        session, actor=actor, test_id=test_id,  # type: ignore[arg-type]
        external_system="jira", external_key=f"PROJ-{actor}",
        link_kind="generated_from",
    )
    assert result.linked_by == actor


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_external_system_raises_value_error(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = _write_a_test(coord, session)
    with pytest.raises(ValueError) as exc_info:
        coord.link_requirement(
            session, actor="human", test_id=test_id,
            external_system="github",  # not registered
            external_key="X-1", link_kind="verifies",
        )
    assert "github" in str(exc_info.value)


def test_nonexistent_test_id_raises_ontology_violation(session) -> None:
    coord = SemanticTransactionCoordinator()
    with pytest.raises(OntologyViolationError) as exc_info:
        coord.link_requirement(
            session, actor="human", test_id=uuid4(),
            external_system="jira", external_key="PROJ-1",
            link_kind="generated_from",
        )
    assert "test_id" in str(exc_info.value)

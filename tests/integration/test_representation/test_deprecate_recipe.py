"""Tests for ``deprecate_recipe``.

Parallel of :mod:`test_deprecate_claim` but targeting recipes.
Per Track D-ε. Provenance event: ``recipe_deprecated``.
"""
from __future__ import annotations

import time
from uuid import uuid4

import pytest

from primeqa.test_representation import (
    AuthorityViolationError,
    SemanticTransactionCoordinator,
    TestProvenance,
)

from ._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
)
from ._fixtures import arrange_approved_claim


def _write_recipe(session, coord, claim_test_id):
    r = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    return r.recipe_id, r.version_seq


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_generated_unapproved_to_deprecated(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    result = coord.deprecate_recipe(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        reason="superseded by a better recipe",
    )
    assert result.status == "deprecated"


def test_approved_to_deprecated(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
    )
    result = coord.deprecate_recipe(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        reason="retired",
    )
    assert result.status == "deprecated"


def test_deprecation_emits_provenance_with_reason(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    coord.deprecate_recipe(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        reason="obsolete after platform upgrade",
    )
    evt = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == recipe_id,
        TestProvenance.event_kind == "recipe_deprecated",
    ).one()
    assert evt.event_data["reason"] == "obsolete after platform upgrade"
    assert evt.event_data["new_status"] == "deprecated"
    assert evt.event_data["prior_status"] == "generated_unapproved"


# ---------------------------------------------------------------------------
# No-op
# ---------------------------------------------------------------------------


def test_already_deprecated_is_noop(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    first = coord.deprecate_recipe(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        reason="first",
    )
    time.sleep(0.05)
    second = coord.deprecate_recipe(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        reason="second",
    )
    assert second.status == "deprecated"
    assert second.updated_at == first.updated_at
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == recipe_id,
        TestProvenance.event_kind == "recipe_deprecated",
    ).all()
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Reason validation
# ---------------------------------------------------------------------------


def test_empty_reason_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    with pytest.raises(ValueError):
        coord.deprecate_recipe(
            session, actor="human",
            recipe_id=recipe_id, version_seq=version_seq,
            reason="",
        )


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["s3", "s8", "s4"])
def test_non_human_actors_rejected(session, actor: str) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    with pytest.raises(AuthorityViolationError):
        coord.deprecate_recipe(
            session, actor=actor,  # type: ignore[arg-type]
            recipe_id=recipe_id, version_seq=version_seq,
            reason="x",
        )


# ---------------------------------------------------------------------------
# Missing target
# ---------------------------------------------------------------------------


def test_nonexistent_recipe_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    with pytest.raises(ValueError):
        coord.deprecate_recipe(
            session, actor="human",
            recipe_id=uuid4(), version_seq=1,
            reason="x",
        )

"""Tests for ``promote_recipe_to_approved``.

Parallel of :mod:`test_promote_claim` but targeting recipes.
Per Track D-ε. Provenance event: ``recipe_approved``.
"""
from __future__ import annotations

import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    AuthorityViolationError,
    SemanticTransactionCoordinator,
    TestProvenance,
    TestRecipe,
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


def test_generated_unapproved_to_approved(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    result = coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
    )
    assert result.status == "approved"
    assert result.version_seq == version_seq


def test_promote_emits_provenance_event(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
    )
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == recipe_id,
        TestProvenance.event_kind == "recipe_approved",
    ).all()
    assert len(events) == 1
    evt = events[0]
    assert evt.event_actor == "human"
    assert evt.event_data["prior_status"] == "generated_unapproved"
    assert evt.event_data["new_status"] == "approved"
    assert evt.event_data["version_seq"] == version_seq


def test_deprecated_to_approved_undeprecation(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    session.execute(text(
        "UPDATE test_recipes SET status = 'deprecated' "
        "WHERE recipe_id = :r AND version_seq = :v"
    ), {"r": str(recipe_id), "v": version_seq})
    session.flush()
    result = coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
    )
    assert result.status == "approved"


# ---------------------------------------------------------------------------
# No-op
# ---------------------------------------------------------------------------


def test_already_approved_is_noop(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    first = coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
    )
    time.sleep(0.05)
    second = coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
    )
    assert second.status == "approved"
    assert second.updated_at == first.updated_at
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == recipe_id,
        TestProvenance.event_kind == "recipe_approved",
    ).all()
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["s3", "s8", "s4"])
def test_non_human_actors_rejected(session, actor: str) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    with pytest.raises(AuthorityViolationError):
        coord.promote_recipe_to_approved(
            session, actor=actor,  # type: ignore[arg-type]
            recipe_id=recipe_id, version_seq=version_seq,
        )


# ---------------------------------------------------------------------------
# Missing target + in-place mutation
# ---------------------------------------------------------------------------


def test_nonexistent_recipe_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    with pytest.raises(ValueError):
        coord.promote_recipe_to_approved(
            session, actor="human",
            recipe_id=uuid4(), version_seq=1,
        )


def test_promote_does_not_bump_version_seq(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
    )
    rows = session.query(TestRecipe).filter(
        TestRecipe.recipe_id == recipe_id,
    ).all()
    assert len(rows) == 1
    assert rows[0].version_seq == version_seq
    assert rows[0].status == "approved"

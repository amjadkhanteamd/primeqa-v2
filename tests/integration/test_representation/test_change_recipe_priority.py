"""Tests for ``change_recipe_priority``.

Per Track D-ε. Covers:
  - Authority: any actor in {human, s3, s8} succeeds; ``s4``
    rejected.
  - In-place mutation: no ``version_seq`` bump.
  - Version specificity: priority change on v1 doesn't affect
    v2.
  - No-op: setting priority to current value emits no provenance
    event; ``updated_at`` unchanged.
  - Negative priorities accepted.
  - Provenance event ``recipe_priority_changed`` with prior +
    new values.
"""
from __future__ import annotations

import time
from uuid import uuid4

import pytest

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


def test_change_priority_from_zero_to_five(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    # New recipes default to priority=0.
    result = coord.change_recipe_priority(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        new_priority=5,
    )
    assert result.priority == 5


def test_priority_change_emits_provenance(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    coord.change_recipe_priority(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        new_priority=7,
    )
    evt = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == recipe_id,
        TestProvenance.event_kind == "recipe_priority_changed",
    ).one()
    assert evt.event_actor == "human"
    assert evt.event_data["prior_priority"] == 0
    assert evt.event_data["new_priority"] == 7
    assert evt.event_data["version_seq"] == version_seq


def test_negative_priority_accepted(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    result = coord.change_recipe_priority(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        new_priority=-3,
    )
    assert result.priority == -3


# ---------------------------------------------------------------------------
# No-op
# ---------------------------------------------------------------------------


def test_change_to_current_priority_is_noop(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    first = coord.change_recipe_priority(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        new_priority=5,
    )
    time.sleep(0.05)
    second = coord.change_recipe_priority(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        new_priority=5,  # same as current
    )
    assert second.priority == 5
    assert second.updated_at == first.updated_at
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == recipe_id,
        TestProvenance.event_kind == "recipe_priority_changed",
    ).all()
    assert len(events) == 1  # only the first change


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["human", "s3", "s8"])
def test_non_s4_actors_succeed(session, actor: str) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    result = coord.change_recipe_priority(
        session, actor=actor,  # type: ignore[arg-type]
        recipe_id=recipe_id, version_seq=version_seq,
        new_priority=10,
    )
    assert result.priority == 10


def test_s4_rejected(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    with pytest.raises(AuthorityViolationError) as exc_info:
        coord.change_recipe_priority(
            session, actor="s4",
            recipe_id=recipe_id, version_seq=version_seq,
            new_priority=5,
        )
    assert "s4" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Missing target
# ---------------------------------------------------------------------------


def test_nonexistent_recipe_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    with pytest.raises(ValueError):
        coord.change_recipe_priority(
            session, actor="human",
            recipe_id=uuid4(), version_seq=1,
            new_priority=5,
        )


# ---------------------------------------------------------------------------
# In-place mutation + version specificity
# ---------------------------------------------------------------------------


def test_no_version_bump_after_priority_change(session) -> None:
    """Per D-ε-8: priority changes update in-place; no new
    version_seq."""
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, version_seq = _write_recipe(session, coord, claim_id)
    coord.change_recipe_priority(
        session, actor="human",
        recipe_id=recipe_id, version_seq=version_seq,
        new_priority=42,
    )
    rows = session.query(TestRecipe).filter(
        TestRecipe.recipe_id == recipe_id,
    ).all()
    assert len(rows) == 1
    assert rows[0].version_seq == version_seq
    assert rows[0].priority == 42


def test_priority_change_on_v1_does_not_affect_v2(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_id, _ = arrange_approved_claim(session, coord)
    recipe_id, _ = _write_recipe(session, coord, claim_id)
    # Write v2 (different content).
    coord.write_recipe(
        session,
        actor="human", recipe_id=recipe_id,
        claim_test_id=claim_id,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    # Change priority only on v1.
    coord.change_recipe_priority(
        session, actor="human",
        recipe_id=recipe_id, version_seq=1,
        new_priority=99,
    )
    v1 = coord.get_recipe_version(session, recipe_id, 1)
    v2 = coord.get_recipe_version(session, recipe_id, 2)
    assert v1.priority == 99
    assert v2.priority == 0  # default; unchanged

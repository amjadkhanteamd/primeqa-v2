"""Tests for ``get_recipe_runtime_state`` — raw runtime state read.

Per Track D-δ + SPEC §8. Covers:
  - Returns ``None`` for recipes with no state row.
  - Returns typed :class:`RecipeRuntimeState` after a
    ``report_run_outcome`` call.
  - Round-trip equivalence: report → get returns the same
    snapshot.
  - Pass / failure timestamps persist across reports.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from primeqa.test_representation import (
    RecipeRuntimeState,
    SemanticTransactionCoordinator,
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
    return r.recipe_id


def test_returns_none_for_recipe_with_no_state(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    assert coord.get_recipe_runtime_state(session, recipe_id) is None


def test_returns_none_for_nonexistent_recipe(session) -> None:
    """The read does no recipe-existence check; an unknown
    recipe_id simply has no runtime row."""
    coord = SemanticTransactionCoordinator()
    assert coord.get_recipe_runtime_state(session, uuid4()) is None


def test_round_trip_after_report(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    when = datetime.now(timezone.utc)
    reported = coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=when,
        last_run_outcome="passed", last_run_recipe_version_seq=1,
    )
    read = coord.get_recipe_runtime_state(session, recipe_id)
    assert isinstance(read, RecipeRuntimeState)
    assert read == reported


def test_pass_and_failure_timestamps_persist_across_reports(
    session,
) -> None:
    """A pass establishes ``last_pass_at``; a subsequent
    failure sets ``last_failure_at`` and preserves
    ``last_pass_at``. A third pass updates ``last_pass_at``
    again; ``last_failure_at`` is preserved."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    t1 = datetime.now(timezone.utc) - timedelta(hours=2)
    t2 = datetime.now(timezone.utc) - timedelta(hours=1)
    t3 = datetime.now(timezone.utc)

    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=t1,
        last_run_outcome="passed", last_run_recipe_version_seq=1,
    )
    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=t2,
        last_run_outcome="failed", last_run_recipe_version_seq=1,
    )
    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=t3,
        last_run_outcome="passed", last_run_recipe_version_seq=1,
    )
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state.last_run_outcome == "passed"
    assert state.last_run_at == t3
    assert state.last_pass_at == t3       # updated on third report
    assert state.last_failure_at == t2    # preserved from second report

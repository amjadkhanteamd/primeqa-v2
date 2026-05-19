"""Tests for ``report_run_outcome`` — the S4 boundary callback.

Per Track D-δ + SPEC §8.3. Covers:
  - Authority: only ``actor='s4'`` is accepted; other actors
    raise :class:`AuthorityViolationError`.
  - Idempotency: same ``last_run_id`` reported twice → no-op
    return; **first-write-wins**.
  - Upsert semantics: one row per ``recipe_id``; subsequent
    reports UPDATE.
  - Accumulation: ``last_pass_at`` / ``last_failure_at`` track
    the most-recent timestamp of their respective categories,
    preserved across non-matching outcomes.
  - ``last_run_recipe_version_seq`` records which version was
    last actually run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    AuthorityViolationError,
    SemanticTransactionCoordinator,
)

from ._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
)
from ._fixtures import arrange_approved_claim


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_recipe(session, coord, claim_test_id):
    """Helper: write a minimal recipe; return recipe_id."""
    r = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="data-mutation-trigger",
        recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    return r.recipe_id


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["human", "s3", "s8"])
def test_non_s4_actors_rejected(session, actor: str) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    with pytest.raises(AuthorityViolationError) as exc_info:
        coord.report_run_outcome(
            session,
            actor=actor,  # type: ignore[arg-type]
            recipe_id=recipe_id,
            last_run_id=uuid4(),
            last_run_at=_now(),
            last_run_outcome="passed",
            last_run_recipe_version_seq=1,
        )
    assert "8.3" in str(exc_info.value)


def test_s4_actor_succeeds(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    state = coord.report_run_outcome(
        session,
        actor="s4",
        recipe_id=recipe_id,
        last_run_id=uuid4(),
        last_run_at=_now(),
        last_run_outcome="passed",
        last_run_recipe_version_seq=1,
    )
    assert state.recipe_id == recipe_id
    assert state.last_run_outcome == "passed"


# ---------------------------------------------------------------------------
# Happy path + persistence
# ---------------------------------------------------------------------------


def test_first_report_inserts_row(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    run_id = uuid4()
    when = _now()
    state = coord.report_run_outcome(
        session,
        actor="s4",
        recipe_id=recipe_id,
        last_run_id=run_id,
        last_run_at=when,
        last_run_outcome="passed",
        last_run_recipe_version_seq=1,
    )
    assert state.last_run_id == run_id
    assert state.last_run_outcome == "passed"
    assert state.last_pass_at == when
    assert state.last_failure_at is None
    assert state.last_run_recipe_version_seq == 1


def test_subsequent_report_updates_row(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    t1 = _now() - timedelta(minutes=10)
    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=t1,
        last_run_outcome="passed", last_run_recipe_version_seq=1,
    )
    t2 = _now()
    state = coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=t2,
        last_run_outcome="failed", last_run_recipe_version_seq=1,
    )
    assert state.last_run_outcome == "failed"
    assert state.last_run_at == t2
    assert state.last_pass_at == t1     # preserved
    assert state.last_failure_at == t2  # set on this report


# ---------------------------------------------------------------------------
# Accumulation per outcome
# ---------------------------------------------------------------------------


def test_errored_treated_as_failure(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    when = _now()
    state = coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=when,
        last_run_outcome="errored", last_run_recipe_version_seq=1,
    )
    assert state.last_run_outcome == "errored"
    assert state.last_failure_at == when
    assert state.last_pass_at is None


def test_skipped_leaves_pass_and_failure_unchanged(session) -> None:
    """Per the prompt: ``skipped`` updates neither
    ``last_pass_at`` nor ``last_failure_at``."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    # Establish a pass first.
    t1 = _now() - timedelta(minutes=10)
    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=t1,
        last_run_outcome="passed", last_run_recipe_version_seq=1,
    )
    # Then a skip.
    t2 = _now()
    state = coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=t2,
        last_run_outcome="skipped", last_run_recipe_version_seq=1,
    )
    assert state.last_run_outcome == "skipped"
    assert state.last_run_at == t2
    assert state.last_pass_at == t1     # preserved
    assert state.last_failure_at is None  # never set


# ---------------------------------------------------------------------------
# Idempotency (first-write-wins on duplicate run_id)
# ---------------------------------------------------------------------------


def test_duplicate_run_id_is_noop(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    run_id = uuid4()
    when = _now()
    first = coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=run_id, last_run_at=when,
        last_run_outcome="passed", last_run_recipe_version_seq=1,
    )
    second = coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=run_id, last_run_at=_now(),
        last_run_outcome="failed",  # different outcome
        last_run_recipe_version_seq=1,
    )
    # First-write-wins: outcome stays 'passed'.
    assert second.last_run_outcome == "passed"
    assert second.last_run_id == run_id
    assert second.last_run_at == first.last_run_at  # unchanged
    assert second.last_pass_at == first.last_pass_at
    assert second.updated_at == first.updated_at


def test_different_run_ids_each_update(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    # Three reports with distinct run_ids; each advances the row.
    seen_run_ids = []
    for outcome in ("passed", "failed", "passed"):
        run_id = uuid4()
        seen_run_ids.append(run_id)
        coord.report_run_outcome(
            session, actor="s4", recipe_id=recipe_id,
            last_run_id=run_id, last_run_at=_now(),
            last_run_outcome=outcome,  # type: ignore[arg-type]
            last_run_recipe_version_seq=1,
        )
    final = coord.get_recipe_runtime_state(session, recipe_id)
    assert final.last_run_id == seen_run_ids[-1]
    assert final.last_run_outcome == "passed"
    # Both last_pass_at and last_failure_at have been set.
    assert final.last_pass_at is not None
    assert final.last_failure_at is not None


# ---------------------------------------------------------------------------
# last_run_recipe_version_seq tracking
# ---------------------------------------------------------------------------


def test_last_run_recipe_version_seq_records_actual_version(
    session,
) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id = _write_recipe(session, coord, test_id)
    # Report a run against v1.
    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=_now(),
        last_run_outcome="passed", last_run_recipe_version_seq=1,
    )
    # Write recipe v2.
    coord.write_recipe(
        session,
        actor="human", recipe_id=recipe_id,
        claim_test_id=test_id,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    # Report a run against v2.
    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=_now(),
        last_run_outcome="passed", last_run_recipe_version_seq=2,
    )
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state.last_run_recipe_version_seq == 2

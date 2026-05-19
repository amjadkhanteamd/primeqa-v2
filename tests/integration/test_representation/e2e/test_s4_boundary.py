"""End-to-end S4 boundary scenarios.

Track E category δ. Each scenario exercises the S4 callback
chain in realistic compositions: runs reported, runs updated,
recipe versions advancing while runtime state persists, and
idempotent retry behavior.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from primeqa.test_representation import (
    SemanticTransactionCoordinator,
)

from .._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
)
from .._fixtures import (
    arrange_approved_test_with_recipes,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# δ-1: Full S4 callback chain across two recipes
# ---------------------------------------------------------------------------


def test_full_s4_callback_chain(session) -> None:
    """Two approved recipes for one test. S4 reports passing
    runs on both → 'passing'. S4 reports a failure on one
    (different run_id) → state UPDATEd with last_failure_at;
    last_pass_at preserved. Test status flips to 'failing'.
    """
    coord = SemanticTransactionCoordinator()
    test_id, recipe_ids = arrange_approved_test_with_recipes(
        session, coord, num_recipes=2,
    )
    r1, r2 = recipe_ids
    initial_pass_ts_r1 = _now()
    coord.report_run_outcome(
        session, actor="s4", recipe_id=r1,
        last_run_id=uuid4(), last_run_at=initial_pass_ts_r1,
        last_run_outcome="passed",
        last_run_recipe_version_seq=1,
    )
    coord.report_run_outcome(
        session, actor="s4", recipe_id=r2,
        last_run_id=uuid4(), last_run_at=_now(),
        last_run_outcome="passed",
        last_run_recipe_version_seq=1,
    )

    # Both passing → test passing.
    assert coord.get_test_runtime_status(session, test_id) == "passing"

    # Raw runtime state retrievable per recipe.
    s1 = coord.get_recipe_runtime_state(session, r1)
    s2 = coord.get_recipe_runtime_state(session, r2)
    assert s1 is not None and s1.last_run_outcome == "passed"
    assert s2 is not None and s2.last_run_outcome == "passed"

    # S4 reports a failure on r1 with a NEW run_id.
    fail_ts = _now()
    coord.report_run_outcome(
        session, actor="s4", recipe_id=r1,
        last_run_id=uuid4(), last_run_at=fail_ts,
        last_run_outcome="failed",
        last_run_recipe_version_seq=1,
    )
    s1 = coord.get_recipe_runtime_state(session, r1)
    assert s1.last_run_outcome == "failed"
    assert s1.last_failure_at == fail_ts
    # The earlier pass timestamp is preserved.
    assert s1.last_pass_at == initial_pass_ts_r1

    # Composed test status flips to failing.
    assert coord.get_test_runtime_status(session, test_id) == "failing"


# ---------------------------------------------------------------------------
# δ-2: Late S4 reports track recipe version advancement
# ---------------------------------------------------------------------------


def test_late_s4_reports_track_recipe_version_advancement(
    session,
) -> None:
    """S4 reports a run on recipe v1; later, recipe gets a v2
    (same recipe_id). Runtime state persists (one row per
    recipe_id per SPEC §8.2). The state's
    ``last_run_recipe_version_seq=1`` signals "runtime state is
    stale relative to v2." S4 eventually reports a run on v2;
    state UPDATEs to version_seq=2.
    """
    coord = SemanticTransactionCoordinator()
    test_id, recipe_ids = arrange_approved_test_with_recipes(
        session, coord, num_recipes=1,
    )
    recipe_id = recipe_ids[0]

    # S4 reports run on v1.
    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=_now(),
        last_run_outcome="passed",
        last_run_recipe_version_seq=1,
    )
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state.last_run_recipe_version_seq == 1

    # Human writes v2 of the recipe (different content). Lands
    # generated_unapproved per §7.4.
    coord.write_recipe(
        session,
        actor="human", recipe_id=recipe_id,
        claim_test_id=test_id,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=recipe_id, version_seq=2,
    )

    # Runtime state row still exists, still pointing at v1 —
    # callers detect "stale" via the version_seq mismatch.
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state is not None
    assert state.last_run_recipe_version_seq == 1

    # S4 eventually reports a run against v2. State advances.
    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(), last_run_at=_now(),
        last_run_outcome="passed",
        last_run_recipe_version_seq=2,
    )
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state.last_run_recipe_version_seq == 2


# ---------------------------------------------------------------------------
# δ-3: S4 idempotency in workflow (retry-safe)
# ---------------------------------------------------------------------------


def test_s4_idempotency_in_workflow(session) -> None:
    """S4 reports a run with ``run_id=X, outcome=passed``. A
    network glitch causes S4 to retry the same run_id with
    ``outcome=failed`` (a divergent corrected value). Per
    D-δ first-write-wins: the second call no-ops; state
    remains 'passed'; ``updated_at`` unchanged.
    """
    coord = SemanticTransactionCoordinator()
    _, recipe_ids = arrange_approved_test_with_recipes(
        session, coord, num_recipes=1,
    )
    recipe_id = recipe_ids[0]
    run_id = uuid4()
    initial = coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=run_id, last_run_at=_now(),
        last_run_outcome="passed",
        last_run_recipe_version_seq=1,
    )

    # Retry — same run_id, divergent outcome.
    retry = coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=run_id, last_run_at=_now(),
        last_run_outcome="failed",
        last_run_recipe_version_seq=1,
    )
    # First-write-wins: still passed; updated_at unchanged.
    assert retry.last_run_outcome == "passed"
    assert retry.last_run_id == run_id
    assert retry.updated_at == initial.updated_at

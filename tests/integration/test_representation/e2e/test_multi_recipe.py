"""End-to-end multi-recipe coordination scenarios.

Track E category γ. Each scenario sets up several recipes for a
single approved claim and exercises the Coordinator's selection
+ runtime-resolution logic across the recipe set. Includes the
"no autonomous execution" commitment in workflow form per
D-γ.2-4.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from primeqa.test_representation import (
    ExecutionEnvironmentBody,
    SemanticTransactionCoordinator,
)

from .._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
)
from .._fixtures import (
    arrange_approved_claim,
    arrange_approved_test_with_recipes,
    make_minimal_execution_environment,
)


# ---------------------------------------------------------------------------
# γ-1: Environment-based recipe selection
# ---------------------------------------------------------------------------


def test_environment_based_recipe_selection(session) -> None:
    """Three recipes with different environment requirements;
    :meth:`select_recipe_for_execution` returns the matching
    recipe based on the available environment passed in.
    """
    coord = SemanticTransactionCoordinator()
    env_a = make_minimal_execution_environment(
        org_kind="sandbox", feature_names=["lightning"],
    )
    env_b = make_minimal_execution_environment(
        org_kind="production",
    )
    env_c = make_minimal_execution_environment(
        org_kind="sandbox", feature_names=["classic"],
    )
    test_id, recipe_ids = arrange_approved_test_with_recipes(
        session, coord, num_recipes=3,
        recipe_environments=[env_a, env_b, env_c],
    )
    id_a, id_b, id_c = recipe_ids

    # Available: sandbox + lightning → recipe A wins.
    available = make_minimal_execution_environment(
        org_kind="sandbox", feature_names=["lightning"],
    )
    chosen = coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    )
    assert chosen is not None
    assert chosen.recipe_id == id_a

    # Available: production → recipe B wins.
    available = make_minimal_execution_environment(
        org_kind="production",
    )
    chosen = coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    )
    assert chosen.recipe_id == id_b

    # Available: sandbox + classic → recipe C wins.
    available = make_minimal_execution_environment(
        org_kind="sandbox", feature_names=["classic"],
    )
    chosen = coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    )
    assert chosen.recipe_id == id_c

    # Available: sandbox alone (no lightning, no classic) → no
    # matching recipe (recipe_A needs lightning; recipe_B needs
    # production; recipe_C needs classic).
    available = make_minimal_execution_environment(org_kind="sandbox")
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    ) is None


# ---------------------------------------------------------------------------
# γ-2: Priority dynamics with deprecation + repriori­tization
# ---------------------------------------------------------------------------


def test_priority_dynamics_with_deprecation(session) -> None:
    """Three recipes at priorities [5, 10, 7]; selection returns
    the 10. Human deprecates 10 → selection returns 7. Human
    bumps 5 → 15; selection returns the (formerly-5, now-15)
    recipe.
    """
    coord = SemanticTransactionCoordinator()
    test_id, recipe_ids = arrange_approved_test_with_recipes(
        session, coord, num_recipes=3,
        recipe_priorities=[5, 10, 7],
    )
    id_5, id_10, id_7 = recipe_ids
    empty_env = ExecutionEnvironmentBody()

    # Step 1: priority=10 wins.
    chosen = coord.select_recipe_for_execution(
        session, test_id, available_environment=empty_env,
    )
    assert chosen.recipe_id == id_10
    assert chosen.priority == 10

    # Step 2: deprecate the priority=10 recipe. Next-highest is 7.
    coord.deprecate_recipe(
        session, actor="human",
        recipe_id=id_10, version_seq=1,
        reason="superseded by a higher-priority alternative",
    )
    chosen = coord.select_recipe_for_execution(
        session, test_id, available_environment=empty_env,
    )
    assert chosen.recipe_id == id_7

    # Step 3: bump the priority=5 recipe to 15.
    coord.change_recipe_priority(
        session, actor="human",
        recipe_id=id_5, version_seq=1,
        new_priority=15,
    )
    chosen = coord.select_recipe_for_execution(
        session, test_id, available_environment=empty_env,
    )
    assert chosen.recipe_id == id_5
    assert chosen.priority == 15


# ---------------------------------------------------------------------------
# γ-3: Mixed outcomes compose to test status
# ---------------------------------------------------------------------------


def test_mixed_outcomes_compose_to_test_status(session) -> None:
    """Three approved recipes; verify SPEC §8.4 conservative
    composition rules:
      - all passed → passing
      - any failed → failing (any-failure-wins)
      - mix of passed + skipped, no failures → passing
        (skipped doesn't downgrade)
    """
    coord = SemanticTransactionCoordinator()
    test_id, recipe_ids = arrange_approved_test_with_recipes(
        session, coord, num_recipes=3,
    )
    r1, r2, r3 = recipe_ids

    def report(recipe_id: str, outcome: str) -> None:
        coord.report_run_outcome(
            session, actor="s4",
            recipe_id=recipe_id, last_run_id=uuid4(),
            last_run_at=datetime.now(timezone.utc),
            last_run_outcome=outcome,  # type: ignore[arg-type]
            last_run_recipe_version_seq=1,
        )

    # All three pass → passing.
    report(r1, "passed")
    report(r2, "passed")
    report(r3, "passed")
    assert coord.get_test_runtime_status(session, test_id) == "passing"

    # r2 now fails → failing (any failure wins).
    report(r2, "failed")
    assert coord.get_test_runtime_status(session, test_id) == "failing"

    # r2 passes again; r1 skipped → passing (skipped doesn't
    # downgrade as long as at least one passed and none failed).
    report(r2, "passed")
    report(r1, "skipped")
    assert coord.get_test_runtime_status(session, test_id) == "passing"


# ---------------------------------------------------------------------------
# γ-4: Generated-unapproved recipe invisible to execution
# ---------------------------------------------------------------------------


def test_generated_unapproved_recipe_invisible_to_execution(
    session,
) -> None:
    """Per D-γ.2-4 / D-061 §7.4: a recipe written by any actor
    lands in ``generated_unapproved`` status. Until a human
    explicitly promotes it,
    :meth:`select_recipe_for_execution` won't return it — even
    if the environment matches. The substrate's "no autonomous
    execution" commitment in workflow form.
    """
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    empty_env = ExecutionEnvironmentBody()

    # S8 writes a recipe. Lands generated_unapproved per §7.4.
    write_result = coord.write_recipe(
        session,
        actor="s8", recipe_id=None,
        claim_test_id=test_id,
        trigger_kind="data-mutation-trigger",
        recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    assert write_result.status == "generated_unapproved"

    # No eligible recipes → select returns None even though
    # environment is satisfied (the empty environment matches
    # the empty execution_environment).
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=empty_env,
    ) is None

    # Test status is 'untested' (no eligible recipes → no run
    # resolution possible).
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "untested"

    # Human promotes the recipe to approved.
    coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=write_result.recipe_id, version_seq=1,
    )

    # Now the recipe surfaces.
    chosen = coord.select_recipe_for_execution(
        session, test_id, available_environment=empty_env,
    )
    assert chosen is not None
    assert chosen.recipe_id == write_result.recipe_id

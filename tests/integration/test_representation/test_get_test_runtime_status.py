"""Tests for ``get_test_runtime_status`` resolution.

Per Track D-γ.2 + SPEC §8.4 + §8.5. The resolution composes:
  - current-approved claim (no approved → "untested")
  - eligible recipe filtering (status IN ('active', 'approved'))
  - per-recipe runtime state aggregation
  - SPEC §8.4 conservative outcome composition policy

SPEC §8.5 acknowledges the conservative policy cannot fully
address all multi-recipe scenarios; v1's behavior is documented
exactly as specified.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from primeqa.test_representation import (
    SemanticTransactionCoordinator,
)

from ._builders import empty_conditions, make_value_claim
from ._fixtures import (
    arrange_approved_claim,
    arrange_recipe_with_status,
    arrange_runtime_state,
)


# ---------------------------------------------------------------------------
# Untested cases
# ---------------------------------------------------------------------------


def test_untested_when_no_approved_claim(session) -> None:
    coord = SemanticTransactionCoordinator()
    # write_claim produces draft, no approval → "untested"
    body = make_value_claim()
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert coord.get_test_runtime_status(
        session, r.test_id,
    ) == "untested"


def test_untested_when_no_recipes_at_all(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "untested"


def test_untested_when_only_generated_unapproved_recipes(
    session,
) -> None:
    """``generated_unapproved`` recipes are NOT eligible. With
    no eligible recipes, the test is untested per the SPEC §8.4
    conservative policy."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id,
        status="generated_unapproved",
    )
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "untested"


def test_untested_when_only_deprecated_recipes(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="deprecated",
    )
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "untested"


def test_untested_when_eligible_recipes_have_no_runtime_state(
    session,
) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    # No arrange_runtime_state call → no runtime row.
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "untested"


def test_untested_when_all_runtime_outcomes_skipped(session) -> None:
    """Per SPEC §8.4: all-skipped means no recipe was actually
    exercised → untested. ``skipped`` outcomes don't constitute
    a real test run."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    arrange_runtime_state(
        session, recipe_id=recipe_id, outcome="skipped",
    )
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "untested"


# ---------------------------------------------------------------------------
# Failing cases
# ---------------------------------------------------------------------------


def test_failing_on_any_failed_outcome(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    arrange_runtime_state(
        session, recipe_id=recipe_id, outcome="failed",
    )
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "failing"


def test_failing_on_any_errored_outcome(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    arrange_runtime_state(
        session, recipe_id=recipe_id, outcome="errored",
    )
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "failing"


def test_failing_on_mix_of_passed_and_failed(session) -> None:
    """Any failure → failing, even with other recipes passing."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_a, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    recipe_b, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="approved",
    )
    arrange_runtime_state(session, recipe_id=recipe_a, outcome="passed")
    arrange_runtime_state(session, recipe_id=recipe_b, outcome="failed")
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "failing"


# ---------------------------------------------------------------------------
# Passing cases
# ---------------------------------------------------------------------------


def test_passing_when_all_eligible_recipes_passed(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    r1, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    r2, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="approved",
    )
    arrange_runtime_state(session, recipe_id=r1, outcome="passed")
    arrange_runtime_state(session, recipe_id=r2, outcome="passed")
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "passing"


def test_passing_when_passed_plus_skipped(session) -> None:
    """At least one passed + others skipped + no failures → passing."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    r1, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    r2, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    arrange_runtime_state(session, recipe_id=r1, outcome="passed")
    arrange_runtime_state(session, recipe_id=r2, outcome="skipped")
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "passing"


# ---------------------------------------------------------------------------
# Mixed cases
# ---------------------------------------------------------------------------


def test_mixed_when_some_have_state_others_dont(session) -> None:
    """Some recipes have passed; others have no runtime row.
    No failures → "mixed" per the conservative policy."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    r1, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="approved",
    )
    arrange_runtime_state(session, recipe_id=r1, outcome="passed")
    # r2 has no runtime state.
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "mixed"


def test_mixed_with_passed_skipped_and_no_state(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    r1, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    r2, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    arrange_runtime_state(session, recipe_id=r1, outcome="passed")
    arrange_runtime_state(session, recipe_id=r2, outcome="skipped")
    # r3 has no runtime state.
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "mixed"


# ---------------------------------------------------------------------------
# Eligibility edge cases
# ---------------------------------------------------------------------------


def test_generated_unapproved_recipe_runtime_state_ignored(
    session,
) -> None:
    """A generated_unapproved recipe with a runtime-state row is
    NOT eligible; its outcome doesn't enter the resolution.
    The substrate's "no autonomous execution" commitment in
    storage form: writing a runtime state for an unapproved
    recipe doesn't influence the test's status."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    unapproved, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id,
        status="generated_unapproved",
    )
    arrange_runtime_state(
        session, recipe_id=unapproved, outcome="failed",
    )
    # Despite the "failed" runtime state, the test is "untested"
    # because the recipe isn't eligible.
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "untested"


def test_deprecated_recipe_runtime_state_ignored(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    deprecated, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="deprecated",
    )
    arrange_runtime_state(
        session, recipe_id=deprecated, outcome="failed",
    )
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "untested"


def test_runtime_state_persists_across_recipe_versions(session) -> None:
    """Per SPEC §8.2: runtime state is keyed by recipe_id, not
    by (recipe_id, version_seq). Writing v2 of a recipe inherits
    v1's runtime state row. The runtime state's
    ``last_run_recipe_version_seq`` column records which version
    was actually run (callers detect "runtime is stale" via
    that column)."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    arrange_runtime_state(
        session, recipe_id=recipe_id, outcome="passed",
        last_run_recipe_version_seq=1,
    )
    # Now write recipe v2 (same recipe_id, different content).
    from ._builders import (
        build_minimal_data_mutation_trigger_body,
        build_minimal_data_recipe_body,
        build_minimal_execution_environment_body,
    )
    from sqlalchemy import text as _t
    coord.write_recipe(
        session,
        actor="human", recipe_id=recipe_id,
        claim_test_id=test_id,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    # v2 lands as generated_unapproved — promote to active.
    session.execute(_t(
        "UPDATE test_recipes SET status = 'active' "
        "WHERE recipe_id = :r AND version_seq = 2"
    ), {"r": str(recipe_id)})
    session.flush()
    # The runtime row's last_run_recipe_version_seq is still 1 —
    # the recipe was last run before the v2 write. But the test
    # is still "passing" because the runtime state row exists
    # for the recipe_id; consumers detect the staleness via the
    # version_seq column.
    assert coord.get_test_runtime_status(
        session, test_id,
    ) == "passing"

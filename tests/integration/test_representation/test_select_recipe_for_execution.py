"""Tests for ``select_recipe_for_execution`` resolution.

Per Track D-γ.2 + SPEC §10.4 + §6.8. The resolution composes:
  - current-approved claim resolution
  - eligible-recipe filtering (D-γ.2's "no autonomous execution"
    commitment)
  - environment satisfiability via membership-based matching
  - deterministic ordering (priority DESC, version_seq DESC,
    recipe_id ASC)

SPEC §6.8 reserves historical and semantic replay modes for
future evolution; other ``replay_mode`` values raise
:class:`NotImplementedError`.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    ExecutionEnvironmentBody,
    FeatureAssumption,
    SemanticTransactionCoordinator,
)

from ._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
    empty_conditions,
    make_value_claim,
)
from ._fixtures import (
    arrange_approved_claim,
    arrange_recipe_with_status,
    make_minimal_execution_environment,
)


def _empty_env() -> ExecutionEnvironmentBody:
    return ExecutionEnvironmentBody()


# ---------------------------------------------------------------------------
# Selection returns None
# ---------------------------------------------------------------------------


def test_returns_none_when_no_approved_claim(session) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert coord.select_recipe_for_execution(
        session, r.test_id, available_environment=_empty_env(),
    ) is None


def test_returns_none_when_no_eligible_recipes(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    # Only a generated_unapproved recipe exists.
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id,
        status="generated_unapproved",
    )
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    ) is None


def test_returns_none_when_no_recipes_match_environment(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    # Recipe requires feature_X; available env doesn't have it.
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            feature_names=["feature_X"],
        ),
    )
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    ) is None


def test_returns_single_matching_recipe(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    assert result is not None
    assert result.recipe_id == recipe_id


# ---------------------------------------------------------------------------
# Eligibility (the "no autonomous execution" commitment)
# ---------------------------------------------------------------------------


def test_generated_unapproved_not_eligible(session) -> None:
    """The substrate's authority model in storage form: an
    unapproved recipe is NOT eligible for execution, even when
    environment matches. Per D-γ.2-4 / D-061 §7.4."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id,
        status="generated_unapproved",
    )
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    ) is None


def test_approved_status_is_eligible(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="approved",
    )
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    assert result is not None
    assert result.status == "approved"


def test_deprecated_not_eligible(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="deprecated",
    )
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    ) is None


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


def test_priority_desc_ordering(session) -> None:
    """Three recipes with priorities [5, 10, 7] → returns 10."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    ids = {}
    for prio in (5, 10, 7):
        rid, _ = arrange_recipe_with_status(
            session, coord, claim_test_id=test_id,
            status="active", priority=prio,
        )
        ids[prio] = rid
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    assert result.recipe_id == ids[10]


def test_tiebreak_by_version_seq_desc(session) -> None:
    """Same priority, different version_seq → higher version_seq
    wins."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    # Two distinct recipes, both at priority=5. The second
    # recipe gets a v2 written so version_seq differs.
    r1, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id,
        status="active", priority=5,
    )
    r2, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id,
        status="active", priority=5,
    )
    # Write v2 of r2 (same recipe_id, new version_seq).
    coord.write_recipe(
        session,
        actor="human", recipe_id=r2,
        claim_test_id=test_id,
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    # Promote v2 to active.
    session.execute(text(
        "UPDATE test_recipes SET status = 'active', priority = 5 "
        "WHERE recipe_id = :r AND version_seq = 2"
    ), {"r": str(r2)})
    session.flush()

    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    # r2 wins on version_seq DESC tiebreak.
    assert result.recipe_id == r2
    assert result.version_seq == 2


def test_tiebreak_by_recipe_id_asc(session) -> None:
    """Same priority, same version_seq → recipe_id ASC wins."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    r_a, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id,
        status="active", priority=0,
    )
    r_b, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id,
        status="active", priority=0,
    )
    expected_winner = sorted([str(r_a), str(r_b)])[0]
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    assert str(result.recipe_id) == expected_winner


# ---------------------------------------------------------------------------
# Environment matching: org_kind / salesforce_edition
# ---------------------------------------------------------------------------


def test_org_kind_match(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            org_kind="sandbox",
        ),
    )
    available = make_minimal_execution_environment(org_kind="sandbox")
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    )
    assert result is not None


def test_org_kind_mismatch(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            org_kind="sandbox",
        ),
    )
    available = make_minimal_execution_environment(org_kind="production")
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    ) is None


def test_recipe_org_kind_none_accepts_any_available(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            org_kind=None,  # any org_kind acceptable
        ),
    )
    available = make_minimal_execution_environment(org_kind="production")
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    )
    assert result is not None


def test_salesforce_edition_match_and_mismatch(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            salesforce_edition="unlimited",
        ),
    )
    # Mismatch.
    available_ent = make_minimal_execution_environment(
        salesforce_edition="enterprise",
    )
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=available_ent,
    ) is None
    # Match.
    available_ulim = make_minimal_execution_environment(
        salesforce_edition="unlimited",
    )
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=available_ulim,
    ) is not None


# ---------------------------------------------------------------------------
# Environment matching: feature_assumptions (required + negative)
# ---------------------------------------------------------------------------


def test_required_feature_match(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            feature_names=["CDC"],
        ),
    )
    available = make_minimal_execution_environment(feature_names=["CDC"])
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    )
    assert result is not None


def test_required_feature_missing(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            feature_names=["CDC"],
        ),
    )
    available = _empty_env()
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    ) is None


def test_negative_assumption_satisfied_when_feature_absent(
    session,
) -> None:
    """``required=False`` is a NEGATIVE assumption — recipe
    expects feature ABSENT in target. Satisfied when available
    doesn't have it."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_env = ExecutionEnvironmentBody(
        feature_assumptions=[
            FeatureAssumption(feature_name="LegacyAPI", required=False),
        ],
    )
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=recipe_env,
    )
    available = _empty_env()  # no features at all
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    )
    assert result is not None


def test_negative_assumption_violated_when_feature_present(
    session,
) -> None:
    """``required=False`` is violated when the feature IS
    present in available — recipe expected absence."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_env = ExecutionEnvironmentBody(
        feature_assumptions=[
            FeatureAssumption(feature_name="LegacyAPI", required=False),
        ],
    )
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=recipe_env,
    )
    available = make_minimal_execution_environment(
        feature_names=["LegacyAPI"],
    )
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    ) is None


# ---------------------------------------------------------------------------
# Environment matching: auth + infrastructure
# ---------------------------------------------------------------------------


def test_auth_assumption_match(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            auth_kinds=["ui_session"],
        ),
    )
    available = make_minimal_execution_environment(
        auth_kinds=["ui_session"],
    )
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    )
    assert result is not None


def test_auth_assumption_missing(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            auth_kinds=["ui_session"],
        ),
    )
    available = _empty_env()
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    ) is None


def test_infrastructure_assumption_match(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            infra_kinds=["browser"],
        ),
    )
    available = make_minimal_execution_environment(
        infra_kinds=["browser"],
    )
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    )
    assert result is not None


def test_infrastructure_assumption_missing(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            infra_kinds=["browser"],
        ),
    )
    available = _empty_env()
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=available,
    ) is None


# ---------------------------------------------------------------------------
# replay_mode
# ---------------------------------------------------------------------------


def test_replay_mode_live_is_default(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    # Default replay_mode is "live"; same call without the kwarg.
    result = coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    assert result is not None


def test_replay_mode_historical_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    with pytest.raises(NotImplementedError) as exc_info:
        coord.select_recipe_for_execution(
            session, test_id,
            available_environment=_empty_env(),
            replay_mode="historical",  # type: ignore[arg-type]
        )
    assert "6.8" in str(exc_info.value)


def test_replay_mode_semantic_raises(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    with pytest.raises(NotImplementedError):
        coord.select_recipe_for_execution(
            session, test_id,
            available_environment=_empty_env(),
            replay_mode="semantic",  # type: ignore[arg-type]
        )


def test_replay_mode_invalid_value_raises(session) -> None:
    """Any non-'live' value raises ``NotImplementedError`` per
    SPEC §6.8 (only 'live' is supported at v1)."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    with pytest.raises(NotImplementedError):
        coord.select_recipe_for_execution(
            session, test_id,
            available_environment=_empty_env(),
            replay_mode="bogus",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# select_recipes_for_execution (PLURAL) — the run-all applicable set (D-275, 3.1)
# ---------------------------------------------------------------------------
# These share the singular's resolution (eligibility + env-satisfiability + sort)
# via the private _matching_recipes_for_execution helper; the existing tests above
# are the singular's byte-identity safety net after that refactor.


def test_plural_empty_when_no_approved_claim(session) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    r = coord.write_claim(
        session, actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert coord.select_recipes_for_execution(
        session, r.test_id, available_environment=_empty_env(),
    ) == []


def test_plural_empty_when_no_eligible_recipes(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="generated_unapproved",
    )
    assert coord.select_recipes_for_execution(
        session, test_id, available_environment=_empty_env(),
    ) == []


def test_plural_returns_full_set_in_priority_order(session) -> None:
    """Three active recipes priorities [5, 10, 7] → the full set, sorted
    priority DESC: [10, 7, 5]."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    ids = {}
    for prio in (5, 10, 7):
        rid, _ = arrange_recipe_with_status(
            session, coord, claim_test_id=test_id, status="active", priority=prio,
        )
        ids[prio] = rid
    result = coord.select_recipes_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    assert [r.recipe_id for r in result] == [ids[10], ids[7], ids[5]]


def test_plural_single_recipe_parity_with_singular(session) -> None:
    """One recipe → plural is a 1-element list whose [0] is exactly what the
    singular returns."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    recipe_id, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    plural = coord.select_recipes_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    singular = coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    assert len(plural) == 1
    assert plural[0].recipe_id == recipe_id == singular.recipe_id


def test_plural_head_equals_singular_multi(session) -> None:
    """The singular-unchanged guarantee: with N recipes, plural[0] is exactly the
    recipe the singular selects (the matching[0] tie-break head)."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    for prio in (5, 10, 7):
        arrange_recipe_with_status(
            session, coord, claim_test_id=test_id, status="active", priority=prio,
        )
    plural = coord.select_recipes_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    singular = coord.select_recipe_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    assert plural[0].recipe_id == singular.recipe_id


def test_plural_excludes_environment_unsatisfiable(session) -> None:
    """The env filter applies to the full set: a recipe requiring feature_X is
    excluded when the available env lacks it; the satisfiable one remains."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    ok_id, _ = arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
        execution_environment=make_minimal_execution_environment(
            feature_names=["feature_X"],
        ),
    )
    result = coord.select_recipes_for_execution(
        session, test_id, available_environment=_empty_env(),
    )
    assert [r.recipe_id for r in result] == [ok_id]


def test_plural_replay_mode_non_live_raises(session) -> None:
    """The plural honors the same SPEC §6.8 replay-mode guard as the singular."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active",
    )
    with pytest.raises(NotImplementedError):
        coord.select_recipes_for_execution(
            session, test_id, available_environment=_empty_env(),
            replay_mode="historical",  # type: ignore[arg-type]
        )

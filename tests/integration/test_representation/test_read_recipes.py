"""Plain recipe-read tests for ``SemanticTransactionCoordinator``.

Per Track D-γ.1 + SPEC §10.2. Covers:
  - :meth:`list_active_recipes` returns only ``valid_to IS NULL``
    recipes for the queried ``claim_test_id``.
  - :meth:`get_recipe_latest` returns the current valid version
    or ``None``.
  - :meth:`get_recipe_version` returns a specific historical
    version regardless of ``valid_to`` state.
  - JSONB → Pydantic body deserialization round-trips correctly
    across multiple trigger × recipe kind pairs.
  - :class:`SchemaIncompatibilityError` raised when JSONB
    declares an unknown ``(kind, body_schema_version)``.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from primeqa.test_representation import (
    DataMutationTriggerBody,
    DataRecipeBody,
    ExecutionEnvironmentBody,
    InboundTriggerBody,
    MetadataRecipeBody,
    RecipeRead,
    SchemaIncompatibilityError,
    SemanticTransactionCoordinator,
    TestRecipe,
    UIRecipeBody,
    UITriggerBody,
)

from ._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
    build_minimal_inbound_trigger_body,
    build_minimal_metadata_recipe_body,
    build_minimal_ui_recipe_body,
    build_minimal_ui_trigger_body,
    write_anchor_claim,
)


# ---------------------------------------------------------------------------
# list_active_recipes
# ---------------------------------------------------------------------------


def test_list_active_recipes_empty_for_unknown_claim(session) -> None:
    coord = SemanticTransactionCoordinator()
    result = coord.list_active_recipes(session, uuid4())
    assert result == []


def test_list_active_recipes_returns_v1(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    written = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    recipes = coord.list_active_recipes(session, claim_test_id)
    assert len(recipes) == 1
    assert isinstance(recipes[0], RecipeRead)
    assert recipes[0].recipe_id == written.recipe_id
    assert recipes[0].version_seq == 1
    assert recipes[0].valid_to is None


def test_list_active_recipes_excludes_superseded_versions(
    session,
) -> None:
    """After write_recipe(v2), v1 has valid_to set. The list
    should contain only v2."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    r1 = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    coord.write_recipe(
        session,
        actor="human", recipe_id=r1.recipe_id,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="metadata-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_metadata_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    recipes = coord.list_active_recipes(session, claim_test_id)
    assert len(recipes) == 1
    assert recipes[0].version_seq == 2
    assert recipes[0].recipe_kind == "metadata-recipe"


def test_list_active_recipes_deterministic_order(session) -> None:
    """Multiple recipes for the same claim are sorted by
    recipe_id ascending."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    # Write 3 recipes (different recipe_ids).
    written_ids = []
    for _ in range(3):
        r = coord.write_recipe(
            session,
            actor="human", recipe_id=None,
            claim_test_id=claim_test_id,
            trigger_kind="ui-trigger", recipe_kind="data-recipe",
            causal_initiation=build_minimal_ui_trigger_body(),
            observation_realization=build_minimal_data_recipe_body(),
            execution_environment=(
                build_minimal_execution_environment_body()
            ),
        )
        written_ids.append(r.recipe_id)
    recipes = coord.list_active_recipes(session, claim_test_id)
    returned_ids = [r.recipe_id for r in recipes]
    assert returned_ids == sorted(written_ids)


# ---------------------------------------------------------------------------
# get_recipe_latest
# ---------------------------------------------------------------------------


def test_get_recipe_latest_none_for_unknown_recipe(session) -> None:
    coord = SemanticTransactionCoordinator()
    assert coord.get_recipe_latest(session, uuid4()) is None


def test_get_recipe_latest_returns_current(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    r1 = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    read = coord.get_recipe_latest(session, r1.recipe_id)
    assert read is not None
    assert read.recipe_id == r1.recipe_id
    assert read.version_seq == 1


def test_get_recipe_latest_returns_v2_after_supersession(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    r1 = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    coord.write_recipe(
        session,
        actor="human", recipe_id=r1.recipe_id,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="ui-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_ui_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    read = coord.get_recipe_latest(session, r1.recipe_id)
    assert read.version_seq == 2
    assert read.recipe_kind == "ui-recipe"


# ---------------------------------------------------------------------------
# get_recipe_version
# ---------------------------------------------------------------------------


def test_get_recipe_version_none_for_unknown_pair(session) -> None:
    coord = SemanticTransactionCoordinator()
    assert coord.get_recipe_version(session, uuid4(), 1) is None


def test_get_recipe_version_returns_historical_row(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    r1 = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    coord.write_recipe(
        session,
        actor="human", recipe_id=r1.recipe_id,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="metadata-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_metadata_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    historical = coord.get_recipe_version(session, r1.recipe_id, 1)
    assert historical is not None
    assert historical.version_seq == 1
    assert historical.recipe_kind == "data-recipe"
    assert historical.valid_to is not None  # closed


# ---------------------------------------------------------------------------
# Body deserialization round-trip
# ---------------------------------------------------------------------------


def test_round_trip_deserializes_all_three_recipe_bodies(session) -> None:
    """The three operational-layer bodies on
    :class:`TestRecipe` all round-trip as their typed Pydantic
    classes."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    trigger_body = build_minimal_ui_trigger_body()
    recipe_body = build_minimal_data_recipe_body()
    env_body = build_minimal_execution_environment_body()
    r1 = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=trigger_body,
        observation_realization=recipe_body,
        execution_environment=env_body,
    )
    read = coord.get_recipe_latest(session, r1.recipe_id)
    assert isinstance(read.causal_initiation, UITriggerBody)
    assert isinstance(read.observation_realization, DataRecipeBody)
    assert isinstance(read.execution_environment, ExecutionEnvironmentBody)
    assert read.causal_initiation == trigger_body
    assert read.observation_realization == recipe_body
    assert read.execution_environment == env_body


# ---------------------------------------------------------------------------
# SchemaIncompatibilityError on unknown body schema
# ---------------------------------------------------------------------------


def test_unknown_body_schema_raises_schema_incompatibility(session) -> None:
    """Same contract as claim-side: an unregistered
    (kind, body_schema_version) on any of the three recipe
    JSONB columns raises :class:`SchemaIncompatibilityError`
    at read time."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    recipe_id = uuid4()
    row = TestRecipe(
        recipe_id=recipe_id,
        version_seq=1,
        claim_test_id=claim_test_id,
        claim_version_seq=None,
        trigger_kind="ui-trigger",
        recipe_kind="data-recipe",
        causal_initiation={
            "body_schema_version": 99,  # not in registry
            "kind": "ui-trigger",
        },
        observation_realization={
            "body_schema_version": 1,
            "kind": "data-recipe",
            "api_choice": "rest",
            "identity_context": "system",
            "execution_mechanism": "direct_api",
            "steps": [],
        },
        execution_environment={
            "body_schema_version": 1,
            "kind": "execution_environment",
        },
        priority=0,
        status="generated_unapproved",
    )
    session.add(row)
    session.flush()
    with pytest.raises(SchemaIncompatibilityError):
        coord.get_recipe_latest(session, recipe_id)


# ---------------------------------------------------------------------------
# Round-trip parametrized over multiple (trigger, recipe) pairs
# ---------------------------------------------------------------------------


_PAIRS = [
    (
        "ui-trigger", build_minimal_ui_trigger_body, UITriggerBody,
        "data-recipe", build_minimal_data_recipe_body, DataRecipeBody,
    ),
    (
        "data-mutation-trigger",
        build_minimal_data_mutation_trigger_body, DataMutationTriggerBody,
        "metadata-recipe", build_minimal_metadata_recipe_body,
        MetadataRecipeBody,
    ),
    (
        "inbound-trigger",
        build_minimal_inbound_trigger_body, InboundTriggerBody,
        "ui-recipe", build_minimal_ui_recipe_body, UIRecipeBody,
    ),
]


@pytest.mark.parametrize(
    "trigger_kind,trigger_builder,trigger_class,"
    "recipe_kind,recipe_builder,recipe_class",
    _PAIRS,
)
def test_round_trip_pair(
    session,
    trigger_kind, trigger_builder, trigger_class,
    recipe_kind, recipe_builder, recipe_class,
) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    trig = trigger_builder()
    rcp = recipe_builder()
    r1 = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind=trigger_kind, recipe_kind=recipe_kind,
        causal_initiation=trig,
        observation_realization=rcp,
        execution_environment=build_minimal_execution_environment_body(),
    )
    read = coord.get_recipe_latest(session, r1.recipe_id)
    assert isinstance(read.causal_initiation, trigger_class)
    assert isinstance(read.observation_realization, recipe_class)
    assert read.causal_initiation == trig
    assert read.observation_realization == rcp

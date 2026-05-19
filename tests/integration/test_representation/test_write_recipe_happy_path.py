"""End-to-end happy-path tests for
``SemanticTransactionCoordinator.write_recipe``.

Per Track D-β.3. Each test exercises the full orchestration
(11-step canonical order with steps 7-9 N/A per SPEC §4.5 + §6.4)
against a real PostgreSQL test DB:
  - Pydantic body → JSONB serialization for all three recipe-body
    slots (causal_initiation, observation_realization,
    execution_environment).
  - Cross-body validation (step 6): claim_test_id resolution.
  - Provenance event "recipe_created" inserted.

Parametrized over all 5 trigger-kinds and all 5 recipe-kinds.
"""
from __future__ import annotations

from uuid import UUID

import pytest

from primeqa.test_representation import (
    SemanticTransactionCoordinator,
    TestProvenance,
    TestRecipe,
)

from ._builders import (
    build_minimal_callout_intercept_recipe_body,
    build_minimal_configuration_trigger_body,
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body,
    build_minimal_event_subscription_recipe_body,
    build_minimal_execution_environment_body,
    build_minimal_inbound_trigger_body,
    build_minimal_metadata_recipe_body,
    build_minimal_time_trigger_body,
    build_minimal_ui_recipe_body,
    build_minimal_ui_trigger_body,
    write_anchor_claim,
)


# ---------------------------------------------------------------------------
# Smoke: new recipe, no prior, human actor
# ---------------------------------------------------------------------------


def test_write_new_recipe_returns_v1_generated_unapproved(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    result = coord.write_recipe(
        session,
        actor="human",
        recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger",
        recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    assert result.version_seq == 1
    assert result.status == "generated_unapproved"
    assert isinstance(result.recipe_id, UUID)


def test_write_inserts_test_recipes_row(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    result = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    rows = session.query(TestRecipe).filter(
        TestRecipe.recipe_id == result.recipe_id,
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.version_seq == 1
    assert row.claim_test_id == claim_test_id
    assert row.claim_version_seq is None  # default: logical resolution
    assert row.trigger_kind == "ui-trigger"
    assert row.recipe_kind == "data-recipe"
    assert row.priority == 0
    assert row.status == "generated_unapproved"
    assert row.valid_to is None
    # JSONB round-trip:
    assert row.causal_initiation["kind"] == "ui-trigger"
    assert row.observation_realization["kind"] == "data-recipe"
    assert row.execution_environment["kind"] == "execution_environment"


def test_write_inserts_provenance_event(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    result = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == result.recipe_id,
    ).all()
    assert len(events) == 1
    evt = events[0]
    assert evt.event_kind == "recipe_created"
    assert evt.event_actor == "human"
    assert evt.claim_test_id is None  # recipe events leave this NULL
    assert evt.event_data["actor"] == "human"
    assert evt.event_data["new_version_seq"] == 1
    assert evt.event_data["prior_version_seq"] is None
    assert evt.event_data["new_status"] == "generated_unapproved"
    assert evt.event_data["claim_test_id"] == str(claim_test_id)


# ---------------------------------------------------------------------------
# All 5 trigger-kinds × the matching minimal recipe (parametrized)
# ---------------------------------------------------------------------------


_TRIGGER_BUILDERS = [
    ("inbound-trigger", build_minimal_inbound_trigger_body),
    ("data-mutation-trigger", build_minimal_data_mutation_trigger_body),
    ("ui-trigger", build_minimal_ui_trigger_body),
    ("time-trigger", build_minimal_time_trigger_body),
    ("configuration-trigger", build_minimal_configuration_trigger_body),
]


@pytest.mark.parametrize("trigger_kind,trigger_builder", _TRIGGER_BUILDERS)
def test_each_trigger_kind_writes(
    session, trigger_kind: str, trigger_builder,
) -> None:
    """Every trigger-kind writes a valid recipe row, paired with
    a single (data-recipe) observation-realization shape — the
    cross-product across recipe-kinds is in the next test."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    result = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind=trigger_kind, recipe_kind="data-recipe",
        causal_initiation=trigger_builder(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    assert result.version_seq == 1
    row = session.query(TestRecipe).filter(
        TestRecipe.recipe_id == result.recipe_id,
    ).one()
    assert row.trigger_kind == trigger_kind
    assert row.causal_initiation["kind"] == trigger_kind


# ---------------------------------------------------------------------------
# All 5 recipe-kinds (parametrized)
# ---------------------------------------------------------------------------


_RECIPE_BUILDERS = [
    ("data-recipe", build_minimal_data_recipe_body),
    ("metadata-recipe", build_minimal_metadata_recipe_body),
    ("ui-recipe", build_minimal_ui_recipe_body),
    (
        "event-subscription-recipe",
        build_minimal_event_subscription_recipe_body,
    ),
    (
        "callout-intercept-recipe",
        build_minimal_callout_intercept_recipe_body,
    ),
]


@pytest.mark.parametrize("recipe_kind,recipe_builder", _RECIPE_BUILDERS)
def test_each_recipe_kind_writes(
    session, recipe_kind: str, recipe_builder,
) -> None:
    """Every recipe-kind writes a valid row, paired with a
    single (ui-trigger) trigger shape — the cross-product
    coverage with trigger-kinds is in the previous test."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    result = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind=recipe_kind,
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=recipe_builder(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    assert result.version_seq == 1
    row = session.query(TestRecipe).filter(
        TestRecipe.recipe_id == result.recipe_id,
    ).one()
    assert row.recipe_kind == recipe_kind
    assert row.observation_realization["kind"] == recipe_kind


# ---------------------------------------------------------------------------
# claim_version_seq pinning per SPEC §6.4
# ---------------------------------------------------------------------------


def test_write_recipe_pinned_to_specific_claim_version(session) -> None:
    """Recipe pins to a specific claim version per SPEC §6.4.
    The ``claim_version_seq`` column carries the pinned value."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, claim_v = write_anchor_claim(session, coord)
    result = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        claim_version_seq=claim_v,  # explicit pinning
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    row = session.query(TestRecipe).filter(
        TestRecipe.recipe_id == result.recipe_id,
    ).one()
    assert row.claim_version_seq == claim_v
    # Provenance event records the pinning.
    evt = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == result.recipe_id,
    ).one()
    assert evt.event_data["claim_version_seq"] == claim_v


def test_write_recipe_default_logical_resolution(session) -> None:
    """When ``claim_version_seq`` is omitted, recipe follows
    claim evolution (logical resolution per SPEC §6.4)."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    result = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=claim_test_id,
        # claim_version_seq omitted → defaults to None
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    row = session.query(TestRecipe).filter(
        TestRecipe.recipe_id == result.recipe_id,
    ).one()
    assert row.claim_version_seq is None

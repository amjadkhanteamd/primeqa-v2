"""Versioning + supersession tests for ``write_recipe``.

Per Track D-β.3 + D-057 + SPEC §6.1. Verifies:
  - Sequential recipe versions: v1 closed (valid_to set) when v2
    arrives; v2 inserted with valid_to=NULL.
  - Multi-version chains close all prior versions.
  - S8 rewrites carry the ``recipe_s8_rewrite`` event_kind and
    advance the version_seq even when body content is unchanged
    (per SPEC §7.4 — recipes have no hash; no behavior-preserving
    detection at v1).
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.test_representation import (
    SemanticTransactionCoordinator,
    TestProvenance,
    TestRecipe,
)

from ._builders import (
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
    build_minimal_metadata_recipe_body,
    build_minimal_ui_trigger_body,
    write_anchor_claim,
)


def test_v2_supersedes_v1(session) -> None:
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
    r2 = coord.write_recipe(
        session,
        actor="human", recipe_id=r1.recipe_id,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="metadata-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_metadata_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )

    rows = session.query(TestRecipe).filter(
        TestRecipe.recipe_id == r1.recipe_id,
    ).order_by(TestRecipe.version_seq).all()
    assert [r.version_seq for r in rows] == [1, 2]
    assert rows[0].valid_to is not None
    assert rows[1].valid_to is None
    assert r2.version_seq == 2


def test_two_provenance_events_for_two_versions(session) -> None:
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
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    # event_at is per-transaction NOW() — identical across one test's writes — so
    # it alone under-specifies the order (heap-order flake); tiebreak on the
    # version each event minted.
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == r1.recipe_id,
    ).order_by(TestProvenance.event_at,
               TestProvenance.event_data["new_version_seq"].as_integer()).all()
    assert [e.event_kind for e in events] == [
        "recipe_created", "recipe_edited",
    ]


def test_three_version_chain_closes_all_prior(session) -> None:
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
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    r3 = coord.write_recipe(
        session,
        actor="human", recipe_id=r1.recipe_id,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )

    rows = session.query(TestRecipe).filter(
        TestRecipe.recipe_id == r1.recipe_id,
    ).order_by(TestRecipe.version_seq).all()
    assert [r.version_seq for r in rows] == [1, 2, 3]
    assert sum(1 for r in rows if r.valid_to is None) == 1
    assert rows[-1].valid_to is None
    assert r3.version_seq == 3


def test_s8_rewrite_chain_with_distinct_event_kinds(session) -> None:
    """S8 rewrites a recipe twice. Each version advances; each
    carries ``recipe_s8_rewrite`` per SPEC §4.1 (after the
    initial ``recipe_created``)."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    r1 = coord.write_recipe(
        session,
        actor="s8", recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    coord.write_recipe(
        session,
        actor="s8", recipe_id=r1.recipe_id,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    coord.write_recipe(
        session,
        actor="s8", recipe_id=r1.recipe_id,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    # event_at is per-transaction NOW() — identical across one test's writes — so
    # it alone under-specifies the order (heap-order flake); tiebreak on the
    # version each event minted.
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == r1.recipe_id,
    ).order_by(TestProvenance.event_at,
               TestProvenance.event_data["new_version_seq"].as_integer()).all()
    assert [e.event_kind for e in events] == [
        # First write by S8 still classes as "created" — S8
        # authored the recipe; the rewrite event_kind applies
        # only to subsequent writes by S8.
        "recipe_created",
        "recipe_s8_rewrite",
        "recipe_s8_rewrite",
    ]

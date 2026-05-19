"""Concurrent-write collision tests for ``write_recipe``.

Per Track D-β.3 + SPEC §7.7. Same pattern as
``test_write_claim_concurrent.py``: the compound PK
``(recipe_id, version_seq)`` on ``test_recipes`` is the
substrate's concurrency guard; the second concurrent writer's
INSERT raises :class:`IntegrityError`.

Retry is the caller's responsibility per SPEC §7.7. The
Coordinator's docstring documents this; the documentation test
below confirms the contract is in the public surface.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from primeqa.test_representation import (
    SemanticTransactionCoordinator,
    TestRecipe,
)

from ._builders import (
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
    build_minimal_ui_trigger_body,
    write_anchor_claim,
)


def test_compound_pk_violation_on_duplicate_version_seq(session) -> None:
    """Simulate a concurrent race: v1 is in place; a parallel
    writer pre-inserts a (closed) v2 row at the same
    (recipe_id, version_seq=2) compound PK. The Coordinator's
    own v2 INSERT collides."""
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

    # Parallel writer: insert a v2 with valid_to set to a
    # non-NULL value, so the Coordinator's prior-version query
    # (filtering on valid_to IS NULL) still returns v1 — and the
    # Coordinator's INSERT for version_seq=2 collides with this
    # row on the compound PK.
    parallel_v2 = TestRecipe(
        recipe_id=r1.recipe_id,
        version_seq=2,
        valid_to=datetime.now(timezone.utc),
        claim_test_id=claim_test_id,
        claim_version_seq=None,
        trigger_kind="ui-trigger",
        recipe_kind="data-recipe",
        causal_initiation={
            "body_schema_version": 1, "kind": "ui-trigger",
            "action_type": "button_click",
            "page_context": {"page_kind": "record_detail"},
        },
        observation_realization={
            "body_schema_version": 1, "kind": "data-recipe",
            "api_choice": "rest",
            "identity_context": "system",
            "execution_mechanism": "direct_api",
            "steps": [],
        },
        execution_environment={
            "body_schema_version": 1, "kind": "execution_environment",
        },
        priority=0,
        status="generated_unapproved",
    )
    session.add(parallel_v2)
    session.flush()

    with pytest.raises(IntegrityError):
        coord.write_recipe(
            session,
            actor="human", recipe_id=r1.recipe_id,
            claim_test_id=claim_test_id,
            trigger_kind="ui-trigger", recipe_kind="data-recipe",
            causal_initiation=build_minimal_ui_trigger_body(),
            observation_realization=build_minimal_data_recipe_body(),
            execution_environment=(
                build_minimal_execution_environment_body()
            ),
        )


def test_concurrent_collision_documents_retry_responsibility(
    session,
) -> None:
    """Document the contract: IntegrityError is the substrate's
    explicit signal for concurrent-collision. Callers retry per
    SPEC §7.7. The Coordinator's docstring carries the
    contract."""
    doc = SemanticTransactionCoordinator.write_recipe.__doc__ or ""
    assert "Concurrent-write" in doc or "concurrent" in doc.lower()
    assert (
        "IntegrityError" in doc
        or "compound primary key" in doc.lower()
    )

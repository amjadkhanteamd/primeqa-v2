"""Per-step failure tests for ``write_recipe``.

Per Track D-β.3. The recipe write-flow shares the 11-step
canonical order with claim-side; steps 7-9 are N/A per SPEC §4.5
+ §6.4 (no canonicalization / hash / coverage for recipes).
Step 6 is the cross-body claim-existence check — the substrate's
only logical-FK enforcement in v1 per D-α A6.

Error types mapped to SPEC §4.7.8:
  - Step 1: ``ValueError`` (discriminator outside closed
    taxonomy)
  - Step 2: :class:`SchemaIncompatibilityError`
  - Step 4: :class:`BodyCorruptionError`
  - Step 5: defence-in-depth — unreachable for typed Pydantic
    input
  - Step 6: :class:`OntologyViolationError` (claim_test_id or
    claim_version_seq pinning fails to resolve)
  - Step 10: under current v1 policy, never fails
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    BodyCorruptionError,
    OntologyViolationError,
    SchemaIncompatibilityError,
    SemanticTransactionCoordinator,
)

from ._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
    build_minimal_metadata_recipe_body,
    build_minimal_ui_trigger_body,
    write_anchor_claim,
)


def _row_count(session, table: str) -> int:
    return session.execute(
        text(f"SELECT COUNT(*) FROM {table}"),
    ).scalar()


# ---------------------------------------------------------------------------
# Step 1: discriminator validation
# ---------------------------------------------------------------------------


def test_unknown_trigger_kind_raises_value_error(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    before = _row_count(session, "test_recipes")
    with pytest.raises(ValueError):
        coord.write_recipe(
            session,
            actor="human", recipe_id=None,
            claim_test_id=claim_test_id,
            trigger_kind="bogus-trigger",  # not in taxonomy
            recipe_kind="data-recipe",
            causal_initiation=build_minimal_ui_trigger_body(),
            observation_realization=build_minimal_data_recipe_body(),
            execution_environment=(
                build_minimal_execution_environment_body()
            ),
        )
    assert _row_count(session, "test_recipes") == before


def test_unknown_recipe_kind_raises_value_error(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    with pytest.raises(ValueError):
        coord.write_recipe(
            session,
            actor="human", recipe_id=None,
            claim_test_id=claim_test_id,
            trigger_kind="ui-trigger",
            recipe_kind="bogus-recipe",  # not in taxonomy
            causal_initiation=build_minimal_ui_trigger_body(),
            observation_realization=build_minimal_data_recipe_body(),
            execution_environment=(
                build_minimal_execution_environment_body()
            ),
        )


# ---------------------------------------------------------------------------
# Step 2: body-shape dispatch via the registry
# ---------------------------------------------------------------------------


def test_trigger_class_mismatch_raises_schema_incompat(session) -> None:
    """Pass a DataMutationTriggerBody but declare
    trigger_kind="ui-trigger". The registry returns UITriggerBody
    for ("ui-trigger", 1); the actual body is
    DataMutationTriggerBody → class mismatch at step 2."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    with pytest.raises(SchemaIncompatibilityError):
        coord.write_recipe(
            session,
            actor="human", recipe_id=None,
            claim_test_id=claim_test_id,
            trigger_kind="ui-trigger",  # caller LIES
            recipe_kind="data-recipe",
            causal_initiation=build_minimal_data_mutation_trigger_body(),
            observation_realization=build_minimal_data_recipe_body(),
            execution_environment=(
                build_minimal_execution_environment_body()
            ),
        )


def test_recipe_class_mismatch_raises_schema_incompat(session) -> None:
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    with pytest.raises(SchemaIncompatibilityError):
        coord.write_recipe(
            session,
            actor="human", recipe_id=None,
            claim_test_id=claim_test_id,
            trigger_kind="ui-trigger",
            recipe_kind="data-recipe",  # caller LIES
            causal_initiation=build_minimal_ui_trigger_body(),
            observation_realization=(
                build_minimal_metadata_recipe_body()
            ),
            execution_environment=(
                build_minimal_execution_environment_body()
            ),
        )


# ---------------------------------------------------------------------------
# Step 4: body-row discriminator consistency
# ---------------------------------------------------------------------------


def test_step_4_unreachable_for_typed_input(session) -> None:
    """As in claim-side step 4, Pydantic Literal type-checking
    on ``kind`` fields prevents constructing a body with an
    inconsistent internal kind. Step 4 is defence-in-depth;
    practical mismatches are caught at step 2 (class mismatch)."""
    pytest.skip(
        "step 4 is defence-in-depth for typed Pydantic input; "
        "step 2's class-mismatch path covers the practical case"
    )


# ---------------------------------------------------------------------------
# Step 5: cross-layer ontology (defence in depth)
# ---------------------------------------------------------------------------


def test_step_5_unreachable_for_typed_input(session) -> None:
    """Pydantic's :data:`OperationalRef` union resolution
    prevents :class:`IdentityBearingRef` from appearing in
    operational-layer bodies at construction time. Step 5's
    defensive walk in the Coordinator is unreachable for typed
    input."""
    pytest.skip(
        "step 5 is defence-in-depth for typed Pydantic input; "
        "OperationalRef union resolution covers the practical case"
    )


# ---------------------------------------------------------------------------
# Step 6: cross-body validation — REAL failure paths
# ---------------------------------------------------------------------------


def test_nonexistent_claim_test_id_raises_ontology_violation(
    session,
) -> None:
    """Step 6 — the substrate's logical-FK enforcement per
    D-α A6. A recipe referencing a claim_test_id with no
    matching row is rejected."""
    coord = SemanticTransactionCoordinator()
    before = _row_count(session, "test_recipes")
    fake_claim = uuid4()
    with pytest.raises(OntologyViolationError) as exc_info:
        coord.write_recipe(
            session,
            actor="human", recipe_id=None,
            claim_test_id=fake_claim,  # no claim row exists
            trigger_kind="ui-trigger", recipe_kind="data-recipe",
            causal_initiation=build_minimal_ui_trigger_body(),
            observation_realization=build_minimal_data_recipe_body(),
            execution_environment=(
                build_minimal_execution_environment_body()
            ),
        )
    err = exc_info.value
    assert err.context["field_path"] == "claim_test_id"
    assert str(fake_claim) in str(err)
    assert _row_count(session, "test_recipes") == before


def test_nonexistent_claim_version_pinning_raises_ontology_violation(
    session,
) -> None:
    """Step 6 with version pinning: claim_test_id resolves but
    the explicit claim_version_seq doesn't. SPEC §6.4 says
    pinning is reproducibility-critical; bad pins MUST fail."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, claim_v = write_anchor_claim(session, coord)
    before = _row_count(session, "test_recipes")
    with pytest.raises(OntologyViolationError) as exc_info:
        coord.write_recipe(
            session,
            actor="human", recipe_id=None,
            claim_test_id=claim_test_id,
            claim_version_seq=claim_v + 99,  # not a real version
            trigger_kind="ui-trigger", recipe_kind="data-recipe",
            causal_initiation=build_minimal_ui_trigger_body(),
            observation_realization=build_minimal_data_recipe_body(),
            execution_environment=(
                build_minimal_execution_environment_body()
            ),
        )
    err = exc_info.value
    assert err.context["field_path"] == "(claim_test_id, claim_version_seq)"
    assert _row_count(session, "test_recipes") == before


# ---------------------------------------------------------------------------
# Step 10: authority — current policy never rejects
# ---------------------------------------------------------------------------


def test_step_10_recipe_authority_never_rejects_v1(session) -> None:
    """SPEC §7.4 conservative default: all actors are allowed
    to write recipes. The authority check is in place for
    future policy evolution (e.g., behavior-preserving detection
    that would allow no-op skips), but at v1 every actor
    succeeds."""
    coord = SemanticTransactionCoordinator()
    claim_test_id, _ = write_anchor_claim(session, coord)
    for actor in ("human", "s3", "s8"):
        result = coord.write_recipe(
            session,
            actor=actor, recipe_id=None,
            claim_test_id=claim_test_id,
            trigger_kind="ui-trigger", recipe_kind="data-recipe",
            causal_initiation=build_minimal_ui_trigger_body(),
            observation_realization=build_minimal_data_recipe_body(),
            execution_environment=(
                build_minimal_execution_environment_body()
            ),
        )
        assert result.status == "generated_unapproved"

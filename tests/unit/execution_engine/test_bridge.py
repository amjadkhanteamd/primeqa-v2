"""Unit tests for the S4 recipe->plan bridge (D-108 slice 1) — pure, no PG.

The bridge turns substrate-2's typed ``RecipeRead`` into a
``MetadataInspectionPlan`` — the semantic, S1-edge-vocabulary contract slices
2-4 consume. These tests exercise the full path a real run takes: S3 *emits* an
inspection recipe (``generation.emission``), the bodies round-trip through
JSONB and back via S2's body registry (exactly what
``Coordinator._deserialize_body`` does on read), the decoded bodies ride a
``RecipeRead``, and the bridge projects them into a plan.

No DB, no live org, no edge->live-read translation (that is slice 2's
executor). Given the same ``RecipeRead`` the bridge always yields the same
plan.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.execution_engine import (
    MetadataInspectionPlan,
    PlannedAssertion,
    PlannedRead,
    PlanTranslationError,
    build_metadata_inspection_plan,
)
from types import SimpleNamespace

from primeqa.generation.emission import (
    GroundedEmission,
    _Endpoint,
    _inspection_recipe,
    author_emission,
)
from primeqa.test_representation.coordinator import RecipeRead
from primeqa.test_representation.models.primitives import AssertionPredicate
from primeqa.test_representation.models.recipes.metadata_recipe import (
    AssertStep,
    MetadataRecipeBody,
    ReadMetadataStep,
    RetrieveStep,
)
from primeqa.test_representation.models.references import LogicalRef, PinnedRef
from primeqa.test_representation.models.registry import get_body_model

_NOW = datetime(2026, 5, 27, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Builders — real emitted recipes + the S2 read round-trip
# ---------------------------------------------------------------------------

def _emit_negative():
    """The metadata-inspection recipe shape S3 historically emitted for a
    prohibition (read a VR APPLIES_TO the subject + assert it surfaces). D-293
    removed that emission — prohibitions now emit a behavioural reject recipe —
    but the bridge is a generic inspection decoder, so this fixture is built
    directly from the inspection-recipe builder (the identical read-subject /
    capture-APPLIES_TO / assert-exists shape, no longer routed via a claim)."""
    trigger, recipe, env = _inspection_recipe(
        read_entity_type="Object", read_external_id="Lead", capture_field="APPLIES_TO",
        env_detail="read Lead metadata to verify a validation rule applies")
    return SimpleNamespace(
        causal_initiation=trigger, observation_realization=recipe,
        execution_environment=env)


def _emit_config():
    """The D-098 config metadata-relationship inspection recipe."""
    return author_emission(GroundedEmission(
        archetype="configuration",
        claim_kind="metadata-relationship-claim",
        edge_type="APPLIES_TO",
        version_seq=7,
        source=_Endpoint(
            entity_id=uuid4(), entity_type="ValidationRule",
            external_id="Lead.Require_Reason",
        ),
        target=_Endpoint(
            entity_id=uuid4(), entity_type="Object", external_id="Lead",
        ),
        requirement_excerpt="A validation rule applies to Lead.",
    ))


def _roundtrip(body):
    """Dump a body to JSONB + re-decode via the registry — exactly what S2's
    Coordinator does on read (``_deserialize_body``). Proves the bridge
    operates on registry-decoded bodies, not the in-memory authoring objects."""
    dumped = body.model_dump(mode="json")
    cls = get_body_model(dumped["kind"], dumped["body_schema_version"])
    return cls.model_validate(dumped)


def _recipe_read(
    bundle,
    *,
    recipe_id=None,
    version_seq=3,
    claim_test_id=None,
    claim_version_seq=None,
    trigger_kind="inspection-trigger",
    recipe_kind="metadata-recipe",
    causal_initiation=None,
    observation_realization=None,
    execution_environment=None,
    roundtrip=True,
):
    """Wrap an emission bundle's bodies in a ``RecipeRead`` the way the
    Coordinator hands one to S4. Bodies round-trip through JSONB by default;
    overrides let error-path tests substitute a custom body."""
    causal = causal_initiation if causal_initiation is not None else bundle.causal_initiation
    obs = observation_realization if observation_realization is not None else bundle.observation_realization
    env = execution_environment if execution_environment is not None else bundle.execution_environment
    if roundtrip:
        causal, obs, env = _roundtrip(causal), _roundtrip(obs), _roundtrip(env)
    return RecipeRead(
        recipe_id=recipe_id or uuid4(),
        version_seq=version_seq,
        valid_from=_NOW,
        valid_to=None,
        claim_test_id=claim_test_id or uuid4(),
        claim_version_seq=claim_version_seq,
        trigger_kind=trigger_kind,
        recipe_kind=recipe_kind,
        causal_initiation=causal,
        observation_realization=obs,
        execution_environment=env,
        priority=0,
        status="generated_unapproved",
        created_at=_NOW,
        updated_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Happy path — emitted recipe -> expected semantic plan
# ---------------------------------------------------------------------------

def test_decodes_emitted_prohibition_inspection_to_plan():
    recipe = _recipe_read(_emit_negative())
    plan = build_metadata_inspection_plan(recipe)

    assert isinstance(plan, MetadataInspectionPlan)
    assert plan.api_choice == "metadata_api"
    assert len(plan.steps) == 2

    read, assertion = plan.steps
    assert isinstance(read, PlannedRead)
    assert read.step_id == "read-subject"
    assert isinstance(read.target_entity, LogicalRef)
    assert read.target_entity.entity_type == "Object"
    assert read.target_entity.external_id == "Lead"
    # S1-edge vocabulary survives verbatim — NOT translated to a live query.
    assert read.fields_to_capture == ("APPLIES_TO",)

    assert isinstance(assertion, PlannedAssertion)
    assert assertion.step_id == "assert-edge"
    assert assertion.predicate.predicate == "exists"
    assert assertion.predicate.subject_ref == "read-subject"


def test_decodes_emitted_config_inspection_to_plan():
    recipe = _recipe_read(_emit_config())
    plan = build_metadata_inspection_plan(recipe)

    read, assertion = plan.steps
    assert isinstance(read, PlannedRead)
    # config reads the *source* endpoint and captures the edge_type.
    assert read.target_entity.entity_type == "ValidationRule"
    assert read.target_entity.external_id == "Lead.Require_Reason"
    assert read.fields_to_capture == ("APPLIES_TO",)
    assert isinstance(assertion, PlannedAssertion)


def test_plan_carries_recipe_and_claim_identity():
    rid, ctid = uuid4(), uuid4()
    recipe = _recipe_read(
        _emit_negative(), recipe_id=rid, version_seq=5,
        claim_test_id=ctid, claim_version_seq=2)
    plan = build_metadata_inspection_plan(recipe)

    # Identity is carried (for slice-4 posture), not resolved (no claim fetch).
    assert plan.recipe_id == rid
    assert plan.recipe_version_seq == 5
    assert plan.claim_test_id == ctid
    assert plan.claim_version_seq == 2


def test_steps_are_ordered_read_then_assert():
    plan = build_metadata_inspection_plan(_recipe_read(_emit_negative()))
    assert [s.kind for s in plan.steps] == ["read", "assert"]


# ---------------------------------------------------------------------------
# Shape gates — recipes the slice-1 bridge refuses
# ---------------------------------------------------------------------------

def test_rejects_non_metadata_recipe_kind():
    recipe = _recipe_read(_emit_negative(), recipe_kind="data-recipe")
    with pytest.raises(PlanTranslationError, match="recipe_kind"):
        build_metadata_inspection_plan(recipe)


def test_rejects_non_inspection_trigger():
    recipe = _recipe_read(
        _emit_negative(), trigger_kind="data-mutation-trigger")
    with pytest.raises(PlanTranslationError, match="trigger_kind"):
        build_metadata_inspection_plan(recipe)


def test_rejects_metadata_write_mode():
    # A write-mode recipe with read+assert is constructible (the body
    # validator only forbids DeployStep in read mode); the bridge refuses it
    # because the inspection vertical is read-only.
    write_body = MetadataRecipeBody(
        mode="metadata_write",
        api_choice="metadata_api",
        steps=[
            ReadMetadataStep(
                step_id="read-subject",
                target_entity=LogicalRef(entity_type="Object", external_id="Lead"),
                fields_to_capture=["APPLIES_TO"],
            ),
            AssertStep(
                step_id="assert-edge",
                predicate=AssertionPredicate(subject_ref="read-subject", predicate="exists"),
            ),
        ],
    )
    recipe = _recipe_read(_emit_negative(), observation_realization=write_body)
    with pytest.raises(PlanTranslationError, match="metadata_read"):
        build_metadata_inspection_plan(recipe)


def test_rejects_pinned_read_target():
    # D-099.3: an inspection re-reads *current* state -> the read target must
    # be logical, never pinned.
    pinned_body = MetadataRecipeBody(
        mode="metadata_read",
        api_choice="metadata_api",
        steps=[
            ReadMetadataStep(
                step_id="read-subject",
                target_entity=PinnedRef(
                    entity_type="Object", entity_id=uuid4(),
                    version_seq=7, external_id="Lead"),
                fields_to_capture=["APPLIES_TO"],
            ),
            AssertStep(
                step_id="assert-edge",
                predicate=AssertionPredicate(subject_ref="read-subject", predicate="exists"),
            ),
        ],
    )
    recipe = _recipe_read(_emit_negative(), observation_realization=pinned_body)
    with pytest.raises(PlanTranslationError, match="LogicalRef"):
        build_metadata_inspection_plan(recipe)


def test_rejects_retrieve_step():
    # RetrieveStep is allowed in read mode by the body validator, but is out
    # of the slice-1 inspection vertical (only read + assert are planned).
    retrieve_body = MetadataRecipeBody(
        mode="metadata_read",
        api_choice="metadata_api",
        steps=[
            ReadMetadataStep(
                step_id="read-subject",
                target_entity=LogicalRef(entity_type="Object", external_id="Lead"),
                fields_to_capture=["APPLIES_TO"],
            ),
            RetrieveStep(
                step_id="grab",
                target_entity=LogicalRef(entity_type="Object", external_id="Lead"),
                into_field="snapshot",
            ),
            AssertStep(
                step_id="assert-edge",
                predicate=AssertionPredicate(subject_ref="read-subject", predicate="exists"),
            ),
        ],
    )
    recipe = _recipe_read(_emit_negative(), observation_realization=retrieve_body)
    with pytest.raises(PlanTranslationError, match="RetrieveStep"):
        build_metadata_inspection_plan(recipe)


def test_rejects_plan_with_no_assert():
    read_only = MetadataRecipeBody(
        mode="metadata_read",
        api_choice="metadata_api",
        steps=[
            ReadMetadataStep(
                step_id="read-subject",
                target_entity=LogicalRef(entity_type="Object", external_id="Lead"),
                fields_to_capture=["APPLIES_TO"],
            ),
        ],
    )
    recipe = _recipe_read(_emit_negative(), observation_realization=read_only)
    with pytest.raises(PlanTranslationError, match="no assert"):
        build_metadata_inspection_plan(recipe)


def test_rejects_plan_with_no_read():
    assert_only = MetadataRecipeBody(
        mode="metadata_read",
        api_choice="tooling_api",
        steps=[
            AssertStep(
                step_id="assert-edge",
                predicate=AssertionPredicate(subject_ref="read-subject", predicate="exists"),
            ),
        ],
    )
    recipe = _recipe_read(_emit_negative(), observation_realization=assert_only)
    with pytest.raises(PlanTranslationError, match="no read"):
        build_metadata_inspection_plan(recipe)


def test_translation_error_carries_recipe_id():
    rid = uuid4()
    recipe = _recipe_read(
        _emit_negative(), recipe_id=rid, recipe_kind="ui-recipe")
    with pytest.raises(PlanTranslationError) as exc:
        build_metadata_inspection_plan(recipe)
    assert exc.value.recipe_id == rid


# ---------------------------------------------------------------------------
# Plan-model structure
# ---------------------------------------------------------------------------

def test_plan_step_kind_defaults_and_frozen():
    read = PlannedRead(
        step_id="r", target_entity=LogicalRef(entity_type="Object", external_id="Lead"),
        fields_to_capture=("APPLIES_TO",))
    assertion = PlannedAssertion(
        step_id="a", predicate=AssertionPredicate(subject_ref="r", predicate="exists"))
    assert read.kind == "read"
    assert assertion.kind == "assert"
    # frozen — the contract is immutable once built.
    with pytest.raises(Exception):
        read.step_id = "mutated"  # type: ignore[misc]

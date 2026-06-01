"""The S2-recipe -> executable-plan bridge (SPEC §2.1, first vertical §5).

Slice 1 of the metadata-inspection vertical (D-108). Takes the **typed** read
of an S2 recipe — a :class:`RecipeRead` from substrate-2's Coordinator — and
resolves it into a :class:`MetadataInspectionPlan`, the semantic executable
plan slices 2-4 consume.

**Why ``RecipeRead`` and not a raw row.** Substrate-2's Coordinator *is* S2's
outward read surface: consuming substrates read recipes through it, and it
guarantees JSONB->Pydantic deserialization via the body registry
(``Coordinator._deserialize_body`` / ``_hydrate_recipe_row``). The
already-built ``select_recipe_for_execution`` returns exactly this
``RecipeRead`` — it is S4's selection front door. So the JSONB *decode* the
SPEC describes happens at the S2 boundary; S4 never re-implements it. The
bridge's job is the layer above decode: **narrow** the generic ``BodyBase``
fields to the concrete metadata-inspection types, **validate** the recipe is a
shape this vertical can run, and **project** the ordered read + assert steps
into the S4-owned plan.

The bridge is pure: no DB, no live org, no edge->live-read translation (that
is slice 2). Given the same ``RecipeRead`` it always yields the same plan.
"""
from __future__ import annotations

from primeqa.execution_engine.errors import PlanTranslationError
from primeqa.execution_engine.plan import (
    DataRecipePlan,
    MetadataInspectionPlan,
    PlannedAssertion,
    PlannedCreate,
    PlannedDataRead,
    PlannedRead,
    PlanStep,
)
from primeqa.test_representation.coordinator import RecipeRead
from primeqa.test_representation.models.environment import (
    ExecutionEnvironmentBody,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    AssertStep as DataAssertStep,
    CreateStep,
    DataRecipeBody,
    ReadStep,
)
from primeqa.test_representation.models.recipes.metadata_recipe import (
    AssertStep,
    DeployStep,
    MetadataRecipeBody,
    ReadMetadataStep,
    RetrieveStep,
)
from primeqa.test_representation.models.references import LogicalRef
from primeqa.test_representation.models.triggers.data_mutation import (
    DataMutationTriggerBody,
)
from primeqa.test_representation.models.triggers.inspection import (
    InspectionTriggerBody,
)

# The first vertical (SPEC §5, F3): an inspection-trigger paired with a
# read-only metadata-recipe. The bridge validates this exact shape; other
# (trigger_kind, recipe_kind) pairings are later verticals / later bridges.
_METADATA_RECIPE_KIND = "metadata-recipe"
_INSPECTION_TRIGGER_KIND = "inspection-trigger"
_READ_ONLY_MODE = "metadata_read"

# The behavioral-negative vertical (SPEC §7, D-110.2): a data-mutation-trigger
# paired with a data-recipe whose single step is a create the org rejects.
_DATA_RECIPE_KIND = "data-recipe"
_DATA_MUTATION_TRIGGER_KIND = "data-mutation-trigger"


def build_metadata_inspection_plan(recipe: RecipeRead) -> MetadataInspectionPlan:
    """Resolve an S2 ``RecipeRead`` into a :class:`MetadataInspectionPlan`.

    Validates that ``recipe`` is a metadata-inspection shape (inspection
    trigger + read-only metadata recipe), narrows its bodies, and projects its
    ordered ``ReadMetadataStep`` / ``AssertStep`` sequence into S4-owned plan
    steps in S1-edge vocabulary.

    Raises :class:`PlanTranslationError` if ``recipe`` is not a shape this
    slice can plan (the message names the specific mismatch).
    """
    rid = recipe.recipe_id

    # 1. recipe_kind gate — this is the metadata-recipe bridge.
    if recipe.recipe_kind != _METADATA_RECIPE_KIND:
        raise PlanTranslationError(
            f"metadata-inspection bridge expects recipe_kind="
            f"{_METADATA_RECIPE_KIND!r}; got {recipe.recipe_kind!r}",
            recipe_id=rid,
        )

    # 2. trigger gate — the first vertical is the inspection trigger.
    if recipe.trigger_kind != _INSPECTION_TRIGGER_KIND:
        raise PlanTranslationError(
            f"metadata-inspection bridge expects trigger_kind="
            f"{_INSPECTION_TRIGGER_KIND!r}; got {recipe.trigger_kind!r}",
            recipe_id=rid,
        )
    if not isinstance(recipe.causal_initiation, InspectionTriggerBody):
        raise PlanTranslationError(
            f"causal_initiation must decode to an InspectionTriggerBody; "
            f"got {type(recipe.causal_initiation).__name__}",
            recipe_id=rid,
        )

    # 3. observation_realization must be a metadata recipe body.
    body = recipe.observation_realization
    if not isinstance(body, MetadataRecipeBody):
        raise PlanTranslationError(
            f"observation_realization must decode to a MetadataRecipeBody; "
            f"got {type(body).__name__}",
            recipe_id=rid,
        )

    # 4. read-only — the inspection vertical re-reads + asserts; it never
    #    deploys (metadata_write / DeployStep is a later increment).
    if body.mode != _READ_ONLY_MODE:
        raise PlanTranslationError(
            f"metadata-inspection bridge handles mode={_READ_ONLY_MODE!r} "
            f"only; got mode={body.mode!r}",
            recipe_id=rid,
        )

    # 5. environment — defensive type narrowing (capability *matching* is the
    #    Coordinator's select_recipe_for_execution, upstream of the bridge;
    #    here we only confirm the body decoded to the expected shape).
    if not isinstance(recipe.execution_environment, ExecutionEnvironmentBody):
        raise PlanTranslationError(
            f"execution_environment must decode to an ExecutionEnvironmentBody; "
            f"got {type(recipe.execution_environment).__name__}",
            recipe_id=rid,
        )

    # 6. project steps, preserving order (an assertion may reference a read
    #    that precedes it; the executor resolves subject_ref at run time).
    steps: tuple[PlanStep, ...] = tuple(
        _plan_step(step, recipe_id=rid) for step in body.steps
    )

    # 7. structural floor — a grounded inspection reads *and* asserts. A plan
    #    with no read has nothing to capture; with no assert it renders no
    #    outcome. Either is a malformed inspection.
    if not any(isinstance(s, PlannedRead) for s in steps):
        raise PlanTranslationError(
            "metadata-inspection plan has no read step (nothing to capture)",
            recipe_id=rid,
        )
    if not any(isinstance(s, PlannedAssertion) for s in steps):
        raise PlanTranslationError(
            "metadata-inspection plan has no assert step (no outcome to "
            "evaluate)",
            recipe_id=rid,
        )

    return MetadataInspectionPlan(
        recipe_id=recipe.recipe_id,
        recipe_version_seq=recipe.version_seq,
        claim_test_id=recipe.claim_test_id,
        claim_version_seq=recipe.claim_version_seq,
        api_choice=body.api_choice,
        steps=steps,
    )


def _plan_step(step: object, *, recipe_id) -> PlanStep:
    """Project one metadata-recipe step into its plan-step form.

    ``ReadMetadataStep`` -> :class:`PlannedRead` (narrowing the read target to
    a :class:`LogicalRef`); ``AssertStep`` -> :class:`PlannedAssertion`.
    ``RetrieveStep`` / ``DeployStep`` are out of the slice-1 vertical and
    raise.
    """
    if isinstance(step, ReadMetadataStep):
        if not isinstance(step.target_entity, LogicalRef):
            raise PlanTranslationError(
                f"metadata-inspection read {step.step_id!r} must target a "
                f"LogicalRef so the executor re-reads current state "
                f"(D-099.3); got a {type(step.target_entity).__name__}",
                recipe_id=recipe_id,
            )
        return PlannedRead(
            step_id=step.step_id,
            target_entity=step.target_entity,
            fields_to_capture=tuple(step.fields_to_capture),
        )

    if isinstance(step, AssertStep):
        return PlannedAssertion(
            step_id=step.step_id,
            predicate=step.predicate,
        )

    if isinstance(step, RetrieveStep):
        raise PlanTranslationError(
            f"RetrieveStep {step.step_id!r} is not part of the metadata-"
            f"inspection vertical (slice 1); only read + assert are planned",
            recipe_id=recipe_id,
        )

    if isinstance(step, DeployStep):
        raise PlanTranslationError(
            f"DeployStep {step.step_id!r} is metadata_write; the inspection "
            f"vertical is read-only",
            recipe_id=recipe_id,
        )

    raise PlanTranslationError(
        f"unsupported metadata-recipe step type: {type(step).__name__}",
        recipe_id=recipe_id,
    )


# ---------------------------------------------------------------------------
# The data-recipe behavioral-negative bridge (D-110.2) — parallel to the
# inspection bridge above. Same read-through-Coordinator seam (a typed
# ``RecipeRead``, no raw-JSONB re-decode); a different shape projected (a
# create + expect-rejection, not a read + assert).
# ---------------------------------------------------------------------------

def build_data_recipe_plan(recipe: RecipeRead) -> DataRecipePlan:
    """Resolve an S2 ``RecipeRead`` into a :class:`DataRecipePlan` — both data
    verticals: the behavioral negative (D-110.2) and the positive
    create-and-verify (D-115 side B).

    Validates the common envelope (a data-mutation trigger + a data-recipe body
    beginning with a ``CreateStep`` on a :class:`LogicalRef`), then dispatches on
    the first create's ``expect_rejection``: **set** → the thinnest behavioral
    negative (a single create-rejected step); **absent** → the positive triple
    (``CreateStep`` → ``ReadStep`` → ``AssertStep``). Narrows + projects each
    shape's steps into the S4-owned plan.

    Raises :class:`PlanTranslationError` if ``recipe`` is not a shape this bridge
    can plan (the message names the specific mismatch). Multi-step / provisioned
    negatives and multi-step positives are deferred (D-110 / D-115).
    """
    rid = recipe.recipe_id

    # 1. recipe_kind gate — this is the data-recipe bridge.
    if recipe.recipe_kind != _DATA_RECIPE_KIND:
        raise PlanTranslationError(
            f"data-recipe bridge expects recipe_kind={_DATA_RECIPE_KIND!r}; "
            f"got {recipe.recipe_kind!r}",
            recipe_id=rid,
        )

    # 2. trigger gate — the behavioral negative fires on a data mutation.
    if recipe.trigger_kind != _DATA_MUTATION_TRIGGER_KIND:
        raise PlanTranslationError(
            f"data-recipe bridge expects trigger_kind="
            f"{_DATA_MUTATION_TRIGGER_KIND!r}; got {recipe.trigger_kind!r}",
            recipe_id=rid,
        )
    if not isinstance(recipe.causal_initiation, DataMutationTriggerBody):
        raise PlanTranslationError(
            f"causal_initiation must decode to a DataMutationTriggerBody; "
            f"got {type(recipe.causal_initiation).__name__}",
            recipe_id=rid,
        )

    # 3. observation_realization must be a data recipe body.
    body = recipe.observation_realization
    if not isinstance(body, DataRecipeBody):
        raise PlanTranslationError(
            f"observation_realization must decode to a DataRecipeBody; "
            f"got {type(body).__name__}",
            recipe_id=rid,
        )

    # 4. environment — defensive type narrowing (capability matching is the
    #    Coordinator's, upstream of the bridge).
    if not isinstance(recipe.execution_environment, ExecutionEnvironmentBody):
        raise PlanTranslationError(
            f"execution_environment must decode to an ExecutionEnvironmentBody; "
            f"got {type(recipe.execution_environment).__name__}",
            recipe_id=rid,
        )

    # 5. common shape floor — both verticals begin with a CreateStep on a
    #    LogicalRef target (operational bodies are logical by default). The
    #    first create's expect_rejection discriminates negative vs positive.
    if not body.steps or not isinstance(body.steps[0], CreateStep):
        got = "an empty step list" if not body.steps else type(body.steps[0]).__name__
        raise PlanTranslationError(
            f"a data-recipe must begin with a CreateStep; got {got}",
            recipe_id=rid,
        )
    create = body.steps[0]
    if not isinstance(create.target_object, LogicalRef):
        raise PlanTranslationError(
            f"create {create.step_id!r} must target a LogicalRef (the sobject by "
            f"name); got a {type(create.target_object).__name__}",
            recipe_id=rid,
        )

    # 6. dispatch on expect_rejection → project the vertical's steps.
    if create.expect_rejection is not None:
        steps = _project_negative(body.steps, recipe_id=rid)
    else:
        steps = _project_positive(body.steps, recipe_id=rid)

    return DataRecipePlan(
        recipe_id=recipe.recipe_id,
        recipe_version_seq=recipe.version_seq,
        claim_test_id=recipe.claim_test_id,
        claim_version_seq=recipe.claim_version_seq,
        api_choice=body.api_choice,
        steps=steps,
    )


def _project_negative(steps, *, recipe_id) -> tuple:
    """Project the behavioral negative (D-110.2): the thinnest negative is a
    single create-rejected step. Multi-step / provisioned negatives deferred.
    ``steps[0]`` is already validated as a ``CreateStep`` on a ``LogicalRef``."""
    if len(steps) != 1:
        raise PlanTranslationError(
            f"data-recipe behavioral-negative vertical plans a single "
            f"create-rejected step; got {len(steps)} steps "
            f"(multi-step / provisioned negatives are deferred)",
            recipe_id=recipe_id,
        )
    create = steps[0]
    return (
        PlannedCreate(
            step_id=create.step_id,
            target_object=create.target_object,
            field_values=dict(create.field_values),
            expect_rejection=create.expect_rejection,
        ),
    )


def _project_positive(steps, *, recipe_id) -> tuple:
    """Project the positive create-and-verify (D-115 side B): a ``CreateStep``
    (no expect_rejection) → ``ReadStep`` → ``AssertStep``. The slice plans
    exactly this triple; multi-step / multi-read positives are deferred.
    ``steps[0]`` is already validated as a ``CreateStep`` on a ``LogicalRef``."""
    if (len(steps) != 3
            or not isinstance(steps[1], ReadStep)
            or not isinstance(steps[2], DataAssertStep)):
        shape = ", ".join(type(s).__name__ for s in steps) or "(empty)"
        raise PlanTranslationError(
            f"positive data-recipe (a create with no expect_rejection) must be "
            f"CreateStep -> ReadStep -> AssertStep; got [{shape}] "
            f"(multi-step positives are deferred)",
            recipe_id=recipe_id,
        )
    create, read, assertion = steps
    if not isinstance(read.target, LogicalRef):
        raise PlanTranslationError(
            f"read {read.step_id!r} must target a LogicalRef (the FROM object by "
            f"name); got a {type(read.target).__name__}",
            recipe_id=recipe_id,
        )
    return (
        PlannedCreate(
            step_id=create.step_id,
            target_object=create.target_object,
            field_values=dict(create.field_values),
            expect_rejection=None,
        ),
        PlannedDataRead(
            step_id=read.step_id,
            target=read.target,
            soql=read.soql,
            fields_to_capture=tuple(read.fields_to_capture),
        ),
        PlannedAssertion(
            step_id=assertion.step_id,
            predicate=assertion.predicate,
        ),
    )

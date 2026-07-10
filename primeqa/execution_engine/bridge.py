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
    PlannedApprovalAction,
    PlannedAssertion,
    PlannedCreate,
    PlannedDataRead,
    PlannedDelete,
    PlannedRead,
    PlannedUpdate,
    PlanStep,
)
from primeqa.test_representation.coordinator import RecipeRead
from primeqa.test_representation.models.environment import (
    ExecutionEnvironmentBody,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    ApprovalActionStep,
    AssertStep as DataAssertStep,
    CreateStep,
    DataRecipeBody,
    DeleteStep,
    ReadStep,
    UpdateStep,
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
        # D-224: the edge's far endpoint follows the same D-099.3 discipline.
        if step.edge_target is not None and not isinstance(step.edge_target,
                                                           LogicalRef):
            raise PlanTranslationError(
                f"metadata-inspection read {step.step_id!r} edge_target must "
                f"be a LogicalRef; got a {type(step.edge_target).__name__}",
                recipe_id=recipe_id,
            )
        return PlannedRead(
            step_id=step.step_id,
            target_entity=step.target_entity,
            fields_to_capture=tuple(step.fields_to_capture),
            edge_target=step.edge_target,
            edge_qualifier=step.edge_qualifier,
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
    the **rejection-bearing step** (S2 guarantees at most one): any step flagged
    → a behavioral negative (1-step create-rejected, D-110.2, or 2-step setup
    create → rejected update/delete, D-203); none → the positive triple
    (``CreateStep`` → ``ReadStep`` → ``AssertStep``, D-115). Narrows + projects
    each shape's steps into the S4-owned plan.

    Raises :class:`PlanTranslationError` if ``recipe`` is not a shape this bridge
    can plan (the message names the specific mismatch). N-step negatives and
    multi-step positives are deferred (5b-2).
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

    # 6. dispatch on the REJECTION-BEARING step (S2 guarantees at most one,
    #    D-110.1): any step flagged → a behavioral negative (1-step
    #    create-rejected, D-110.2, or 2-step setup + rejected mutation, D-203);
    #    none flagged → the positive triple.
    if any(getattr(s, "expect_rejection", None) is not None for s in body.steps):
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
    """Project a behavioral negative — exactly two shapes (plus the D-333
    approval-arc interposition), every other fails loud:

      - **1-step** (D-110.2): a single ``CreateStep`` carrying
        ``expect_rejection`` → ``(PlannedCreate,)``;
      - **2-step** (D-203): a setup ``CreateStep`` (no expectation) followed by
        an ``UpdateStep`` / ``DeleteStep`` carrying ``expect_rejection`` →
        ``(PlannedCreate, PlannedUpdate|PlannedDelete)``. The mutation's
        ``target`` must name the same object as the setup's ``target_object``
        (the positional binding the executor resolves — D-203).
      - **D-333**: the 2-step shape may interpose ``ApprovalActionStep × K``
        (K ≥ 1, contiguous) between the setup create and the rejected
        mutation — the approval-action arc. Each projects to a
        :class:`PlannedApprovalAction` bound to the setup create.

    ``steps[0]`` is already validated as a ``CreateStep`` on a ``LogicalRef``.
    """
    create = steps[0]

    if len(steps) == 1:
        if create.expect_rejection is None:
            raise PlanTranslationError(
                "a 1-step behavioral negative must carry expect_rejection on "
                "its create step; got none",
                recipe_id=recipe_id,
            )
        return (
            PlannedCreate(
                step_id=create.step_id,
                target_object=create.target_object,
                field_values=dict(create.field_values),
                expect_rejection=create.expect_rejection,
            ),
        )

    # D-333: peel the approval-action arc — contiguous ApprovalActionSteps
    # immediately after the setup create, before the rejected mutation.
    # VR05 arc: an ENTRY UpdateStep (expect_acceptance, no expect_rejection)
    # may also interpose — the org's own transition establishing the prior
    # state the mutation is then rejected under (a direct create into a gated
    # state would bypass the org's controls). Same staging semantics as the
    # arc: the entry must SUCCEED or the prohibition was never exercised.
    arc = []
    entries = []
    n = 1
    while n < len(steps):
        step = steps[n]
        if isinstance(step, ApprovalActionStep):
            arc.append(step)
        elif (isinstance(step, UpdateStep) and step.expect_acceptance
                and step.expect_rejection is None
                and n + 1 < len(steps)):
            if (not isinstance(step.target, LogicalRef)
                    or step.target.external_id
                    != create.target_object.external_id):
                raise PlanTranslationError(
                    f"entry update {step.step_id!r} must target the setup "
                    f"create's object {create.target_object.external_id!r}",
                    recipe_id=recipe_id,
                )
            entries.append(step)
        else:
            break
        n += 1

    if len(steps) != n + 1:
        shape = ", ".join(type(s).__name__ for s in steps)
        raise PlanTranslationError(
            f"a behavioral negative is 1 step (create-rejected) or a setup "
            f"create -> [ApprovalActionStep x K, D-333 | entry "
            f"update-expect-acceptance x K, VR05 arc] -> rejected "
            f"update/delete; got {len(steps)} steps [{shape}] (N-step "
            f"negatives are deferred, 5b-2)",
            recipe_id=recipe_id,
        )

    # The flag must sit on the mutation, not the setup create.
    if create.expect_rejection is not None:
        raise PlanTranslationError(
            f"a create-rejected negative is single-step; a 2-step negative's "
            f"setup create {create.step_id!r} must not carry expect_rejection",
            recipe_id=recipe_id,
        )
    mutation = steps[n]
    if not isinstance(mutation, (UpdateStep, DeleteStep)):
        raise PlanTranslationError(
            f"step 2 of a 2-step behavioral negative must be an UpdateStep or "
            f"DeleteStep; got {type(mutation).__name__}",
            recipe_id=recipe_id,
        )
    if mutation.expect_rejection is None:
        raise PlanTranslationError(
            f"{mutation.kind} {mutation.step_id!r} in a 2-step behavioral "
            f"negative must carry expect_rejection",
            recipe_id=recipe_id,
        )
    if not isinstance(mutation.target, LogicalRef):
        raise PlanTranslationError(
            f"{mutation.kind} {mutation.step_id!r} must target a LogicalRef; "
            f"got a {type(mutation.target).__name__}",
            recipe_id=recipe_id,
        )
    if mutation.target.external_id != create.target_object.external_id:
        raise PlanTranslationError(
            f"{mutation.kind} {mutation.step_id!r} targets "
            f"{mutation.target.external_id!r} but the setup create provisions "
            f"{create.target_object.external_id!r} — the rejected mutation "
            f"must act on the record the setup creates (D-203)",
            recipe_id=recipe_id,
        )

    planned_setup = PlannedCreate(
        step_id=create.step_id,
        target_object=create.target_object,
        field_values=dict(create.field_values),
        expect_rejection=None,
    )
    planned_arc = tuple(
        PlannedApprovalAction(
            step_id=a.step_id, action=a.action, comment=a.comment,
            setup_step_id=create.step_id)
        for a in arc)
    # VR05 arc: entry updates ride BEFORE the arc in the planned middle (the
    # authored order is create -> entries -> mutation; arc and entries do not
    # mix today — each comes from a different author).
    planned_arc = tuple(
        PlannedUpdate(
            step_id=e.step_id, target_object=e.target,
            field_changes=dict(e.field_changes),
            expect_rejection=None, expect_acceptance=True,
            setup_step_id=create.step_id)
        for e in entries) + planned_arc
    if isinstance(mutation, UpdateStep):
        return (
            planned_setup,
            *planned_arc,
            PlannedUpdate(
                step_id=mutation.step_id,
                target_object=mutation.target,
                field_changes=dict(mutation.field_changes),
                expect_rejection=mutation.expect_rejection,
                setup_step_id=create.step_id,
            ),
        )
    return (
        planned_setup,
        *planned_arc,
        PlannedDelete(
            step_id=mutation.step_id,
            target_object=mutation.target,
            expect_rejection=mutation.expect_rejection,
            setup_step_id=create.step_id,
        ),
    )


def _project_positive(steps, *, recipe_id) -> tuple:
    """Project the positive create-and-verify (D-115 side B, N-create chains
    D-205, the update-then-observe phase D-306): ``CreateStep × N`` (N ≥ 1,
    none carrying ``expect_rejection``) → ``[UpdateStep × 0..1]`` (no
    ``expect_rejection`` — a flagged update routes to the negative projection)
    → ``ReadStep`` → ``AssertStep``. A later create's field values may
    reference an earlier create's record (``"$<step_id>.id"`` — the executor
    resolves). The update, when present, mutates the record the TERMINAL
    create made (the record under observation — the one the read observes);
    its target must name that create's object. Every other shape fails loud.
    ``steps[0]`` is already validated as a ``CreateStep`` on a ``LogicalRef``."""
    n = 0
    while n < len(steps) and isinstance(steps[n], CreateStep):
        create = steps[n]
        if create.expect_rejection is not None:
            # Unreachable via build_data_recipe_plan (a flagged step routes to
            # the negative projection) — kept as a loud guard for direct calls.
            raise PlanTranslationError(
                f"create {create.step_id!r} in a positive recipe must not "
                f"carry expect_rejection",
                recipe_id=recipe_id,
            )
        if not isinstance(create.target_object, LogicalRef):
            raise PlanTranslationError(
                f"create {create.step_id!r} must target a LogicalRef (the "
                f"sobject by name); got a {type(create.target_object).__name__}",
                recipe_id=recipe_id,
            )
        n += 1

    creates_end = n
    terminal = steps[creates_end - 1] if creates_end > 0 else None

    # D-333: an optional approval-action arc — contiguous ApprovalActionSteps
    # after the creates, bound to the TERMINAL create (the record under
    # observation). The arc requires the positive update that follows it (an
    # arc with nothing to accept afterwards observes nothing).
    arc = []
    while n < len(steps) and isinstance(steps[n], ApprovalActionStep):
        arc.append(steps[n])
        n += 1
    if arc and (n >= len(steps) or not isinstance(steps[n], UpdateStep)):
        raise PlanTranslationError(
            f"an approval-action arc in a positive recipe must be followed by "
            f"the accepted UpdateStep (D-333); got "
            f"{type(steps[n]).__name__ if n < len(steps) else 'nothing'} after "
            f"{len(arc)} action step(s)",
            recipe_id=recipe_id,
        )

    # D-306: an optional single positive update between the creates and the
    # read — the trigger phase of update-then-observe.
    update = None
    if n < len(steps) and isinstance(steps[n], UpdateStep):
        update = steps[n]
        if update.expect_rejection is not None:
            # Unreachable via build_data_recipe_plan (same reason as above).
            raise PlanTranslationError(
                f"update {update.step_id!r} in a positive recipe must not "
                f"carry expect_rejection",
                recipe_id=recipe_id,
            )
        if not isinstance(update.target, LogicalRef):
            raise PlanTranslationError(
                f"update {update.step_id!r} must target a LogicalRef; got a "
                f"{type(update.target).__name__}",
                recipe_id=recipe_id,
            )
        if terminal is None or (
                update.target.external_id != terminal.target_object.external_id):
            raise PlanTranslationError(
                f"update {update.step_id!r} targets "
                f"{update.target.external_id!r} but the terminal create "
                f"provisions "
                f"{terminal.target_object.external_id if terminal else None!r} "
                f"— a positive update must act on the record under "
                f"observation (D-306)",
                recipe_id=recipe_id,
            )
        if not update.field_changes:
            raise PlanTranslationError(
                f"update {update.step_id!r} carries no field_changes — an "
                f"empty positive update observes nothing (D-306)",
                recipe_id=recipe_id,
            )
        n += 1

    if (creates_end == 0 or len(steps) != n + 2
            or not isinstance(steps[n], ReadStep)
            or not isinstance(steps[n + 1], DataAssertStep)):
        shape = ", ".join(type(s).__name__ for s in steps) or "(empty)"
        raise PlanTranslationError(
            f"positive data-recipe must be CreateStep x N (N >= 1) -> "
            f"[ApprovalActionStep x K, D-333] -> [UpdateStep x 0..1] -> "
            f"ReadStep -> AssertStep; got [{shape}]",
            recipe_id=recipe_id,
        )
    read, assertion = steps[n], steps[n + 1]
    if not isinstance(read.target, LogicalRef):
        raise PlanTranslationError(
            f"read {read.step_id!r} must target a LogicalRef (the FROM object by "
            f"name); got a {type(read.target).__name__}",
            recipe_id=recipe_id,
        )
    # D-333: the arc binds to the TERMINAL create (the record the actions and
    # the accepted update act on).
    planned_arc = tuple(
        PlannedApprovalAction(
            step_id=a.step_id, action=a.action, comment=a.comment,
            setup_step_id=terminal.step_id)
        for a in arc)
    planned_update = () if update is None else (
        PlannedUpdate(
            step_id=update.step_id,
            target_object=update.target,
            field_changes=dict(update.field_changes),
            expect_rejection=None,
            # binds to the TERMINAL create — the record under observation.
            setup_step_id=terminal.step_id,
            # D-305.1 (review B1) applied forward: the flag must SURVIVE
            # projection or the update leg's defining failure grade is dead code.
            expect_acceptance=getattr(update, "expect_acceptance", False),
        ),
    )
    return tuple(
        PlannedCreate(
            step_id=c.step_id,
            target_object=c.target_object,
            field_values=dict(c.field_values),
            expect_rejection=None,
            # D-305.1 (review B1): the acceptance flag must SURVIVE projection —
            # dropping it made the archetype's defining failure grade dead code.
            expect_acceptance=getattr(c, "expect_acceptance", False),
        )
        for c in steps[:creates_end]
    ) + planned_arc + planned_update + (
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

"""S8 recipe-grounding leg (D-113) — does a behavioral-negative recipe's stored
payload still violate the current validation-rule formula?

The first slice of the S8 grounding-validity predicate (SPEC §2), recipe-grounding
axis only. A **pure function**, **object-level**: it reads the recipe's stored
violating payload (S2 — ``CreateStep.field_values`` + ``target_object``) and the
current active validation-rule formula(s) on that object (S1, through a VR-read
port — the ``APPLIES_TO`` path), and asks, via the neutral
``primeqa.semantic.formula.evaluate`` primitive: does the payload still fire an
active rule?

**Object-level (D-113).** The recipe pins no specific VR (only a generic
``FIELD_CUSTOM_VALIDATION_EXCEPTION`` expectation), so the verdict is *behavioral*
— "still rejected by *any* active VR on the object" — not claim-specific. An
``intact`` can therefore mask the loss of one *specific* VR when a different active
VR still catches the payload. A VR-pin on the recipe is the deferred
generation-side sharpening.

**Parallel-siblings (D-112).** S8 reads S1's VR metadata through its OWN read port
(:class:`VrReader`), duck-typed: any object exposing ``is_active`` +
``formula_text`` satisfies it — S6's ``VrMeta`` does, but S8 does **not** import
it. The predicate consumes the neutral ``evaluate`` primitive, never S6's verdict.

Faculty-first / produce-only: a pure verdict, nothing wired, no persistence.
Recipe-grounding axis ONLY — claim-grounding (schema / field resolution),
admissibility (derivability regression), and field-value-validity (e.g. a removed
picklist value — a known false-``intact`` here) are later legs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol

from primeqa.semantic.formula import evaluate, parse
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)

GroundingVerdict = Literal["intact", "drifted", "broken"]
# reason is load-bearing on ``broken`` (D-113): WHY there is no grounding.
BrokenReason = Literal["no_active_vr", "formula_non_evaluable"]


@dataclass(frozen=True)
class RecipeGroundingResult:
    """The recipe-grounding verdict. ``reason`` is set only on ``broken`` (one of
    :data:`BrokenReason`) and is **load-bearing** — it distinguishes "no active VR
    to ground against" (``no_active_vr`` — the foundation is gone) from "active
    VR(s) present but their current formula(s) are non-evaluable"
    (``formula_non_evaluable`` — structural)."""

    verdict: GroundingVerdict
    reason: Optional[str] = None


class VrReader(Protocol):
    """Read-port for the validation rules applying to an object (the
    ``APPLIES_TO`` path). Production delegates to S1's query interface; tests
    inject a stub. Returns objects duck-typed to expose ``is_active: bool`` and
    ``formula_text: Optional[str]`` — S6's ``S1ValidationRuleReader`` /
    ``VrMeta`` satisfy it, but S8 does not import them (parallel-siblings,
    D-112)."""

    def vrs_for_object(self, subject_external_id: str) -> tuple:
        ...


def recipe_grounding_validity(
    payload: dict[str, Any], subject_external_id: str, *, s1: VrReader,
) -> RecipeGroundingResult:
    """Does ``payload`` still violate an active VR on ``subject_external_id``?

    The pure object-level core (D-113):

    - **intact** — >=1 active VR's current formula evaluates ``True`` (the create
      would still be rejected).
    - **drifted** — no active VR is triggered (>=1 evaluated ``False``) but at
      least one active, *evaluable* VR exists -> re-groundable.
    - **broken** — no active VR is triggered and there is no re-groundable
      foundation: ``no_active_vr`` (none active on the object) or
      ``formula_non_evaluable`` (active VR(s) present, but none evaluable).
    """
    active = [vr for vr in s1.vrs_for_object(subject_external_id) if vr.is_active]
    if not active:
        return RecipeGroundingResult("broken", reason="no_active_vr")

    saw_false = False
    for vr in active:
        if not vr.formula_text:
            continue
        result = evaluate(parse(vr.formula_text), payload)
        if result is True:
            return RecipeGroundingResult("intact")     # an active VR still fires
        if result is False:
            saw_false = True
        # NonEvaluable -> neither a fire nor a clean drift signal; skip it.

    if saw_false:
        return RecipeGroundingResult("drifted")
    return RecipeGroundingResult("broken", reason="formula_non_evaluable")


def recipe_grounding_validity_for_recipe(
    recipe: DataRecipeBody, *, s1: VrReader,
) -> RecipeGroundingResult:
    """Adapter: extract the behavioral-negative create step's stored payload +
    target object from a :class:`DataRecipeBody` and evaluate its grounding.

    The object name comes from ``target_object.external_id`` (both ``LogicalRef``
    and ``PinnedRef`` carry it — no S1 resolution needed). Raises ``ValueError``
    when the recipe carries no behavioral-negative create step (no
    ``expect_rejection``) — the leg is defined for behavioral negatives."""
    step = _negative_create_step(recipe)
    if step is None:
        raise ValueError(
            "recipe has no behavioral-negative create step (expect_rejection)")
    return recipe_grounding_validity(
        step.field_values, step.target_object.external_id, s1=s1)


def _negative_create_step(recipe: DataRecipeBody) -> Optional[CreateStep]:
    for step in recipe.steps:
        if isinstance(step, CreateStep) and step.expect_rejection is not None:
            return step
    return None

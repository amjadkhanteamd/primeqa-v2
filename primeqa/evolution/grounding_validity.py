"""S8 two-level grounding-validity composition (D-141) — the public predicate.

Composes the three built legs (recipe-grounding D-113, claim-grounding D-139,
field-value-validity D-140) into the grounding-validity predicate SPEC §2 names:
``grounding_validity(artifact, current_org) → intact | drifted | broken``. It is
**two-level** — a claim-level verdict (one per claim) and a recipe-level verdict
(one per recipe) — and **composed, never collapsed** (D-112 Fork C): the parts
stay individually addressable on the result; ``overall`` is only a derived
roll-up.

**Precedence (D-141):** ``broken > drifted > intact``. Per recipe, the two recipe
legs roll up by max severity — so a field-value ``broken`` **un-masks** a
recipe-grounding ``intact`` (D-140's point: the payload still fires a VR, but its
picklist value is gone). ``overall`` is the max severity across the claim-level
verdict + every recipe roll-up — so a claim-level ``broken`` dominates (the
subject is gone; nothing grounds).

**Three ports, not one (D-112).** ``grounding_validity`` takes ``subjects`` /
``vrs`` / ``picklists`` separately — each leg keeps its own read port
(parallel-siblings). The production tri-port adapter over one ``SemanticOrgModel``
lands in slice 5 (``s1_reader.py``), where the recompute trigger first exercises
it; this module is the pure composition only.

**Mixed-recipe honesty.** The recipe legs are behavioral-negative-only; the
composition **skips** non-negative recipes (no fabricated verdict) — they ground
via the claim-grounding leg + future legs.
"""
from __future__ import annotations

from dataclasses import dataclass

from primeqa.evolution.claim_grounding import (
    ClaimGroundingResult,
    SubjectResolver,
    claim_grounding_validity_for_claim,
)
from primeqa.evolution.field_value_grounding import (
    FieldValueGroundingResult,
    PicklistReader,
    field_value_grounding_validity_for_recipe,
)
from primeqa.evolution.recipe_grounding import (
    GroundingVerdict,
    RecipeGroundingResult,
    VrReader,
    _negative_create_step,
    recipe_grounding_validity_for_recipe,
)
from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.recipes.data_recipe import DataRecipeBody

# broken > drifted > intact.
_SEVERITY = {"intact": 0, "drifted": 1, "broken": 2}


@dataclass(frozen=True)
class Artifact:
    """One claim + its recipes — the unit S8 grounds. ``claim`` is the claim's
    ``asserted_truth`` body; ``recipes`` are its data recipes (the realized S2
    join, ``test_claims`` + ``test_recipes`` by ``claim_test_id``)."""

    claim: BodyBase
    recipes: tuple[DataRecipeBody, ...] = ()


@dataclass(frozen=True)
class RecipeVerdict:
    """The recipe-level verdict for one behavioral-negative recipe: both recipe
    legs + their max-severity roll-up. ``recipe`` is the source body (by identity,
    for correlation back to its S2 row). ``field_value`` un-masks a
    ``recipe_grounding`` ``intact``."""

    recipe: DataRecipeBody
    recipe_grounding: RecipeGroundingResult
    field_value: FieldValueGroundingResult
    rolled_up: GroundingVerdict


@dataclass(frozen=True)
class GroundingValidity:
    """The composed grounding-validity verdict — two-level, composed never
    collapsed (D-112 Fork C): the claim-level verdict + the per-recipe verdicts
    stay addressable; ``overall`` is the derived max-severity roll-up."""

    claim_grounding: ClaimGroundingResult
    recipe_verdicts: tuple[RecipeVerdict, ...]
    overall: GroundingVerdict


def grounding_validity(
    artifact: Artifact, *,
    subjects: SubjectResolver, vrs: VrReader, picklists: PicklistReader,
) -> GroundingValidity:
    """Compose the three legs over ``artifact`` into the two-level verdict.

    Claim-level = the claim-grounding leg over ``artifact.claim``. Recipe-level =
    per **behavioral-negative** recipe, the recipe-grounding + field-value legs
    rolled up by max severity; non-negative recipes are skipped (the recipe legs
    are negative-only). ``overall`` = the max severity across the claim verdict +
    every recipe roll-up."""
    claim_g = claim_grounding_validity_for_claim(artifact.claim, s1=subjects)

    recipe_verdicts: list[RecipeVerdict] = []
    for recipe in artifact.recipes:
        if _negative_create_step(recipe) is None:
            continue                       # not a behavioral negative — legs N/A
        rg = recipe_grounding_validity_for_recipe(recipe, s1=vrs)
        fv = field_value_grounding_validity_for_recipe(recipe, s1=picklists)
        rolled = _max_severity(rg.verdict, fv.verdict)
        recipe_verdicts.append(RecipeVerdict(recipe, rg, fv, rolled))

    overall = _max_severity(
        claim_g.verdict, *(rv.rolled_up for rv in recipe_verdicts))
    return GroundingValidity(claim_g, tuple(recipe_verdicts), overall)


def _max_severity(*verdicts: str) -> GroundingVerdict:
    """The most-severe verdict among ``verdicts`` (broken > drifted > intact).
    Claim verdicts are intact/broken; recipe roll-ups add drifted. Always called
    with at least the claim verdict, so ``verdicts`` is non-empty."""
    return max(verdicts, key=lambda v: _SEVERITY[v])

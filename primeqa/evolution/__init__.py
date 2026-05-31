"""Substrate 8 — Evolution Engine.

S8 governs whether a test remains *meaningfully true* as the org evolves (the SPEC
lives at ``docs/architecture/substrate_8_evolution/SPEC.md``). The keystone: a
claim's identity is org-independent by construction, so org evolution is never an
identity-axis event — always a grounding-axis event. S8 evaluates **grounding
continuity under identity preservation**; it never changes what a test means.
Distinct from v1's ``primeqa.intelligence`` and from S6's
``primeqa.interpretation``.

Faculty-first (D-112 / D-113): the semantic core is the **grounding-validity
predicate** — a deterministic, on-demand pure function ``(artifact, current org)
-> intact / drifted / broken`` that re-asks generation's own grounding checks
against today's org. The evolution *mechanics* (manifests, triggers, reverse
index, re-grounding orchestration) are explicitly deferred.

Slice 1 (D-113): the **recipe-grounding leg** — does a behavioral-negative
recipe's stored payload still violate the current validation-rule formula?
Re-consumes the neutral ``primeqa.semantic.formula.evaluate`` primitive (parallel
to S6, never via S6). Produce-only.
"""
from primeqa.evolution.recipe_grounding import (
    RecipeGroundingResult,
    VrReader,
    recipe_grounding_validity,
    recipe_grounding_validity_for_recipe,
)

__all__ = [
    "recipe_grounding_validity",
    "recipe_grounding_validity_for_recipe",
    "RecipeGroundingResult",
    "VrReader",
]

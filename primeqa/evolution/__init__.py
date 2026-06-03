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

Phase 6 slice 1 (D-139): the **claim-grounding leg** — does a claim's subject
still resolve in the current org? Re-asks generation's resolution step (S1
``get_entities`` by ``sf_api_name``) through S8's own :class:`SubjectResolver`
port. Produce-only.

Phase 6 slice 2 (D-140): the **field-value-validity leg** — do a recipe payload's
field values still exist? Closes recipe-grounding's removed-picklist-value
false-``intact`` via S8's own :class:`PicklistReader` port. Produce-only.

Phase 6 slice 3 (D-141): the **two-level composition** — ``grounding_validity``
composes the three legs into the predicate (claim-level + recipe-level, composed
never collapsed; ``broken`` > ``drifted`` > ``intact``). Pure; three ports.
"""
from primeqa.evolution.claim_grounding import (
    ClaimGroundingResult,
    SubjectResolver,
    claim_grounding_validity,
    claim_grounding_validity_for_claim,
)
from primeqa.evolution.field_value_grounding import (
    FieldValueGroundingResult,
    PicklistReader,
    field_value_grounding_validity,
    field_value_grounding_validity_for_recipe,
)
from primeqa.evolution.grounding_validity import (
    Artifact,
    GroundingValidity,
    RecipeVerdict,
    grounding_validity,
)
from primeqa.evolution.recipe_grounding import (
    RecipeGroundingResult,
    VrReader,
    recipe_grounding_validity,
    recipe_grounding_validity_for_recipe,
)

__all__ = [
    # recipe-grounding leg (D-113)
    "recipe_grounding_validity",
    "recipe_grounding_validity_for_recipe",
    "RecipeGroundingResult",
    "VrReader",
    # claim-grounding leg (D-139)
    "claim_grounding_validity",
    "claim_grounding_validity_for_claim",
    "ClaimGroundingResult",
    "SubjectResolver",
    # field-value-validity leg (D-140)
    "field_value_grounding_validity",
    "field_value_grounding_validity_for_recipe",
    "FieldValueGroundingResult",
    "PicklistReader",
    # two-level composition (D-141)
    "grounding_validity",
    "GroundingValidity",
    "Artifact",
    "RecipeVerdict",
]

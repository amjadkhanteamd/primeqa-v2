"""Semantic conditions body — claim-kind-independent.

Per SPEC §3 (semantic conditions layer is identity-bearing and
structurally uniform across archetypes) + D-051 §"Semantic
conditions". Conditions narrow the scope under which a claim's
asserted truth holds; the substrate models them as a flat list of
:class:`Condition` instances that are implicitly AND-composed.

This body shape is **not** archetype-specific — every archetype
(data-behavior, configuration, permission, ui, integration) shares
the same conditions envelope. The semantic content of individual
conditions can vary, but the envelope is uniform.

Empty conditions are permitted: a claim with an empty conditions
body applies unconditionally ("this is always true," not "this is
never true"). The Coordinator decides whether unconditional truth
is meaningful for a given claim_kind.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

# Predicates that require ``value`` to be provided.
_VALUE_BEARING_PREDICATES = {"equals", "not_equals", "in_set", "matches_pattern"}
# Predicates that require ``value`` to be absent.
_VALUE_FREE_PREDICATES = {"is_null", "is_not_null"}


class Condition(BaseModel):
    """A single semantic-conditions clause.

    The clause asserts a predicate over ``subject`` — typically a
    Salesforce field, but the substrate doesn't pin the entity_type
    here (that's the Coordinator's job per D-058 §5.7 ontology
    enforcement).

    The (predicate, value) coupling is enforced by a model
    validator:
      - value-bearing predicates (equals / not_equals / in_set /
        matches_pattern) REQUIRE ``value`` to be non-None.
      - value-free predicates (is_null / is_not_null) REQUIRE
        ``value`` to be None.

    The substrate validates structural coupling. Value-type
    correctness against the subject's Salesforce type is the
    Coordinator's responsibility per D-060 §4.7.6.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: IdentityBearingRef
    """The entity (usually a field) the predicate operates over.
    Pinned per D-058 §5.4 — coverage extraction walks these refs."""

    predicate: Literal[
        "equals",
        "not_equals",
        "in_set",
        "matches_pattern",
        "is_null",
        "is_not_null",
    ]
    """The predicate. Closed taxonomy at B-β; extensions land via a
    new body_schema_version (D-059 §6.3.7)."""

    value: Optional[Any] = None
    """The value the predicate operates against. ``Any`` so the
    substrate doesn't enforce SF-type alignment here (Coordinator
    does that). MUST be None for value-free predicates."""

    @model_validator(mode="after")
    def _check_value_predicate_coupling(self) -> "Condition":
        if self.predicate in _VALUE_BEARING_PREDICATES:
            if self.value is None:
                raise ValueError(
                    f"predicate {self.predicate!r} requires a "
                    f"non-None ``value``"
                )
        elif self.predicate in _VALUE_FREE_PREDICATES:
            if self.value is not None:
                raise ValueError(
                    f"predicate {self.predicate!r} forbids a "
                    f"``value``; got {self.value!r}"
                )
        return self


# ---------------------------------------------------------------------------
# SemanticConditionsBody
# ---------------------------------------------------------------------------

@register_body("conditions", 1)
class SemanticConditionsBody(BodyBase):
    """The conditions-layer body shape.

    Per SPEC §3: every archetype carries a conditions layer with
    this same envelope shape. The list is implicitly AND-composed;
    OR-composition is intentionally NOT supported at v1 (the
    typical case is a conjunction of "field X = Y" predicates, and
    OR makes coverage extraction + canonicalization meaningfully
    harder). If a future scenario needs disjunction, a new
    body_schema_version can extend.

    Per D-058 §5.4: every IdentityBearingRef in the conditions
    body is walkable for coverage extraction — the substrate
    treats conditions as part of the test's identity, so refs in
    here count toward what the test covers.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["conditions"] = "conditions"

    conditions: list[Condition] = []
    """Implicitly AND-composed clauses. Empty list = "applies
    unconditionally". Order is preserved on the wire but is NOT
    semantically meaningful (canonicalization per D-059 §6.3.4
    will sort for hashing)."""

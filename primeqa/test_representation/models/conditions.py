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

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from primeqa.test_representation.models.common import (
    ArraySemantics,
    BodyBase,
)
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

# Predicates that require ``value`` to be provided.
_VALUE_BEARING_PREDICATES = {"equals", "not_equals", "in_set"}
# Predicates whose ``value`` is OPTIONAL (D-384): ``matches_pattern``'s
# format is ORG-OWNED (the grounding validation rule's REGEX defines it),
# so the clause's semantic content is complete as (subject, predicate) and
# S3 authors it value-free. Optional rather than forbidden: persisted
# legacy bodies carry model-invented values and re-validate through this
# model on every read — forbidding would make them unreadable.
_VALUE_OPTIONAL_PREDICATES = {"matches_pattern"}
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
      - value-bearing predicates (equals / not_equals / in_set)
        REQUIRE ``value`` to be non-None.
      - value-free predicates (is_null / is_not_null) REQUIRE
        ``value`` to be None.
      - value-optional predicates (matches_pattern, D-384) accept
        either: new claims author value-free (the org owns the
        format); legacy persisted clauses carry a value and must
        keep re-validating on read.

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
        if self.predicate in _VALUE_OPTIONAL_PREDICATES:
            return self  # D-384: value accepted either way (legacy parse)
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
# ConditionV2 — the cross-field comparison form (D-330)
# ---------------------------------------------------------------------------

# v2 predicates whose right-hand side is ANOTHER FIELD (``compared_to``),
# not a literal ``value``.
_FIELD_COMPARISON_PREDICATES = {"exceeds"}


class ConditionV2(BaseModel):
    """A v2 semantic-conditions clause — v1's taxonomy plus the
    CROSS-FIELD comparison form (D-330): ``exceeds`` asserts the
    subject field's value is greater than ANOTHER field's value
    ("Loan Amount exceeds Property Value"), carried as
    ``compared_to`` (a pinned ref, walkable for coverage per
    D-058 §5.4) instead of a literal ``value``.

    Coupling (model-validated):
      - value-bearing predicates: ``value`` required, ``compared_to``
        forbidden (v1 semantics unchanged).
      - value-free predicates: both forbidden.
      - value-optional predicates (``matches_pattern``, D-384):
        ``value`` accepted either way (v1 semantics), ``compared_to``
        forbidden.
      - field-comparison predicates (``exceeds``): ``compared_to``
        required, ``value`` forbidden.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: IdentityBearingRef
    predicate: Literal[
        "equals",
        "not_equals",
        "in_set",
        "matches_pattern",
        "is_null",
        "is_not_null",
        "exceeds",
    ]
    value: Optional[Any] = None
    compared_to: Optional[IdentityBearingRef] = None
    """The right-hand FIELD of a field-comparison predicate. Pinned
    per D-058 §5.4 — coverage extraction walks this ref too."""

    @model_validator(mode="after")
    def _check_value_predicate_coupling(self) -> "ConditionV2":
        if self.predicate in _FIELD_COMPARISON_PREDICATES:
            if self.compared_to is None:
                raise ValueError(
                    f"predicate {self.predicate!r} requires "
                    f"``compared_to``"
                )
            if self.value is not None:
                raise ValueError(
                    f"predicate {self.predicate!r} forbids a "
                    f"``value``; got {self.value!r}"
                )
            return self
        if self.compared_to is not None:
            raise ValueError(
                f"predicate {self.predicate!r} forbids "
                f"``compared_to``"
            )
        if self.predicate in _VALUE_OPTIONAL_PREDICATES:
            return self  # D-384: value accepted either way (legacy parse)
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

    conditions: Annotated[list[Condition], ArraySemantics.SET] = []
    """Implicitly AND-composed clauses. Empty list = "applies
    unconditionally". Marked :class:`ArraySemantics.SET` per D-059
    §6.3.4: AND-composition is commutative; order is incidental on
    the wire and canonicalization sorts for hashing."""


@register_body("conditions", 2)
class SemanticConditionsBodyV2(BodyBase):
    """The conditions-layer body shape, v2 (D-330) — same envelope
    as v1 but the clauses are :class:`ConditionV2` (adds the
    cross-field ``exceeds`` predicate + ``compared_to`` ref).

    Why a new version, not a v1 field (the D-306.1 precedent):
    canonicalization includes every model field (None → null), so
    adding ``compared_to`` to v1's ``Condition`` would re-key every
    existing claim's conditions hash (dedup misses, duplicate
    claims). Claims whose clauses all fit v1 keep authoring v1
    (byte-identical); only a claim carrying a cross-field clause
    authors v2.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[2] = 2
    kind: Literal["conditions"] = "conditions"

    conditions: Annotated[list[ConditionV2], ArraySemantics.SET] = []
    """Implicitly AND-composed clauses — v1 semantics, v2 clause
    shape."""

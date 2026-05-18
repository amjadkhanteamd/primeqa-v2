"""Common body conventions for substrate-2 Pydantic models.

Per SPEC §4.4 (universal body convention) + §4.7.2 (Pydantic
patterns). Defines:

  - :class:`BodyBase` — abstract Pydantic base every concrete body
    model derives from. Carries the two universal body fields
    (``body_schema_version`` + ``kind``) that the registry uses
    for dispatch.
  - :class:`ArraySemantics` — enum distinguishing ORDERED vs SET
    arrays for the canonicalization step in D-059 §6.3.4.
  - :class:`SemanticField` / :class:`ProjectionField` — sentinel
    markers reserved for body subclasses to attach descriptor
    metadata (ontology hints, observation projections) per D-059
    §6.3.10 + D-060 §4.7.4 trajectory. They're empty in B-α;
    concrete body sub-tracks (B-β..) will populate.
  - Type aliases — narrow names for the substrate's id types.
"""
from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Type aliases — narrow names for substrate-2's identifier types. Kept as
# plain aliases (not NewType) so Pydantic treats them as the underlying type
# without extra wrapping; they exist for documentation + grep-ability.
# ---------------------------------------------------------------------------

TestId = UUID
"""Identifier of a test (the identity-bearing claim per D-051)."""

RecipeId = UUID
"""Identifier of a recipe (a first-class operational entity per
D-051)."""

EntityId = UUID
"""Identifier of an S1 entity. Cross-substrate logical FK per D-058
§5.3."""

IdentityHash = str
"""Semantic equivalence fingerprint per D-059. NOT unique, NOT a
key; comparison is only valid within the same
``identity_hash_version`` (D-059 §6.3.7)."""


# ---------------------------------------------------------------------------
# BodyBase
# ---------------------------------------------------------------------------

class BodyBase(BaseModel):
    """Abstract base for every concrete body model.

    Per SPEC §4.4: every JSONB body in substrate-2 carries the same
    two top-level keys — ``body_schema_version`` (integer) and
    ``kind`` (string). These keys drive registry dispatch in
    :mod:`primeqa.test_representation.models.registry` and the
    DB-level key-presence check enforced by migration
    ``20260518_1014_create_substrate_2_tables`` per Track A note
    A8.

    Concrete subclasses MUST:
      1. Override ``kind`` with a ``Literal["specific-kind"]`` so
         Pydantic enforces the exact discriminator value.
      2. Set ``body_schema_version`` to their own version integer
         (or accept it as a field default) so the registry can find
         them by ``(kind, body_schema_version)``.

    The class is intentionally permissive about extra fields at
    this layer; concrete bodies tighten via their own
    ``model_config``.
    """

    model_config = ConfigDict(
        # Concrete bodies decide whether to allow extras. The base
        # leaves it permissive so this class isn't the source of
        # cross-body inconsistency.
        extra="allow",
        # Frozen models match the substrate's append-only +
        # supersession semantics (bodies are not mutated in place
        # per D-061). Subclasses can override if they need mutable
        # construction paths.
        frozen=False,
    )

    body_schema_version: int
    """Per SPEC §4.4. The registry uses this to dispatch to the
    right Pydantic model on read."""

    kind: str
    """Per SPEC §4.4. Concrete subclasses MUST tighten to a
    ``Literal["..."]`` matching one of the enum values defined in
    Track A's migration (e.g., ``"value-claim"``,
    ``"data-recipe"``)."""


# ---------------------------------------------------------------------------
# ArraySemantics
# ---------------------------------------------------------------------------

class ArraySemantics(str, Enum):
    """Per D-059 §6.3.4. Distinguishes arrays whose order is
    semantically meaningful from arrays that are unordered sets.

    Canonicalization for ``identity_hash`` computation handles the
    two cases differently:
      - ``ORDERED``: preserve element order; the canonical form is
        the JSON array exactly as written.
      - ``SET``: sort elements by their canonical-JSON
        representation before hashing; equal sets hash equal
        regardless of input order.

    The enum value matches what concrete body fields will declare
    via :class:`SemanticField` descriptors in B-β through B-ε.
    """

    ORDERED = "ordered"
    """Array order is semantically meaningful (e.g., a sequence of
    state transitions in a state-transition-claim)."""

    SET = "set"
    """Array order is not semantically meaningful (e.g., a set of
    capability references in a capability-claim)."""


# ---------------------------------------------------------------------------
# Descriptor sentinel markers
#
# Per D-059 §6.3.10 + D-060 §4.7.4. These are intentionally empty
# in B-α; concrete body sub-tracks (B-β..) extend them with the
# ontology + projection metadata each body kind requires. Keeping
# them defined here lets B-α downstream tests + type stubs import
# stable names without forward-declaring.
# ---------------------------------------------------------------------------

class SemanticField:
    """Sentinel marker for fields that participate in identity-hash
    canonicalization and ontology validation per D-059 §6.3.10.

    Concrete body models in B-β through B-ε will use instances of
    this marker as Pydantic ``Field(...)`` metadata so the
    Coordinator can introspect ontology allow-lists and array
    semantics. B-α reserves the name.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def __repr__(self) -> str:
        return f"SemanticField(**{self.kwargs!r})"


class ProjectionField:
    """Sentinel marker for fields that contribute to S6's
    observation projections per D-060 §4.7.4 trajectory.

    Concrete body models in B-β through B-ε will use instances of
    this marker on recipe-body fields whose values S6 reads to
    construct observation diffs. B-α reserves the name.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def __repr__(self) -> str:
        return f"ProjectionField(**{self.kwargs!r})"

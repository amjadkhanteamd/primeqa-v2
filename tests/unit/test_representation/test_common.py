"""Tests for substrate-2 common body conventions.

Per SPEC §4.4 (universal body convention) + §4.7.2 (Pydantic
patterns) + D-059 §6.3.4 (array semantics).

Covers:

  - BodyBase carries body_schema_version + kind and concrete
    subclasses tighten ``kind`` to a ``Literal`` — the registry
    dispatch contract.
  - Pydantic enforces the Literal discriminator at construction.
  - ArraySemantics enum: member access, .value, iteration, string
    enum behaviour (str(ArraySemantics.ORDERED) compatibility).
  - SemanticField / ProjectionField descriptor sentinels: stable
    constructors + repr for B-α (they're empty markers; concrete
    body sub-tracks B-β..ε populate them).
  - Type aliases (TestId / RecipeId / EntityId / IdentityHash)
    are importable and resolve to the expected underlying types.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal
from uuid import UUID

import pytest
from pydantic import ValidationError

from primeqa.test_representation.models.common import (
    ArraySemantics,
    BodyBase,
    EntityId,
    IdentityHash,
    ProjectionField,
    RecipeId,
    SemanticField,
    # Aliased so pytest's ``Test*`` class-collection glob doesn't
    # try to treat the UUID alias as a test class.
    TestId as _TestId,
)


# ---------------------------------------------------------------------------
# BodyBase subclassing — the canonical concrete-body shape
# ---------------------------------------------------------------------------

class _FixtureValueClaimBody(BodyBase):
    """Lightweight stand-in for a concrete body model.

    Demonstrates the tightening contract every B-β..ε body must
    follow:
      - ``body_schema_version`` defaults to the body's own version
      - ``kind`` is locked to a single string via ``Literal``
      - additional fields are declared alongside
    """
    body_schema_version: Literal[1] = 1
    kind: Literal["value-claim"] = "value-claim"
    statement: str


class _FixtureBodyV2(BodyBase):
    """Second version of the same kind — registry dispatches by
    (kind, body_schema_version)."""
    body_schema_version: Literal[2] = 2
    kind: Literal["value-claim"] = "value-claim"
    statement: str
    rationale: str


class TestBodyBaseSubclassing:
    def test_concrete_body_constructs_with_defaults(self) -> None:
        body = _FixtureValueClaimBody(statement="Account is the canonical Account.")
        assert body.body_schema_version == 1
        assert body.kind == "value-claim"
        assert body.statement == "Account is the canonical Account."

    def test_concrete_body_rejects_wrong_kind(self) -> None:
        """Literal["value-claim"] must reject any other string at
        construction time — that's what makes the registry's
        (kind, version) dispatch safe."""
        with pytest.raises(ValidationError):
            _FixtureValueClaimBody(
                statement="x",
                kind="data-recipe",  # type: ignore[arg-type]
            )

    def test_concrete_body_rejects_wrong_version(self) -> None:
        with pytest.raises(ValidationError):
            _FixtureValueClaimBody(
                statement="x",
                body_schema_version=99,  # type: ignore[arg-type]
            )

    def test_concrete_body_serializes_with_universal_keys(self) -> None:
        body = _FixtureValueClaimBody(statement="x")
        dumped = body.model_dump()
        # SPEC §4.4: every persisted body MUST carry these two top-
        # level keys. The migration's CHECK constraint enforces this
        # at the DB layer; Pydantic enforces it here at the model
        # layer.
        assert "body_schema_version" in dumped
        assert "kind" in dumped
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "value-claim"
        assert dumped["statement"] == "x"

    def test_two_versions_of_same_kind_are_distinct_types(self) -> None:
        v1 = _FixtureValueClaimBody(statement="x")
        v2 = _FixtureBodyV2(statement="x", rationale="because")
        assert v1.kind == v2.kind == "value-claim"
        assert v1.body_schema_version == 1
        assert v2.body_schema_version == 2
        # The registry treats these as distinct entries; the model
        # classes themselves are unrelated by Python inheritance.
        assert type(v1) is not type(v2)

    def test_body_base_is_abstract_in_spirit(self) -> None:
        """BodyBase doesn't enforce abstractness at the Python
        level, but ``kind`` is a plain ``str`` field there — it
        won't reject anything. The discipline is "concrete bodies
        tighten ``kind`` to a Literal". This test documents the
        gap: BodyBase accepts arbitrary strings."""
        b = BodyBase(body_schema_version=1, kind="anything-goes")
        assert b.kind == "anything-goes"


# ---------------------------------------------------------------------------
# ArraySemantics enum
# ---------------------------------------------------------------------------

class TestArraySemantics:
    def test_enum_members(self) -> None:
        assert ArraySemantics.ORDERED.value == "ordered"
        assert ArraySemantics.SET.value == "set"

    def test_enum_is_str_enum(self) -> None:
        """ArraySemantics is a ``str, Enum`` so members compare
        equal to their wire-format strings. Important for
        canonicalization code that round-trips through JSON."""
        assert ArraySemantics.ORDERED == "ordered"
        assert ArraySemantics.SET == "set"
        assert isinstance(ArraySemantics.ORDERED, str)

    def test_enum_membership(self) -> None:
        members = {m.value for m in ArraySemantics}
        assert members == {"ordered", "set"}

    def test_enum_from_value(self) -> None:
        assert ArraySemantics("ordered") is ArraySemantics.ORDERED
        assert ArraySemantics("set") is ArraySemantics.SET

    def test_enum_rejects_unknown_value(self) -> None:
        with pytest.raises(ValueError):
            ArraySemantics("multiset")

    def test_enum_is_an_enum(self) -> None:
        # Sanity check — protects against accidental refactor to a
        # plain string constant.
        assert issubclass(ArraySemantics, Enum)


# ---------------------------------------------------------------------------
# Sentinel descriptor markers — B-α reserves the names; B-β..ε populate
# ---------------------------------------------------------------------------

class TestDescriptorSentinels:
    def test_semantic_field_constructs_with_kwargs(self) -> None:
        sf = SemanticField(allowed_entity_types=["Object", "Field"])
        assert sf.kwargs == {"allowed_entity_types": ["Object", "Field"]}

    def test_semantic_field_repr_is_stable(self) -> None:
        sf = SemanticField(allowed_entity_types=["Object"])
        # Repr is asserted on so downstream tests + log lines can
        # rely on its shape.
        assert "SemanticField" in repr(sf)
        assert "allowed_entity_types" in repr(sf)

    def test_semantic_field_empty_kwargs(self) -> None:
        sf = SemanticField()
        assert sf.kwargs == {}

    def test_projection_field_constructs_with_kwargs(self) -> None:
        pf = ProjectionField(projects_to="observation_diff")
        assert pf.kwargs == {"projects_to": "observation_diff"}

    def test_projection_field_repr_is_stable(self) -> None:
        pf = ProjectionField(projects_to="observation_diff")
        assert "ProjectionField" in repr(pf)
        assert "projects_to" in repr(pf)

    def test_sentinels_are_distinct_types(self) -> None:
        """The Coordinator branches on isinstance(...,
        SemanticField) vs isinstance(..., ProjectionField); they
        must be unrelated classes."""
        sf = SemanticField()
        pf = ProjectionField()
        assert not isinstance(sf, ProjectionField)
        assert not isinstance(pf, SemanticField)


# ---------------------------------------------------------------------------
# Type aliases — narrow names + correct underlying types
# ---------------------------------------------------------------------------

class TestTypeAliases:
    def test_uuid_aliases_resolve_to_uuid(self) -> None:
        """Aliases are plain ``UUID`` so Pydantic treats them
        without extra wrapping. NewType would have added a
        wrapping cost we don't want."""
        assert _TestId is UUID
        assert RecipeId is UUID
        assert EntityId is UUID

    def test_identity_hash_is_str(self) -> None:
        assert IdentityHash is str

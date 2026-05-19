"""Tests for canonicalization error modes.

Per SPEC §6.3.2 + §6.3.4 + §6.3.10 + D-058 §5.7 + D-059. The
canonicalizer fails LOUD rather than producing a possibly-wrong
canonical form. Each error type below carries structured context
so callers can diagnose without parsing message text.

Covers:
  - :class:`UnresolvedSemanticProjectionError` — a field carrying
    a :class:`SemanticField` or :class:`ProjectionField` marker
    cannot be canonicalized in v1.
  - :class:`UnmarkedArraySemanticsError` — a typed list field
    without :class:`ArraySemantics`.
  - :class:`FloatInIdentityBearingContentError` — float at any
    depth.
  - :class:`OntologyViolationError` — StateTransition /
    AutomationEffect canonicalizers raise when a state dict
    references a field external_id not declared in
    ``subject_fields`` / ``affected_fields``.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, Field

from primeqa.test_representation import (
    ArraySemantics,
    AutomationEffectClaimBody,
    BodyBase,
    EventDescriptor,
    FieldChangeEffect,
    FloatInIdentityBearingContentError,
    IdentityBearingRef,
    LiteralValue,
    OntologyViolationError,
    ProjectionField,
    SemanticField,
    StateDescriptor,
    StateTransitionClaimBody,
    UnmarkedArraySemanticsError,
    UnresolvedSemanticProjectionError,
    ValueClaimBody,
    canonicalize,
)


def _ib(external_id: str = "X.Y", entity_id: UUID = None) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type="Field",
        entity_id=entity_id or UUID("00000000-0000-0000-0000-000000000001"),
        version_seq=1,
        external_id=external_id,
    )


# ---------------------------------------------------------------------------
# Fixtures: bodies with intentionally-malformed shape for error testing
# ---------------------------------------------------------------------------

class _SemanticMarkedBody(BodyBase):
    """A body with a field carrying a SemanticField marker —
    intentionally invalid for v1 canonicalization."""

    model_config = ConfigDict(extra="forbid", frozen=False)
    body_schema_version: Literal[1] = 1
    kind: Literal["_test_semantic_marker"] = "_test_semantic_marker"
    name: str = "x"
    # Adding the SemanticField sentinel to a field's metadata via
    # Annotated. Pydantic ignores the marker for validation; the
    # canonicalizer must surface it as a hard error.
    flagged: Annotated[str, SemanticField(role="x")] = "v"


class _ProjectionMarkedBody(BodyBase):
    """A body with a field carrying a ProjectionField marker."""

    model_config = ConfigDict(extra="forbid", frozen=False)
    body_schema_version: Literal[1] = 1
    kind: Literal["_test_projection_marker"] = "_test_projection_marker"
    flagged: Annotated[str, ProjectionField(projects_to="x")] = "v"


class _UnmarkedListBody(BodyBase):
    """A body with a typed list field that lacks ArraySemantics —
    intentionally invalid for v1 canonicalization. Identity-bearing
    list fields MUST declare semantics."""

    model_config = ConfigDict(extra="forbid", frozen=False)
    body_schema_version: Literal[1] = 1
    kind: Literal["_test_unmarked_list"] = "_test_unmarked_list"
    items: list[str] = []


# ---------------------------------------------------------------------------
# UnresolvedSemanticProjectionError
# ---------------------------------------------------------------------------

class TestUnresolvedSemanticProjectionError:
    def test_semantic_field_marker_raises(self) -> None:
        body = _SemanticMarkedBody()
        with pytest.raises(UnresolvedSemanticProjectionError) as exc_info:
            canonicalize(body)
        err = exc_info.value
        assert err.context["body_class"] == "_SemanticMarkedBody"
        assert err.context["field_path"] == "flagged"
        assert err.context["marker_kind"] == "SemanticField"

    def test_projection_field_marker_raises(self) -> None:
        body = _ProjectionMarkedBody()
        with pytest.raises(UnresolvedSemanticProjectionError) as exc_info:
            canonicalize(body)
        err = exc_info.value
        assert err.context["marker_kind"] == "ProjectionField"

    def test_error_message_references_spec_section(self) -> None:
        """Diagnostic value: future readers of the error should
        find a pointer to the SPEC section explaining the
        rule."""
        body = _SemanticMarkedBody()
        with pytest.raises(UnresolvedSemanticProjectionError) as exc_info:
            canonicalize(body)
        assert "6.3.10" in str(exc_info.value)


# ---------------------------------------------------------------------------
# UnmarkedArraySemanticsError
# ---------------------------------------------------------------------------

class TestUnmarkedArraySemanticsError:
    def test_unmarked_typed_list_raises(self) -> None:
        body = _UnmarkedListBody(items=["a", "b"])
        with pytest.raises(UnmarkedArraySemanticsError) as exc_info:
            canonicalize(body)
        err = exc_info.value
        assert err.context["body_class"] == "_UnmarkedListBody"
        assert err.context["field_path"] == "items"

    def test_unmarked_typed_list_empty_still_raises(self) -> None:
        """An empty list shouldn't bypass the marker check —
        absent semantics is still absent semantics, and we want
        the structural invariant enforced at canonicalize time
        regardless of content."""
        body = _UnmarkedListBody(items=[])
        with pytest.raises(UnmarkedArraySemanticsError):
            canonicalize(body)


# ---------------------------------------------------------------------------
# FloatInIdentityBearingContentError
# ---------------------------------------------------------------------------

class TestFloatInIdentityBearingContentError:
    def test_float_at_top_of_literal_value_raises(self) -> None:
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value=3.14),
        )
        with pytest.raises(FloatInIdentityBearingContentError) as exc_info:
            canonicalize(body)
        err = exc_info.value
        # The body_class on the error reflects WHERE we hit the
        # float — the innermost model containing the field.
        # canonicalize walks LiteralValue's "value" field; the
        # field path is "value".
        assert err.context["body_class"] == "LiteralValue"
        assert err.context["field_path"] == "value"
        assert "3.14" in err.context["value_repr"]

    def test_float_nested_in_dict_raises(self) -> None:
        """Float buried inside a nested dict value still raises;
        the path reflects the nesting."""
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(
                value={"outer": {"inner": 2.5}},
            ),
        )
        with pytest.raises(FloatInIdentityBearingContentError) as exc_info:
            canonicalize(body)
        err = exc_info.value
        assert "outer" in err.context["field_path"]
        assert "inner" in err.context["field_path"]

    def test_float_in_list_raises(self) -> None:
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value=[1, 2.0, 3]),
        )
        with pytest.raises(FloatInIdentityBearingContentError):
            canonicalize(body)

    def test_int_not_rejected(self) -> None:
        """Int is not a float. canonicalize handles int verbatim."""
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value=42),
        )
        # Should not raise.
        canonicalize(body)


# ---------------------------------------------------------------------------
# OntologyViolationError (state-transition + automation-effect)
# ---------------------------------------------------------------------------

class TestOntologyViolationOnStateTransition:
    def test_state_dict_references_undeclared_field(self) -> None:
        """A from_state field_values key not present in
        subject_fields → canonicalizer raises
        :class:`OntologyViolationError` per the C1-B contract."""
        subject_id = UUID("00000000-0000-0000-0000-000000000010")
        field_id = UUID("00000000-0000-0000-0000-000000000011")
        body = StateTransitionClaimBody(
            subject=IdentityBearingRef(
                entity_type="Object", entity_id=subject_id,
                version_seq=1, external_id="Opportunity",
            ),
            subject_fields=[
                IdentityBearingRef(
                    entity_type="Field", entity_id=field_id,
                    version_seq=1, external_id="Opportunity.StageName",
                ),
            ],
            from_state=StateDescriptor(field_values={
                # Referenced but NOT in subject_fields:
                "Opportunity.UndeclaredField": LiteralValue(value="x"),
            }),
            to_state=StateDescriptor(field_values={}),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
        )
        with pytest.raises(OntologyViolationError) as exc_info:
            canonicalize(body)
        err = exc_info.value
        assert "Opportunity.UndeclaredField" in str(err)
        assert "from_state" in err.context["field_path"]


class TestOntologyViolationOnAutomationEffect:
    def test_effect_changes_reference_undeclared_field(self) -> None:
        automation_id = UUID("00000000-0000-0000-0000-000000000020")
        affected_id = UUID("00000000-0000-0000-0000-000000000021")
        body = AutomationEffectClaimBody(
            automation=IdentityBearingRef(
                entity_type="Flow", entity_id=automation_id,
                version_seq=1, external_id="StampCloseDate",
            ),
            automation_primitive="flow",
            triggering_action=EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description="update",
            ),
            expected_effect=FieldChangeEffect(
                changes=StateDescriptor(field_values={
                    # Referenced but NOT in affected_fields:
                    "Opportunity.UndeclaredField": LiteralValue(value="x"),
                }),
            ),
            affected_fields=[
                IdentityBearingRef(
                    entity_type="Field", entity_id=affected_id,
                    version_seq=1, external_id="Opportunity.CloseDate",
                ),
            ],
        )
        with pytest.raises(OntologyViolationError) as exc_info:
            canonicalize(body)
        err = exc_info.value
        assert "Opportunity.UndeclaredField" in str(err)
        assert "expected_effect" in err.context["field_path"]


# ---------------------------------------------------------------------------
# Multiple errors at once — defensive: the first one wins
# ---------------------------------------------------------------------------

class TestErrorPrecedence:
    def test_first_violation_surfaces(self) -> None:
        """A body with both an undeclared field AND a float should
        surface the FIRST detected violation. The ontology check
        happens at the state-rewrite step before recursing into
        the value, so it should win."""
        subject_id = UUID("00000000-0000-0000-0000-000000000030")
        body = StateTransitionClaimBody(
            subject=IdentityBearingRef(
                entity_type="Object", entity_id=subject_id,
                version_seq=1, external_id="Opportunity",
            ),
            subject_fields=[],  # nothing declared
            from_state=StateDescriptor(field_values={
                "Opportunity.X": LiteralValue(value=3.14),
            }),
            to_state=StateDescriptor(field_values={}),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
        )
        with pytest.raises(OntologyViolationError):
            canonicalize(body)

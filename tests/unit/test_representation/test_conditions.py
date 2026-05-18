"""Tests for the semantic-conditions body.

Per SPEC §3 (conditions layer is identity-bearing, structurally
uniform) + D-051 + D-058 §5.4.

Covers:

  - :class:`Condition` construction with each of the six predicates.
  - The (predicate, value) coupling validator: value-bearing
    predicates require a value; value-free predicates forbid one.
  - :class:`SemanticConditionsBody` accepts empty + non-empty
    condition lists.
  - Body is registered at import time under
    ``("conditions", 1)``.
  - :class:`Condition.subject` is constructed as an
    :class:`IdentityBearingRef` instance (B-α empirical pattern).
  - Serialization round-trip preserves discriminator data.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from primeqa.test_representation.errors import SchemaIncompatibilityError
from primeqa.test_representation.models.conditions import (
    Condition,
    SemanticConditionsBody,
)
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    PinnedRef,
)
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)


def _ib_ref(name: str = "Account.Industry") -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type="Field",
        entity_id=uuid4(),
        version_seq=1,
        external_id=name,
    )


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

class TestCondition:
    @pytest.mark.parametrize("predicate", [
        "equals", "not_equals", "in_set", "matches_pattern",
    ])
    def test_value_bearing_predicates_accept_value(
        self, predicate: str,
    ) -> None:
        c = Condition(
            subject=_ib_ref(),
            predicate=predicate,  # type: ignore[arg-type]
            value="Tech",
        )
        assert c.predicate == predicate
        assert c.value == "Tech"

    @pytest.mark.parametrize("predicate", ["is_null", "is_not_null"])
    def test_value_free_predicates_accept_no_value(
        self, predicate: str,
    ) -> None:
        c = Condition(
            subject=_ib_ref(),
            predicate=predicate,  # type: ignore[arg-type]
        )
        assert c.predicate == predicate
        assert c.value is None

    @pytest.mark.parametrize("predicate", [
        "equals", "not_equals", "in_set", "matches_pattern",
    ])
    def test_value_bearing_predicates_reject_missing_value(
        self, predicate: str,
    ) -> None:
        with pytest.raises(ValidationError) as exc_info:
            Condition(
                subject=_ib_ref(),
                predicate=predicate,  # type: ignore[arg-type]
            )
        assert "requires" in str(exc_info.value).lower()

    @pytest.mark.parametrize("predicate", ["is_null", "is_not_null"])
    def test_value_free_predicates_reject_provided_value(
        self, predicate: str,
    ) -> None:
        with pytest.raises(ValidationError) as exc_info:
            Condition(
                subject=_ib_ref(),
                predicate=predicate,  # type: ignore[arg-type]
                value="oops",
            )
        assert "forbids" in str(exc_info.value).lower()

    def test_unknown_predicate_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Condition(
                subject=_ib_ref(),
                predicate="contains",  # type: ignore[arg-type]
                value="x",
            )

    def test_subject_is_constructed_as_identity_bearing(self) -> None:
        """B-α empirical pattern: field type IdentityBearingRef →
        Pydantic constructs IdentityBearingRef instance."""
        c = Condition.model_validate({
            "subject": {
                "ref_kind": "pinned",
                "entity_type": "Field",
                "entity_id": str(uuid4()),
                "version_seq": 1,
                "external_id": "Account.Name",
            },
            "predicate": "equals",
            "value": "x",
        })
        assert type(c.subject) is IdentityBearingRef
        assert isinstance(c.subject, PinnedRef)

    def test_subject_rejects_logical_ref(self) -> None:
        with pytest.raises(ValidationError):
            Condition.model_validate({
                "subject": {
                    "ref_kind": "logical",
                    "entity_type": "Field",
                    "external_id": "Account.Name",
                },
                "predicate": "equals",
                "value": "x",
            })

    def test_condition_is_frozen(self) -> None:
        c = Condition(
            subject=_ib_ref(), predicate="equals", value="x",
        )
        with pytest.raises(ValidationError):
            c.value = "y"  # type: ignore[misc]

    def test_condition_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            Condition(  # type: ignore[call-arg]
                subject=_ib_ref(),
                predicate="equals",
                value="x",
                surprise=True,
            )


# ---------------------------------------------------------------------------
# SemanticConditionsBody
# ---------------------------------------------------------------------------

class TestSemanticConditionsBody:
    def test_empty_conditions_permitted(self) -> None:
        body = SemanticConditionsBody()
        assert body.conditions == []
        assert body.body_schema_version == 1
        assert body.kind == "conditions"

    def test_non_empty_conditions(self) -> None:
        body = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib_ref("Account.Industry"),
                predicate="equals",
                value="Tech",
            ),
            Condition(
                subject=_ib_ref("Account.ParentId"),
                predicate="is_null",
            ),
        ])
        assert len(body.conditions) == 2
        assert body.conditions[1].predicate == "is_null"

    def test_round_trip(self) -> None:
        body = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib_ref("Account.Industry"),
                predicate="equals",
                value="Tech",
            ),
        ])
        roundtrip = SemanticConditionsBody.model_validate(body.model_dump())
        assert roundtrip == body
        assert type(roundtrip.conditions[0].subject) is IdentityBearingRef

    def test_universal_keys_present_in_dump(self) -> None:
        body = SemanticConditionsBody()
        dumped = body.model_dump()
        # SPEC §4.4: every body MUST carry these two top-level keys.
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "conditions"

    def test_kind_locked_to_conditions(self) -> None:
        with pytest.raises(ValidationError):
            SemanticConditionsBody(
                kind="value-claim",  # type: ignore[arg-type]
            )

    def test_version_locked_to_1(self) -> None:
        with pytest.raises(ValidationError):
            SemanticConditionsBody(
                body_schema_version=2,  # type: ignore[arg-type]
            )

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            SemanticConditionsBody(
                conditions=[],
                surprise=True,  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------

class TestConditionsRegistration:
    def test_registered_in_default_registry(self) -> None:
        """Importing :mod:`conditions` registers the body via the
        @register_body decorator. The fact that we imported it at
        top-of-file is what triggers registration; this test
        confirms the side effect."""
        assert ("conditions", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model("conditions", 1) is SemanticConditionsBody

    def test_unknown_version_for_conditions_raises(self) -> None:
        with pytest.raises(SchemaIncompatibilityError):
            get_body_model("conditions", 99)


# ---------------------------------------------------------------------------
# ArraySemantics marker (Track C)
# ---------------------------------------------------------------------------

class TestConditionsArraySemanticsMarker:
    def test_conditions_field_marked_set(self) -> None:
        """Per Track C: conditions are AND-composed and order is
        incidental; the field carries :class:`ArraySemantics.SET`
        so canonicalization sorts the list before hashing."""
        from primeqa.test_representation.models.common import ArraySemantics

        metadata = SemanticConditionsBody.model_fields["conditions"].metadata
        assert ArraySemantics.SET in metadata

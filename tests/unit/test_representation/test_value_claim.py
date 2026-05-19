"""Tests for :class:`ValueClaimBody`.

Per SPEC §3 + D-053 (value-claim semantic). Covers:

  - Construction with literal + null expected values.
  - Universal body keys present in serialization.
  - ``kind`` + ``body_schema_version`` locked.
  - ``subject`` constructed as :class:`IdentityBearingRef`
    instance (B-α empirical pattern).
  - Registry registration on import.
  - Logical refs rejected at the identity-bearing slot.
  - Round-trip preserves type discipline.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from primeqa.test_representation.models.claims.data_behavior.value_claim import (
    ValueClaimBody,
)
from primeqa.test_representation.models.primitives import (
    LiteralValue,
    NullValue,
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


class TestValueClaimConstruction:
    def test_minimal_with_literal_value(self) -> None:
        body = ValueClaimBody(
            subject=_ib_ref(),
            expected_value=LiteralValue(value="Tech"),
        )
        assert body.kind == "value-claim"
        assert body.body_schema_version == 1
        assert body.expected_value.value == "Tech"  # type: ignore[union-attr]

    def test_with_null_value(self) -> None:
        body = ValueClaimBody(
            subject=_ib_ref("Account.ParentId"),
            expected_value=NullValue(),
        )
        assert isinstance(body.expected_value, NullValue)

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            ValueClaimBody(
                subject=_ib_ref(),
                expected_value=NullValue(),
                kind="state-transition-claim",  # type: ignore[arg-type]
            )

    def test_version_locked(self) -> None:
        with pytest.raises(ValidationError):
            ValueClaimBody(
                subject=_ib_ref(),
                expected_value=NullValue(),
                body_schema_version=2,  # type: ignore[arg-type]
            )

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            ValueClaimBody(  # type: ignore[call-arg]
                subject=_ib_ref(),
                expected_value=NullValue(),
                surprise=True,
            )


class TestValueClaimSerialization:
    def test_universal_body_keys_present(self) -> None:
        body = ValueClaimBody(
            subject=_ib_ref(),
            expected_value=LiteralValue(value="x"),
        )
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "value-claim"

    def test_round_trip_preserves_identity_bearing_type(self) -> None:
        eid = uuid4()
        body = ValueClaimBody.model_validate({
            "body_schema_version": 1,
            "kind": "value-claim",
            "subject": {
                "ref_kind": "pinned",
                "entity_type": "Field",
                "entity_id": str(eid),
                "version_seq": 1,
                "external_id": "Account.Name",
            },
            "expected_value": {"kind": "literal", "value": "Acme"},
        })
        assert type(body.subject) is IdentityBearingRef
        assert isinstance(body.subject, PinnedRef)
        assert body.subject.entity_id == eid

    def test_full_round_trip(self) -> None:
        body = ValueClaimBody(
            subject=_ib_ref(),
            expected_value=LiteralValue(value=42),
        )
        roundtrip = ValueClaimBody.model_validate(body.model_dump())
        assert roundtrip == body
        assert type(roundtrip.subject) is IdentityBearingRef
        assert type(roundtrip.expected_value) is LiteralValue


class TestValueClaimRefDiscipline:
    def test_logical_ref_rejected_for_subject(self) -> None:
        """Identity-bearing slot rejects logical references per
        D-060 §4.7.6."""
        with pytest.raises(ValidationError):
            ValueClaimBody.model_validate({
                "subject": {
                    "ref_kind": "logical",
                    "entity_type": "Field",
                    "external_id": "Account.Name",
                },
                "expected_value": {"kind": "literal", "value": "x"},
            })


class TestValueClaimRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("value-claim", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model("value-claim", 1) is ValueClaimBody

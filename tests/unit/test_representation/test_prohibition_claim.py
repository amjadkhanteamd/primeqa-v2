"""Tests for :class:`ProhibitionClaimBody`.

Per SPEC §3 + D-053. Covers:

  - Construction with each ``operation`` and
    ``prohibition_mechanism`` value.
  - Identity-bearing slot construction (``target`` and the nested
    ``expected_rejection.error_field``).
  - Logical refs rejected at identity-bearing slots.
  - RejectionSignal's "at least one signal" rule propagates:
    a body with an empty signal is invalid.
  - Registry registration on import.
  - Full round-trip preserves nested type discipline.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from primeqa.test_representation.models.claims.data_behavior.prohibition_claim import (
    ProhibitionClaimBody,
)
from primeqa.test_representation.models.primitives import RejectionSignal
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)


def _ib_ref(
    name: str = "Opportunity", entity_type: str = "Object",
) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type=entity_type,
        entity_id=uuid4(),
        version_seq=1,
        external_id=name,
    )


class TestProhibitionConstruction:
    @pytest.mark.parametrize("operation", [
        "delete", "create_duplicate", "modify_field",
        "modify_record", "share", "transfer_ownership",
    ])
    def test_all_operations_accepted(self, operation: str) -> None:
        body = ProhibitionClaimBody(
            target=_ib_ref(),
            operation=operation,  # type: ignore[arg-type]
            prohibition_mechanism="validation_rule",
            expected_rejection=RejectionSignal(error_code="X"),
        )
        assert body.operation == operation

    @pytest.mark.parametrize("mechanism", [
        "validation_rule", "sharing_rule",
        "apex_trigger", "system_enforced",
    ])
    def test_all_mechanisms_accepted(self, mechanism: str) -> None:
        body = ProhibitionClaimBody(
            target=_ib_ref(),
            operation="delete",
            prohibition_mechanism=mechanism,  # type: ignore[arg-type]
            expected_rejection=RejectionSignal(error_code="X"),
        )
        assert body.prohibition_mechanism == mechanism

    def test_unknown_operation_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProhibitionClaimBody(
                target=_ib_ref(),
                operation="undelete",  # type: ignore[arg-type]
                prohibition_mechanism="validation_rule",
                expected_rejection=RejectionSignal(error_code="X"),
            )

    def test_unknown_mechanism_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProhibitionClaimBody(
                target=_ib_ref(),
                operation="delete",
                prohibition_mechanism="webhook",  # type: ignore[arg-type]
                expected_rejection=RejectionSignal(error_code="X"),
            )

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            ProhibitionClaimBody(
                target=_ib_ref(),
                operation="delete",
                prohibition_mechanism="validation_rule",
                expected_rejection=RejectionSignal(error_code="X"),
                kind="value-claim",  # type: ignore[arg-type]
            )

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            ProhibitionClaimBody(  # type: ignore[call-arg]
                target=_ib_ref(),
                operation="delete",
                prohibition_mechanism="validation_rule",
                expected_rejection=RejectionSignal(error_code="X"),
                surprise=True,
            )


class TestProhibitionRejectionSignalPropagates:
    def test_empty_signal_rejected_at_body_level(self) -> None:
        """The RejectionSignal's at-least-one validator must fire
        when used inside a ProhibitionClaimBody — the body is
        invalid because the embedded signal is invalid."""
        with pytest.raises(ValidationError) as exc_info:
            ProhibitionClaimBody.model_validate({
                "target": {
                    "ref_kind": "pinned",
                    "entity_type": "Object",
                    "entity_id": str(uuid4()),
                    "version_seq": 1,
                    "external_id": "Opportunity",
                },
                "operation": "delete",
                "prohibition_mechanism": "validation_rule",
                "expected_rejection": {},
            })
        assert "at least one" in str(exc_info.value).lower()


class TestProhibitionRefDiscipline:
    def test_target_constructed_as_identity_bearing(self) -> None:
        body = ProhibitionClaimBody.model_validate({
            "target": {
                "ref_kind": "pinned",
                "entity_type": "Object",
                "entity_id": str(uuid4()),
                "version_seq": 1,
                "external_id": "Opportunity",
            },
            "operation": "delete",
            "prohibition_mechanism": "validation_rule",
            "expected_rejection": {"error_code": "X"},
        })
        assert type(body.target) is IdentityBearingRef

    def test_error_field_constructed_as_identity_bearing(self) -> None:
        body = ProhibitionClaimBody.model_validate({
            "target": {
                "ref_kind": "pinned",
                "entity_type": "Object",
                "entity_id": str(uuid4()),
                "version_seq": 1,
                "external_id": "Opportunity",
            },
            "operation": "modify_field",
            "prohibition_mechanism": "validation_rule",
            "expected_rejection": {
                "error_field": {
                    "ref_kind": "pinned",
                    "entity_type": "Field",
                    "entity_id": str(uuid4()),
                    "version_seq": 1,
                    "external_id": "Opportunity.StageName",
                },
            },
        })
        assert type(body.expected_rejection.error_field) is IdentityBearingRef

    def test_logical_target_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProhibitionClaimBody.model_validate({
                "target": {
                    "ref_kind": "logical",
                    "entity_type": "Object",
                    "external_id": "Opportunity",
                },
                "operation": "delete",
                "prohibition_mechanism": "validation_rule",
                "expected_rejection": {"error_code": "X"},
            })


class TestProhibitionSerialization:
    def test_universal_body_keys_present(self) -> None:
        body = ProhibitionClaimBody(
            target=_ib_ref(),
            operation="delete",
            prohibition_mechanism="validation_rule",
            expected_rejection=RejectionSignal(error_code="X"),
        )
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "prohibition-claim"

    def test_full_round_trip_with_nested_error_field(self) -> None:
        body = ProhibitionClaimBody(
            target=_ib_ref(),
            operation="modify_field",
            prohibition_mechanism="validation_rule",
            expected_rejection=RejectionSignal(
                error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION",
                error_message_pattern="cannot.*modify",
                error_field=IdentityBearingRef(
                    entity_type="Field",
                    entity_id=uuid4(),
                    version_seq=1,
                    external_id="Opportunity.StageName",
                ),
            ),
        )
        roundtrip = ProhibitionClaimBody.model_validate(body.model_dump())
        assert roundtrip == body
        assert type(roundtrip.target) is IdentityBearingRef
        assert type(roundtrip.expected_rejection.error_field) is IdentityBearingRef


class TestProhibitionRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("prohibition-claim", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model(
            "prohibition-claim", 1,
        ) is ProhibitionClaimBody

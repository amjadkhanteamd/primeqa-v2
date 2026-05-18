"""Tests for the cross-archetype :data:`ClaimBody` discriminated union.

Per SPEC §4.7.2 + D-053 (claim-kind taxonomy purity guardrail —
every kind value is unique across the entire taxonomy). Covers:

  - Each of the four data-behavior claim kinds dispatches to the
    correct concrete body class.
  - Unknown ``kind`` values are rejected.
  - Round-trip through the union preserves the concrete type.

The union is **flat** (not nested per-archetype) so that
substrate consumers can dispatch with one ``TypeAdapter`` call.
Future archetype claim bodies extend this union; the kind-value
uniqueness guarantee keeps the dispatch unambiguous.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation import (
    AutomationEffectClaimBody,
    ClaimBody,
    ProhibitionClaimBody,
    StateTransitionClaimBody,
    ValueClaimBody,
)


def _pinned_field(name: str = "Account.Industry") -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": "Field",
        "entity_id": str(uuid4()),
        "version_seq": 1,
        "external_id": name,
    }


def _pinned_object(name: str = "Opportunity") -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": "Object",
        "entity_id": str(uuid4()),
        "version_seq": 1,
        "external_id": name,
    }


@pytest.fixture
def adapter() -> TypeAdapter:
    return TypeAdapter(ClaimBody)


class TestClaimUnionDispatch:
    def test_value_claim_dispatch(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "value-claim",
            "subject": _pinned_field(),
            "expected_value": {"kind": "literal", "value": "Tech"},
        })
        assert type(result) is ValueClaimBody

    def test_state_transition_claim_dispatch(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "state-transition-claim",
            "subject": _pinned_object(),
            "subject_fields": [_pinned_field("Opportunity.StageName")],
            "from_state": {"field_values": {
                "Opportunity.StageName": {"kind": "literal",
                                            "value": "Prospecting"},
            }},
            "to_state": {"field_values": {
                "Opportunity.StageName": {"kind": "literal",
                                            "value": "Closed Won"},
            }},
            "triggering_event": {
                "trigger_kind": "ui-trigger",
                "description": "click close",
            },
        })
        assert type(result) is StateTransitionClaimBody

    def test_automation_effect_claim_dispatch(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "automation-effect-claim",
            "automation": _pinned_object("Stamp Close Date"),
            "automation_primitive": "flow",
            "triggering_action": {
                "trigger_kind": "data-mutation-trigger",
                "description": "update",
            },
            "expected_effect": {
                "kind": "side_effect",
                "description": "email",
            },
            "affected_fields": [],
        })
        assert type(result) is AutomationEffectClaimBody

    def test_prohibition_claim_dispatch(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "prohibition-claim",
            "target": _pinned_object(),
            "operation": "delete",
            "prohibition_mechanism": "validation_rule",
            "expected_rejection": {"error_code": "X"},
        })
        assert type(result) is ProhibitionClaimBody


class TestClaimUnionRejection:
    def test_unknown_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "body_schema_version": 1,
                "kind": "platform-event-claim",  # future archetype
                "subject": _pinned_field(),
            })

    def test_missing_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "body_schema_version": 1,
                "subject": _pinned_field(),
                "expected_value": {"kind": "literal", "value": "x"},
            })

    def test_trigger_kind_value_does_not_collide(
        self, adapter: TypeAdapter,
    ) -> None:
        """Trigger-kind values (e.g., ``"ui-trigger"``) are
        distinct from claim-kind values per D-053 purity. A
        ``kind="ui-trigger"`` payload is NOT a claim body and
        must be rejected by the claim-body union."""
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "body_schema_version": 1,
                "kind": "ui-trigger",
                "action_type": "button_click",
            })


class TestClaimUnionRoundTrip:
    def test_value_claim_round_trips(self, adapter: TypeAdapter) -> None:
        original = ValueClaimBody.model_validate({
            "body_schema_version": 1,
            "kind": "value-claim",
            "subject": _pinned_field(),
            "expected_value": {"kind": "literal", "value": "Tech"},
        })
        roundtrip = adapter.validate_python(original.model_dump())
        assert type(roundtrip) is ValueClaimBody
        assert roundtrip == original

    def test_prohibition_claim_round_trips(
        self, adapter: TypeAdapter,
    ) -> None:
        original = ProhibitionClaimBody.model_validate({
            "body_schema_version": 1,
            "kind": "prohibition-claim",
            "target": _pinned_object(),
            "operation": "modify_field",
            "prohibition_mechanism": "validation_rule",
            "expected_rejection": {
                "error_code": "FIELD_CUSTOM_VALIDATION_EXCEPTION",
                "error_message_pattern": "required",
            },
        })
        roundtrip = adapter.validate_python(original.model_dump())
        assert type(roundtrip) is ProhibitionClaimBody
        assert roundtrip == original


class TestClaimUnionViaTopLevelImport:
    """The cross-archetype union should be importable from the
    top-level package — that's the substrate's primary
    consumer-facing dispatch type."""

    def test_claim_body_importable_from_top_level(self) -> None:
        from primeqa.test_representation import ClaimBody as TopClaim
        assert TopClaim is ClaimBody

"""Tests for the :data:`DataBehaviorClaimBody` discriminated union.

Per SPEC §4.7.2: the four data-behavior claim bodies share the
universal ``kind`` discriminator declared on :class:`BodyBase`.
Pydantic dispatches by ``kind`` to the right concrete class.

Covers:

  - Each of the four kinds dispatches to the correct body class.
  - Unknown ``kind`` is rejected.
  - Missing ``kind`` is rejected.
  - Round-trip through the union preserves the concrete type.
  - Importing the data_behavior package alone registers all four
    bodies in :data:`default_registry`.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.claims.data_behavior import (
    AutomationEffectClaimBody,
    DataBehaviorClaimBody,
    ProhibitionClaimBody,
    StateTransitionClaimBody,
    ValueClaimBody,
)
from primeqa.test_representation.models.registry import default_registry


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
    return TypeAdapter(DataBehaviorClaimBody)


class TestDataBehaviorUnionDispatch:
    def test_value_claim_lands(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "value-claim",
            "subject": _pinned_field(),
            "expected_value": {"kind": "literal", "value": "Tech"},
        })
        assert type(result) is ValueClaimBody

    def test_state_transition_claim_lands(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "state-transition-claim",
            "subject": _pinned_object(),
            "subject_fields": [_pinned_field("Opportunity.StageName")],
            "from_state": {"field_values": {
                "Opportunity.StageName": {"kind": "literal", "value": "Prospecting"},
            }},
            "to_state": {"field_values": {
                "Opportunity.StageName": {"kind": "literal", "value": "Closed Won"},
            }},
            "triggering_event": {
                "trigger_kind": "ui-trigger",
                "description": "close opportunity",
            },
        })
        assert type(result) is StateTransitionClaimBody

    def test_automation_effect_claim_lands(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "automation-effect-claim",
            "automation": _pinned_object("Stamp Close Date"),
            "automation_primitive": "flow",
            "triggering_action": {
                "trigger_kind": "data-mutation-trigger",
                "description": "record update",
            },
            "expected_effect": {
                "kind": "side_effect",
                "description": "email sent",
            },
            "affected_fields": [],
        })
        assert type(result) is AutomationEffectClaimBody

    def test_prohibition_claim_lands(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "prohibition-claim",
            "target": _pinned_object(),
            "operation": "delete",
            "prohibition_mechanism": "validation_rule",
            "expected_rejection": {"error_code": "X"},
        })
        assert type(result) is ProhibitionClaimBody


class TestDataBehaviorUnionRejection:
    def test_unknown_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "body_schema_version": 1,
                "kind": "platform-event-claim",  # different archetype
                "subject": _pinned_field(),
            })

    def test_missing_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "body_schema_version": 1,
                "subject": _pinned_field(),
                "expected_value": {"kind": "literal", "value": "x"},
            })


class TestDataBehaviorUnionRoundTrip:
    def test_value_claim_round_trips_through_union(
        self, adapter: TypeAdapter,
    ) -> None:
        original = ValueClaimBody.model_validate({
            "body_schema_version": 1,
            "kind": "value-claim",
            "subject": _pinned_field(),
            "expected_value": {"kind": "literal", "value": "Tech"},
        })
        # Dump and re-validate through the union to confirm the
        # discriminator survives the trip.
        roundtrip = adapter.validate_python(original.model_dump())
        assert type(roundtrip) is ValueClaimBody
        assert roundtrip == original


class TestDataBehaviorUnionRegistersAll:
    def test_importing_subpackage_registers_all_four(self) -> None:
        """Importing :mod:`primeqa.test_representation.models.claims.data_behavior`
        at top-of-file triggers @register_body on each of the four
        claim modules. Verify each lands in default_registry."""
        for kind in (
            "value-claim",
            "state-transition-claim",
            "automation-effect-claim",
            "prohibition-claim",
        ):
            assert (kind, 1) in default_registry, (
                f"data_behavior import did not register {kind!r}"
            )

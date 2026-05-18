"""Tests for the :data:`TriggerBody` discriminated union.

Per SPEC §4.7.2 + D-055. Covers:

  - Each of the 5 trigger-kinds dispatches to the correct
    concrete body class via the ``kind`` discriminator.
  - Unknown / missing ``kind`` rejected.
  - Round-trip through the union preserves the concrete type.
  - Importing the triggers package alone registers all five
    body kinds in :data:`default_registry`.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.registry import default_registry
from primeqa.test_representation.models.triggers import (
    ConfigurationTriggerBody,
    DataMutationTriggerBody,
    InboundTriggerBody,
    TimeTriggerBody,
    TriggerBody,
    UITriggerBody,
)


def _logical(entity_type: str, external_id: str) -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": entity_type,
        "external_id": external_id,
    }


def _pinned(entity_type: str, external_id: str) -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": entity_type,
        "entity_id": str(uuid4()),
        "version_seq": 1,
        "external_id": external_id,
    }


@pytest.fixture
def adapter() -> TypeAdapter:
    return TypeAdapter(TriggerBody)


class TestTriggerUnionDispatch:
    def test_inbound_trigger_lands(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "inbound-trigger",
            "channel": "rest",
            "payload": {"format": "json", "content": {}},
        })
        assert type(result) is InboundTriggerBody

    def test_data_mutation_trigger_lands(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "data-mutation-trigger",
            "operation": "create",
            "target": _logical("Object", "Opportunity"),
            "identity_context": "system",
            "volume": "single",
        })
        assert type(result) is DataMutationTriggerBody

    def test_ui_trigger_lands(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "ui-trigger",
            "action_type": "button_click",
            "page_context": {"page_kind": "record_detail"},
        })
        assert type(result) is UITriggerBody

    def test_time_trigger_lands(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "time-trigger",
            "mechanism": "scheduled_flow",
            "time_predicate": {
                "predicate_kind": "after_duration",
                "duration_seconds": 60,
            },
        })
        assert type(result) is TimeTriggerBody

    def test_configuration_trigger_lands(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "configuration-trigger",
            "deploy_target": "activation_flag",
            "affected_entity": _logical(
                "ValidationRule", "Account.RequireIndustry",
            ),
            "change_description": {
                "kind": "activation", "new_state": "active",
            },
        })
        assert type(result) is ConfigurationTriggerBody


class TestTriggerUnionRejection:
    def test_unknown_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "body_schema_version": 1,
                "kind": "manual-trigger",
                "operation": "create",
            })

    def test_missing_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "body_schema_version": 1,
                "channel": "rest",
                "payload": {"format": "json", "content": {}},
            })


class TestTriggerUnionRoundTrip:
    def test_inbound_round_trips(self, adapter: TypeAdapter) -> None:
        original = InboundTriggerBody.model_validate({
            "channel": "rest",
            "payload": {"format": "json", "content": {"AccountId": "x"}},
            "endpoint": _pinned("ApexClass", "WebhookHandler"),
        })
        roundtrip = adapter.validate_python(original.model_dump())
        assert type(roundtrip) is InboundTriggerBody
        assert roundtrip == original

    def test_configuration_round_trips(
        self, adapter: TypeAdapter,
    ) -> None:
        original = ConfigurationTriggerBody.model_validate({
            "deploy_target": "permission_grant",
            "affected_entity": _logical("PermissionSet", "AccountFull"),
            "change_description": {
                "kind": "permission_grant",
                "subject_type": "permission_set",
                "permission_value": {"AccountRead": True},
            },
        })
        roundtrip = adapter.validate_python(original.model_dump())
        assert type(roundtrip) is ConfigurationTriggerBody
        assert roundtrip == original


class TestTriggerUnionRegistersAll:
    def test_importing_triggers_subpackage_registers_all_five(self) -> None:
        for kind in (
            "inbound-trigger",
            "data-mutation-trigger",
            "ui-trigger",
            "time-trigger",
            "configuration-trigger",
        ):
            assert (kind, 1) in default_registry, (
                f"triggers import did not register {kind!r}"
            )

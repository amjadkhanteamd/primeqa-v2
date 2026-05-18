"""Tests for the :data:`ObservationRealizationBody` discriminated union.

Per SPEC §4.7.2 + D-054. Covers:

  - Each of the 5 recipe-kinds dispatches to the correct concrete
    body class via the ``kind`` discriminator.
  - Unknown / missing ``kind`` rejected.
  - Round-trip through the union preserves the concrete type.
  - Importing the recipes package alone registers all five body
    kinds in :data:`default_registry`.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.recipes import (
    CalloutInterceptRecipeBody,
    DataRecipeBody,
    EventSubscriptionRecipeBody,
    MetadataRecipeBody,
    ObservationRealizationBody,
    UIRecipeBody,
)
from primeqa.test_representation.models.registry import default_registry


def _logical(entity_type: str, ext_id: str) -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": entity_type,
        "external_id": ext_id,
    }


def _pinned(entity_type: str, ext_id: str) -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": entity_type,
        "entity_id": str(uuid4()),
        "version_seq": 1,
        "external_id": ext_id,
    }


@pytest.fixture
def adapter() -> TypeAdapter:
    return TypeAdapter(ObservationRealizationBody)


class TestRecipeUnionDispatch:
    def test_data_recipe_lands(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "data-recipe",
            "api_choice": "rest",
            "identity_context": "system",
            "execution_mechanism": "direct_api",
            "steps": [],
        })
        assert type(result) is DataRecipeBody

    def test_metadata_recipe_lands(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "metadata-recipe",
            "mode": "metadata_read",
            "api_choice": "metadata_api",
            "steps": [],
        })
        assert type(result) is MetadataRecipeBody

    def test_ui_recipe_lands(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "ui-recipe",
            "framework": "playwright",
            "identity_context": "system",
            "steps": [],
        })
        assert type(result) is UIRecipeBody

    def test_event_subscription_lands(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "event-subscription-recipe",
            "channel_type": "platform_event",
            "subscription_mode": "ephemeral",
            "channel_target": _logical(
                "PlatformEvent", "AccountUpdated__e",
            ),
            "steps": [],
        })
        assert type(result) is EventSubscriptionRecipeBody

    def test_callout_intercept_lands(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "body_schema_version": 1,
            "kind": "callout-intercept-recipe",
            "interception_mechanism": "proxy",
            "steps": [],
        })
        assert type(result) is CalloutInterceptRecipeBody


class TestRecipeUnionRejection:
    def test_unknown_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "body_schema_version": 1,
                "kind": "soql-recipe",
            })

    def test_missing_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "body_schema_version": 1,
                "api_choice": "rest",
            })


class TestRecipeUnionRoundTrip:
    def test_data_recipe_round_trips(self, adapter: TypeAdapter) -> None:
        original = DataRecipeBody.model_validate({
            "api_choice": "rest",
            "identity_context": "system",
            "execution_mechanism": "direct_api",
            "steps": [{
                "kind": "create", "step_id": "s1",
                "target_object": _pinned("Object", "Account"),
                "field_values": {"Name": "x"},
            }],
        })
        roundtrip = adapter.validate_python(original.model_dump())
        assert type(roundtrip) is DataRecipeBody
        assert roundtrip == original

    def test_metadata_recipe_round_trips(
        self, adapter: TypeAdapter,
    ) -> None:
        original = MetadataRecipeBody.model_validate({
            "mode": "metadata_write",
            "api_choice": "tooling_api",
            "steps": [{
                "kind": "deploy", "step_id": "s1",
                "deployment_payload": {"x": 1},
            }],
        })
        roundtrip = adapter.validate_python(original.model_dump())
        assert type(roundtrip) is MetadataRecipeBody
        assert roundtrip == original


class TestRecipeUnionRegistersAll:
    def test_importing_recipes_registers_all_five(self) -> None:
        for kind in (
            "data-recipe",
            "metadata-recipe",
            "ui-recipe",
            "event-subscription-recipe",
            "callout-intercept-recipe",
        ):
            assert (kind, 1) in default_registry, (
                f"recipes import did not register {kind!r}"
            )

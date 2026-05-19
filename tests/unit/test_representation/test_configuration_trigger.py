"""Tests for :class:`ConfigurationTriggerBody` and the
:data:`ConfigurationChange` discriminated union.

Per SPEC §3 + D-055. Covers:

  - All 4 deploy_target values accepted.
  - ConfigurationChange union: each of the 4 shape variants
    dispatches correctly via the ``kind`` discriminator.
  - Unknown / missing ``kind`` rejected.
  - OperationalRef behaviour on ``affected_entity``.
  - Nested-union round-trip: a body with a ConfigurationChange
    variant survives model_dump + model_validate.
  - Registry registration.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
    PinnedRef,
)
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)
from primeqa.test_representation.models.triggers.configuration import (
    ActivationChange,
    ConfigurationChange,
    ConfigurationTriggerBody,
    EntityLifecycleChange,
    PermissionGrantChange,
    PropertyChange,
)


def _logical_validation_rule() -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": "ValidationRule",
        "external_id": "Account.RequireIndustry",
    }


def _pinned_profile() -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": "Profile",
        "entity_id": str(uuid4()),
        "version_seq": 2,
        "external_id": "Standard User",
    }


# ---------------------------------------------------------------------------
# ConfigurationChange union dispatch
# ---------------------------------------------------------------------------

class TestConfigurationChangeUnion:
    @pytest.fixture
    def adapter(self) -> TypeAdapter:
        return TypeAdapter(ConfigurationChange)

    def test_activation_change_dispatches(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "kind": "activation",
            "new_state": "active",
        })
        assert type(result) is ActivationChange
        assert result.new_state == "active"

    def test_property_change_dispatches(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "kind": "property",
            "property_name": "Description",
            "new_value": "Updated description",
        })
        assert type(result) is PropertyChange
        assert result.property_name == "Description"

    def test_entity_lifecycle_dispatches(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "kind": "entity_lifecycle",
            "lifecycle_event": "created",
        })
        assert type(result) is EntityLifecycleChange

    def test_permission_grant_dispatches(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "kind": "permission_grant",
            "subject_type": "permission_set",
            "permission_value": {"AccountRead": True, "AccountEdit": False},
        })
        assert type(result) is PermissionGrantChange
        assert result.permission_value["AccountRead"] is True

    def test_unknown_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({"kind": "feature_flag"})

    def test_missing_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({"new_state": "active"})

    def test_activation_rejects_unknown_state(self) -> None:
        with pytest.raises(ValidationError):
            ActivationChange(
                new_state="paused",  # type: ignore[arg-type]
            )

    def test_entity_lifecycle_rejects_unknown_event(self) -> None:
        with pytest.raises(ValidationError):
            EntityLifecycleChange(
                lifecycle_event="renamed",  # type: ignore[arg-type]
            )

    def test_permission_grant_rejects_unknown_subject(self) -> None:
        with pytest.raises(ValidationError):
            PermissionGrantChange(
                subject_type="role",  # type: ignore[arg-type]
                permission_value={},
            )

    def test_all_variants_frozen(self) -> None:
        a = ActivationChange(new_state="active")
        with pytest.raises(ValidationError):
            a.new_state = "inactive"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ConfigurationTriggerBody
# ---------------------------------------------------------------------------

class TestConfigurationTriggerConstruction:
    @pytest.mark.parametrize("deploy_target", [
        "activation_flag", "property_change",
        "entity_create_or_delete", "permission_grant",
    ])
    def test_all_deploy_targets_accepted(self, deploy_target: str) -> None:
        body = ConfigurationTriggerBody.model_validate({
            "deploy_target": deploy_target,
            "affected_entity": _logical_validation_rule(),
            "change_description": {
                "kind": "activation", "new_state": "active",
            },
        })
        assert body.deploy_target == deploy_target

    def test_unknown_deploy_target_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ConfigurationTriggerBody.model_validate({
                "deploy_target": "ui_change",
                "affected_entity": _logical_validation_rule(),
                "change_description": {
                    "kind": "activation", "new_state": "active",
                },
            })

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            ConfigurationTriggerBody.model_validate({
                "kind": "ui-trigger",
                "deploy_target": "activation_flag",
                "affected_entity": _logical_validation_rule(),
                "change_description": {
                    "kind": "activation", "new_state": "active",
                },
            })

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            ConfigurationTriggerBody.model_validate({
                "deploy_target": "activation_flag",
                "affected_entity": _logical_validation_rule(),
                "change_description": {
                    "kind": "activation", "new_state": "active",
                },
                "surprise": True,
            })


class TestConfigurationTriggerOperationalRef:
    def test_pinned_affected_entity_lands_as_pinned_ref(self) -> None:
        body = ConfigurationTriggerBody.model_validate({
            "deploy_target": "permission_grant",
            "affected_entity": _pinned_profile(),
            "change_description": {
                "kind": "permission_grant",
                "subject_type": "profile",
                "permission_value": {"OpportunityRead": True},
            },
        })
        assert type(body.affected_entity) is PinnedRef
        assert not isinstance(body.affected_entity, IdentityBearingRef)

    def test_logical_affected_entity_lands_as_logical_ref(self) -> None:
        body = ConfigurationTriggerBody.model_validate({
            "deploy_target": "activation_flag",
            "affected_entity": _logical_validation_rule(),
            "change_description": {
                "kind": "activation", "new_state": "active",
            },
        })
        assert type(body.affected_entity) is LogicalRef


class TestConfigurationTriggerNestedUnion:
    """Verify the nested ConfigurationChange union dispatches
    correctly when read through the body's validation path."""

    def test_body_with_activation_change(self) -> None:
        body = ConfigurationTriggerBody.model_validate({
            "deploy_target": "activation_flag",
            "affected_entity": _logical_validation_rule(),
            "change_description": {
                "kind": "activation", "new_state": "inactive",
            },
        })
        assert type(body.change_description) is ActivationChange

    def test_body_with_property_change(self) -> None:
        body = ConfigurationTriggerBody.model_validate({
            "deploy_target": "property_change",
            "affected_entity": _logical_validation_rule(),
            "change_description": {
                "kind": "property",
                "property_name": "ErrorMessage",
                "new_value": "Industry is required.",
            },
        })
        assert type(body.change_description) is PropertyChange

    def test_body_with_entity_lifecycle(self) -> None:
        body = ConfigurationTriggerBody.model_validate({
            "deploy_target": "entity_create_or_delete",
            "affected_entity": _logical_validation_rule(),
            "change_description": {
                "kind": "entity_lifecycle", "lifecycle_event": "deleted",
            },
        })
        assert type(body.change_description) is EntityLifecycleChange

    def test_body_with_permission_grant(self) -> None:
        body = ConfigurationTriggerBody.model_validate({
            "deploy_target": "permission_grant",
            "affected_entity": _pinned_profile(),
            "change_description": {
                "kind": "permission_grant",
                "subject_type": "permission_set",
                "permission_value": {"AccountRead": True},
            },
        })
        assert type(body.change_description) is PermissionGrantChange


class TestConfigurationTriggerSerialization:
    def test_universal_body_keys_present(self) -> None:
        body = ConfigurationTriggerBody.model_validate({
            "deploy_target": "activation_flag",
            "affected_entity": _logical_validation_rule(),
            "change_description": {
                "kind": "activation", "new_state": "active",
            },
        })
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "configuration-trigger"

    def test_full_round_trip_preserves_nested_union_type(self) -> None:
        body = ConfigurationTriggerBody.model_validate({
            "deploy_target": "permission_grant",
            "affected_entity": _pinned_profile(),
            "change_description": {
                "kind": "permission_grant",
                "subject_type": "profile",
                "permission_value": {"AccountRead": True, "AccountEdit": True},
            },
        })
        roundtrip = ConfigurationTriggerBody.model_validate(
            body.model_dump(),
        )
        assert roundtrip == body
        assert type(roundtrip.affected_entity) is PinnedRef
        assert type(roundtrip.change_description) is PermissionGrantChange


class TestConfigurationTriggerRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("configuration-trigger", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model(
            "configuration-trigger", 1,
        ) is ConfigurationTriggerBody

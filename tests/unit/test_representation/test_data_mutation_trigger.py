"""Tests for :class:`DataMutationTriggerBody`.

Per SPEC §3 + D-055. Covers:

  - All 5 operations + 2 identity contexts + 2 volume values.
  - Cross-field validator: ``identity_context="run_as_user"`` ↔
    ``run_as_user`` non-None.
  - OperationalRef behaviour for ``target`` and ``run_as_user``
    (pinned → PinnedRef, logical → LogicalRef, never
    IdentityBearingRef).
  - field_changes round-trip with realistic dict shape.
  - Registry registration.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
    PinnedRef,
)
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)
from primeqa.test_representation.models.triggers.data_mutation import (
    DataMutationTriggerBody,
)


def _pinned_target() -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": "Object",
        "entity_id": str(uuid4()),
        "version_seq": 3,
        "external_id": "Opportunity",
    }


def _logical_target() -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": "Object",
        "external_id": "Opportunity",
    }


def _logical_user() -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": "User",
        "external_id": "sales.rep@example.com",
    }


class TestDataMutationConstruction:
    @pytest.mark.parametrize("operation", [
        "create", "update", "delete", "undelete", "upsert",
    ])
    def test_all_operations_accepted(self, operation: str) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": operation,
            "target": _logical_target(),
            "identity_context": "system",
            "volume": "single",
        })
        assert body.operation == operation

    def test_unknown_operation_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DataMutationTriggerBody.model_validate({
                "operation": "merge",
                "target": _logical_target(),
                "identity_context": "system",
                "volume": "single",
            })

    @pytest.mark.parametrize("volume", ["single", "bulk"])
    def test_both_volumes_accepted(self, volume: str) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": "create",
            "target": _logical_target(),
            "identity_context": "system",
            "volume": volume,
        })
        assert body.volume == volume

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            DataMutationTriggerBody.model_validate({
                "kind": "ui-trigger",
                "operation": "create",
                "target": _logical_target(),
                "identity_context": "system",
                "volume": "single",
            })

    def test_field_changes_round_trip(self) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": "update",
            "target": _logical_target(),
            "identity_context": "system",
            "volume": "single",
            "field_changes": {
                "Status": "Approved",
                "ApprovedDate": "2026-05-18",
                "Amount": 12_500.50,
            },
        })
        assert body.field_changes is not None
        assert body.field_changes["Status"] == "Approved"
        roundtrip = DataMutationTriggerBody.model_validate(body.model_dump())
        assert roundtrip == body

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            DataMutationTriggerBody.model_validate({
                "operation": "create",
                "target": _logical_target(),
                "identity_context": "system",
                "volume": "single",
                "surprise": True,
            })


class TestDataMutationIdentityContextCoupling:
    def test_run_as_user_with_user_set_ok(self) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": "create",
            "target": _logical_target(),
            "identity_context": "run_as_user",
            "run_as_user": _logical_user(),
            "volume": "single",
        })
        assert isinstance(body.run_as_user, LogicalRef)

    def test_run_as_user_without_user_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DataMutationTriggerBody.model_validate({
                "operation": "create",
                "target": _logical_target(),
                "identity_context": "run_as_user",
                "volume": "single",
            })
        assert "run_as_user" in str(exc_info.value)

    def test_system_without_user_ok(self) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": "create",
            "target": _logical_target(),
            "identity_context": "system",
            "volume": "single",
        })
        assert body.run_as_user is None

    def test_system_with_user_rejected(self) -> None:
        """An explicit run_as_user under system identity is a
        misconfiguration — likely a typo on identity_context.
        Fail loudly."""
        with pytest.raises(ValidationError) as exc_info:
            DataMutationTriggerBody.model_validate({
                "operation": "create",
                "target": _logical_target(),
                "identity_context": "system",
                "run_as_user": _logical_user(),
                "volume": "single",
            })
        assert "forbids" in str(exc_info.value).lower()


class TestDataMutationOperationalRef:
    def test_pinned_target_lands_as_pinned_ref(self) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": "create",
            "target": _pinned_target(),
            "identity_context": "system",
            "volume": "single",
        })
        assert type(body.target) is PinnedRef
        assert not isinstance(body.target, IdentityBearingRef)

    def test_logical_target_lands_as_logical_ref(self) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": "create",
            "target": _logical_target(),
            "identity_context": "system",
            "volume": "single",
        })
        assert type(body.target) is LogicalRef

    def test_pinned_run_as_user_lands_as_pinned_ref(self) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": "create",
            "target": _logical_target(),
            "identity_context": "run_as_user",
            "run_as_user": {
                "ref_kind": "pinned",
                "entity_type": "User",
                "entity_id": str(uuid4()),
                "version_seq": 1,
                "external_id": "rep@example.com",
            },
            "volume": "single",
        })
        assert type(body.run_as_user) is PinnedRef
        assert not isinstance(body.run_as_user, IdentityBearingRef)


class TestDataMutationSerialization:
    def test_universal_body_keys_present(self) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": "create",
            "target": _logical_target(),
            "identity_context": "system",
            "volume": "single",
        })
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "data-mutation-trigger"

    def test_full_round_trip(self) -> None:
        body = DataMutationTriggerBody.model_validate({
            "operation": "update",
            "target": _pinned_target(),
            "identity_context": "run_as_user",
            "run_as_user": _logical_user(),
            "volume": "bulk",
            "field_changes": {"StageName": "Closed Won"},
        })
        roundtrip = DataMutationTriggerBody.model_validate(body.model_dump())
        assert roundtrip == body
        assert type(roundtrip.target) is PinnedRef
        assert type(roundtrip.run_as_user) is LogicalRef


class TestDataMutationRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("data-mutation-trigger", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model(
            "data-mutation-trigger", 1,
        ) is DataMutationTriggerBody

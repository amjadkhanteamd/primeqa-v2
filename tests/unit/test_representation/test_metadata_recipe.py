"""Tests for :class:`MetadataRecipeBody`.

Per SPEC §3 + D-054. Covers:

  - 4 step variants + mode + api_choice closed taxonomies.
  - Body-level validator: DeployStep rejected when mode is
    metadata_read; accepted when mode is metadata_write.
  - OperationalRef behaviour on step targets.
  - ORDERED marker on steps.
  - Registry registration.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from primeqa.test_representation.models.common import ArraySemantics
from primeqa.test_representation.models.recipes.metadata_recipe import (
    AssertStep,
    DeployStep,
    MetadataRecipeBody,
    ReadMetadataStep,
    RetrieveStep,
)
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
    PinnedRef,
)
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)


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


def _body(**overrides) -> dict:
    base = {
        "mode": "metadata_read",
        "api_choice": "metadata_api",
        "steps": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Step variants
# ---------------------------------------------------------------------------

class TestMetadataStepVariants:
    def test_read_metadata_step(self) -> None:
        s = ReadMetadataStep.model_validate({
            "step_id": "s1",
            "target_entity": _logical("CustomObject", "Account"),
            "fields_to_capture": ["fields", "validationRules"],
        })
        assert s.kind == "read_metadata"

    def test_deploy_step(self) -> None:
        s = DeployStep.model_validate({
            "step_id": "s2",
            "deployment_payload": {"CustomField": {"fullName": "Account.Foo__c"}},
        })
        assert s.kind == "deploy"

    def test_retrieve_step(self) -> None:
        s = RetrieveStep.model_validate({
            "step_id": "s3",
            "target_entity": _logical("CustomField", "Account.Foo__c"),
            "into_field": "captured_field",
        })
        assert s.into_field == "captured_field"

    def test_assert_step(self) -> None:
        s = AssertStep.model_validate({
            "step_id": "s4",
            "predicate": {
                "subject_ref": "s3.captured_field",
                "predicate": "exists",
            },
        })
        assert s.kind == "assert"


# ---------------------------------------------------------------------------
# Mode + api_choice
# ---------------------------------------------------------------------------

class TestMetadataRecipeMode:
    @pytest.mark.parametrize("mode", ["metadata_read", "metadata_write"])
    def test_both_modes_accepted(self, mode: str) -> None:
        body = MetadataRecipeBody.model_validate(_body(mode=mode))
        assert body.mode == mode

    @pytest.mark.parametrize("api_choice", ["metadata_api", "tooling_api"])
    def test_both_api_choices_accepted(self, api_choice: str) -> None:
        body = MetadataRecipeBody.model_validate(
            _body(api_choice=api_choice),
        )
        assert body.api_choice == api_choice

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            MetadataRecipeBody.model_validate(_body(kind="ui-recipe"))

    def test_unknown_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetadataRecipeBody.model_validate(_body(mode="metadata_audit"))


# ---------------------------------------------------------------------------
# Body-level validator: DeployStep only valid when mode=metadata_write
# ---------------------------------------------------------------------------

class TestMetadataRecipeDeployModeValidator:
    def test_deploy_in_write_mode_ok(self) -> None:
        body = MetadataRecipeBody.model_validate(_body(
            mode="metadata_write",
            steps=[{
                "kind": "deploy", "step_id": "s1",
                "deployment_payload": {"x": 1},
            }],
        ))
        assert isinstance(body.steps[0], DeployStep)

    def test_deploy_in_read_mode_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            MetadataRecipeBody.model_validate(_body(
                mode="metadata_read",
                steps=[{
                    "kind": "deploy", "step_id": "s1",
                    "deployment_payload": {"x": 1},
                }],
            ))
        msg = str(exc_info.value)
        assert "DeployStep" in msg
        assert "metadata_read" in msg

    def test_mixed_steps_with_deploy_in_read_mode_rejected(self) -> None:
        """Even when most steps are valid for read mode, a single
        DeployStep among them must surface the error."""
        with pytest.raises(ValidationError):
            MetadataRecipeBody.model_validate(_body(
                mode="metadata_read",
                steps=[
                    {"kind": "read_metadata", "step_id": "s1",
                     "target_entity": _logical("CustomObject", "Account")},
                    {"kind": "deploy", "step_id": "s2",
                     "deployment_payload": {}},
                ],
            ))

    def test_read_mode_with_only_read_steps_ok(self) -> None:
        body = MetadataRecipeBody.model_validate(_body(
            mode="metadata_read",
            steps=[
                {"kind": "read_metadata", "step_id": "s1",
                 "target_entity": _logical("CustomObject", "Account")},
                {"kind": "assert", "step_id": "s2",
                 "predicate": {"subject_ref": "s1",
                                "predicate": "exists"}},
            ],
        ))
        assert len(body.steps) == 2


# ---------------------------------------------------------------------------
# OperationalRef behaviour
# ---------------------------------------------------------------------------

class TestMetadataRecipeOperationalRef:
    def test_pinned_target_entity_lands_as_pinned_ref(self) -> None:
        body = MetadataRecipeBody.model_validate(_body(
            mode="metadata_read",
            steps=[{
                "kind": "read_metadata", "step_id": "s1",
                "target_entity": _pinned("CustomObject", "Account"),
            }],
        ))
        step = body.steps[0]
        assert isinstance(step, ReadMetadataStep)
        assert type(step.target_entity) is PinnedRef
        assert not isinstance(step.target_entity, IdentityBearingRef)

    def test_logical_target_entity_lands_as_logical_ref(self) -> None:
        body = MetadataRecipeBody.model_validate(_body(
            mode="metadata_read",
            steps=[{
                "kind": "read_metadata", "step_id": "s1",
                "target_entity": _logical("CustomObject", "Account"),
            }],
        ))
        step = body.steps[0]
        assert isinstance(step, ReadMetadataStep)
        assert type(step.target_entity) is LogicalRef


# ---------------------------------------------------------------------------
# ORDERED marker
# ---------------------------------------------------------------------------

class TestMetadataRecipeOrderedSteps:
    def test_steps_field_marked_ordered(self) -> None:
        metadata = MetadataRecipeBody.model_fields["steps"].metadata
        assert ArraySemantics.ORDERED in metadata


# ---------------------------------------------------------------------------
# Round-trip + registry
# ---------------------------------------------------------------------------

class TestMetadataRecipeSerialization:
    def test_full_round_trip(self) -> None:
        body = MetadataRecipeBody.model_validate(_body(
            mode="metadata_write",
            api_choice="tooling_api",
            steps=[
                {"kind": "deploy", "step_id": "s1",
                 "deployment_payload": {"CustomField": {}}},
                {"kind": "retrieve", "step_id": "s2",
                 "target_entity": _logical("CustomField", "Foo__c"),
                 "into_field": "result"},
                {"kind": "assert", "step_id": "s3",
                 "predicate": {"subject_ref": "s2.result",
                                "predicate": "exists"}},
            ],
        ))
        roundtrip = MetadataRecipeBody.model_validate(body.model_dump())
        assert roundtrip == body
        kinds = [s.kind for s in roundtrip.steps]
        assert kinds == ["deploy", "retrieve", "assert"]

    def test_universal_body_keys_present(self) -> None:
        body = MetadataRecipeBody.model_validate(_body())
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "metadata-recipe"


class TestMetadataRecipeRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("metadata-recipe", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model("metadata-recipe", 1) is MetadataRecipeBody

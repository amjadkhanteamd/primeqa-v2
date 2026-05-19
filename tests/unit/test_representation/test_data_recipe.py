"""Tests for :class:`DataRecipeBody` + its step variants.

Per SPEC §3 + D-054 + D-059 §6.3.4 (ordered steps). Covers:

  - All 6 step variants construct + dispatch via the union.
  - Cross-field run_as_user coupling (bidirectional).
  - api_choice / execution_mechanism / identity_context closed
    taxonomies enforced.
  - OperationalRef behaviour on step + body fields.
  - Mixed-step recipe round-trips with step kinds preserved.
  - Registry registration.
  - ``steps`` field carries :class:`ArraySemantics.ORDERED` in
    its Pydantic metadata.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.common import ArraySemantics
from primeqa.test_representation.models.primitives import AssertionPredicate
from primeqa.test_representation.models.recipes.data_recipe import (
    ApexStep,
    AssertStep,
    CreateStep,
    DataRecipeBody,
    DataRecipeStep,
    DeleteStep,
    ReadStep,
    UpdateStep,
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


# ---------------------------------------------------------------------------
# Step variants — construction + union dispatch
# ---------------------------------------------------------------------------

class TestDataRecipeStepVariants:
    @pytest.fixture
    def adapter(self) -> TypeAdapter:
        return TypeAdapter(DataRecipeStep)

    def test_create_step(self) -> None:
        s = CreateStep.model_validate({
            "step_id": "s1",
            "target_object": _logical("Object", "Account"),
            "field_values": {"Name": "Acme"},
        })
        assert s.kind == "create"

    def test_read_step_with_soql(self) -> None:
        s = ReadStep.model_validate({
            "step_id": "s2",
            "target": _logical("Object", "Account"),
            "soql": "SELECT Id, Name FROM Account WHERE Id=:s1.Id",
            "fields_to_capture": ["Id", "Name"],
        })
        assert s.soql is not None

    def test_read_step_without_soql(self) -> None:
        s = ReadStep.model_validate({
            "step_id": "s2",
            "target": _logical("Object", "Account"),
        })
        assert s.soql is None
        assert s.fields_to_capture == []

    def test_update_step(self) -> None:
        s = UpdateStep.model_validate({
            "step_id": "s3",
            "target": _logical("Object", "Account"),
            "field_changes": {"Phone": "555-1212"},
        })
        assert s.field_changes["Phone"] == "555-1212"

    def test_delete_step(self) -> None:
        s = DeleteStep.model_validate({
            "step_id": "s4",
            "target": _logical("Object", "Account"),
        })
        assert s.kind == "delete"

    def test_assert_step(self) -> None:
        s = AssertStep.model_validate({
            "step_id": "s5",
            "predicate": {
                "subject_ref": "s1.Id",
                "predicate": "exists",
            },
        })
        assert isinstance(s.predicate, AssertionPredicate)

    def test_apex_step(self) -> None:
        s = ApexStep.model_validate({
            "step_id": "s6",
            "body": "System.debug('hi');",
            "captured_outputs": ["debug:hi"],
        })
        assert s.body.startswith("System.debug")

    @pytest.mark.parametrize("kind,payload", [
        ("create", {"target_object": {"ref_kind": "logical",
                                       "entity_type": "Object",
                                       "external_id": "A"},
                    "field_values": {}}),
        ("delete", {"target": {"ref_kind": "logical",
                                "entity_type": "Object",
                                "external_id": "A"}}),
        ("assert", {"predicate": {"subject_ref": "x",
                                    "predicate": "exists"}}),
        ("apex", {"body": "x;"}),
    ])
    def test_union_dispatches(
        self, adapter: TypeAdapter, kind: str, payload: dict,
    ) -> None:
        full = dict(payload)
        full["kind"] = kind
        full["step_id"] = "x"
        result = adapter.validate_python(full)
        assert result.kind == kind

    def test_union_rejects_unknown_kind(
        self, adapter: TypeAdapter,
    ) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "kind": "merge",
                "step_id": "x",
            })


# ---------------------------------------------------------------------------
# Body construction
# ---------------------------------------------------------------------------

def _body(**overrides) -> dict:
    base = {
        "api_choice": "rest",
        "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": [],
    }
    base.update(overrides)
    return base


class TestDataRecipeBodyConstruction:
    def test_minimal(self) -> None:
        body = DataRecipeBody.model_validate(_body())
        assert body.kind == "data-recipe"
        assert body.body_schema_version == 1

    @pytest.mark.parametrize("api_choice", ["rest", "bulk", "composite"])
    def test_all_api_choices_accepted(self, api_choice: str) -> None:
        body = DataRecipeBody.model_validate(_body(api_choice=api_choice))
        assert body.api_choice == api_choice

    @pytest.mark.parametrize("mechanism", ["direct_api", "anonymous_apex"])
    def test_all_execution_mechanisms_accepted(
        self, mechanism: str,
    ) -> None:
        body = DataRecipeBody.model_validate(
            _body(execution_mechanism=mechanism),
        )
        assert body.execution_mechanism == mechanism

    def test_unknown_api_choice_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DataRecipeBody.model_validate(_body(api_choice="metadata"))

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            DataRecipeBody.model_validate(_body(kind="ui-recipe"))


class TestDataRecipeIdentityContextCoupling:
    def test_run_as_user_with_user_set_ok(self) -> None:
        body = DataRecipeBody.model_validate(_body(
            identity_context="run_as_user",
            run_as_user=_logical("User", "rep@example.com"),
        ))
        assert isinstance(body.run_as_user, LogicalRef)

    def test_run_as_user_without_user_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DataRecipeBody.model_validate(_body(
                identity_context="run_as_user",
            ))
        assert "run_as_user" in str(exc_info.value)

    def test_system_with_user_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DataRecipeBody.model_validate(_body(
                identity_context="system",
                run_as_user=_logical("User", "x"),
            ))
        assert "forbids" in str(exc_info.value).lower()


class TestDataRecipeOperationalRef:
    def test_pinned_target_in_step_lands_as_pinned_ref(self) -> None:
        body = DataRecipeBody.model_validate(_body(
            steps=[{
                "kind": "read",
                "step_id": "s1",
                "target": _pinned("Object", "Account"),
            }],
        ))
        step = body.steps[0]
        assert isinstance(step, ReadStep)
        assert type(step.target) is PinnedRef
        assert not isinstance(step.target, IdentityBearingRef)

    def test_logical_target_in_step_lands_as_logical_ref(self) -> None:
        body = DataRecipeBody.model_validate(_body(
            steps=[{
                "kind": "read",
                "step_id": "s1",
                "target": _logical("Object", "Account"),
            }],
        ))
        step = body.steps[0]
        assert isinstance(step, ReadStep)
        assert type(step.target) is LogicalRef


# ---------------------------------------------------------------------------
# Mixed-step recipe round-trip + ORDERED marker
# ---------------------------------------------------------------------------

class TestDataRecipeMixedRoundTrip:
    def test_mixed_steps_preserve_types(self) -> None:
        body = DataRecipeBody.model_validate(_body(
            steps=[
                {"kind": "create", "step_id": "s1",
                 "target_object": _logical("Object", "Account"),
                 "field_values": {"Name": "Acme"}},
                {"kind": "read", "step_id": "s2",
                 "target": _logical("Object", "Account"),
                 "soql": "SELECT Id FROM Account WHERE Id=:s1.Id",
                 "fields_to_capture": ["Id"]},
                {"kind": "assert", "step_id": "s3",
                 "predicate": {"subject_ref": "s2.Id",
                                "predicate": "exists"}},
            ],
        ))
        roundtrip = DataRecipeBody.model_validate(body.model_dump())
        assert roundtrip == body
        kinds = [s.kind for s in roundtrip.steps]
        assert kinds == ["create", "read", "assert"]
        assert isinstance(roundtrip.steps[0], CreateStep)
        assert isinstance(roundtrip.steps[1], ReadStep)
        assert isinstance(roundtrip.steps[2], AssertStep)

    def test_universal_body_keys_present(self) -> None:
        body = DataRecipeBody.model_validate(_body())
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "data-recipe"


class TestDataRecipeOrderedStepsMarker:
    def test_steps_field_marked_ordered(self) -> None:
        """Per D-059 §6.3.4 + SPEC §6.3.4: recipe step lists are
        ORDERED for canonicalization. Marker must be discoverable
        via Pydantic's field metadata so the canonicalization
        layer (a later track) can introspect it."""
        metadata = DataRecipeBody.model_fields["steps"].metadata
        assert ArraySemantics.ORDERED in metadata


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestDataRecipeRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("data-recipe", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model("data-recipe", 1) is DataRecipeBody

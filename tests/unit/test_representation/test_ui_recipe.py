"""Tests for :class:`UIRecipeBody`.

Per SPEC §3 + D-054. Covers:

  - All 7 step variants accept their data + dispatch via the union.
  - framework + identity_context closed taxonomies.
  - Bidirectional run_as_user coupling.
  - ORDERED marker on steps.
  - Registry registration.
  - Mixed-step recipe round-trip preserves step types.
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.common import ArraySemantics
from primeqa.test_representation.models.recipes.ui_recipe import (
    AssertStep,
    CaptureStep,
    ClickStep,
    NavigateStep,
    SelectStep,
    TypeStep,
    UIRecipeBody,
    UIRecipeStep,
    WaitStep,
)
from primeqa.test_representation.models.references import LogicalRef
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)


def _logical_user() -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": "User",
        "external_id": "sales.rep@example.com",
    }


def _body(**overrides) -> dict:
    base = {
        "framework": "playwright",
        "identity_context": "system",
        "steps": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Step variants
# ---------------------------------------------------------------------------

class TestUIStepVariants:
    @pytest.fixture
    def adapter(self) -> TypeAdapter:
        return TypeAdapter(UIRecipeStep)

    def test_navigate(self) -> None:
        s = NavigateStep(step_id="s1", url_or_route="/lightning/o/Account")
        assert s.kind == "navigate"

    def test_click(self) -> None:
        s = ClickStep(step_id="s2", element_selector="button.save")
        assert s.kind == "click"

    def test_type(self) -> None:
        s = TypeStep(step_id="s3", element_selector="input.name", text="Acme")
        assert s.text == "Acme"

    def test_select(self) -> None:
        s = SelectStep(
            step_id="s4",
            element_selector="select.industry",
            option_value="Technology",
        )
        assert s.option_value == "Technology"

    def test_wait(self) -> None:
        s = WaitStep(
            step_id="s5",
            condition="toast.success visible",
            timeout_seconds=10,
        )
        assert s.timeout_seconds == 10

    @pytest.mark.parametrize("capture_kind", [
        "text", "value", "screenshot", "dom",
    ])
    def test_capture_all_kinds(self, capture_kind: str) -> None:
        s = CaptureStep(
            step_id="s6",
            element_selector="div.toast",
            capture_kind=capture_kind,  # type: ignore[arg-type]
        )
        assert s.capture_kind == capture_kind

    def test_assert(self) -> None:
        s = AssertStep.model_validate({
            "step_id": "s7",
            "predicate": {
                "subject_ref": "s6", "predicate": "exists",
            },
        })
        assert s.kind == "assert"

    @pytest.mark.parametrize("kind,extra", [
        ("navigate", {"url_or_route": "/x"}),
        ("click", {"element_selector": "x"}),
        ("type", {"element_selector": "x", "text": "y"}),
        ("select", {"element_selector": "x", "option_value": "y"}),
        ("wait", {"condition": "x", "timeout_seconds": 1}),
        ("capture", {"element_selector": "x", "capture_kind": "text"}),
        ("assert", {"predicate": {"subject_ref": "x",
                                    "predicate": "exists"}}),
    ])
    def test_union_dispatches(
        self, adapter: TypeAdapter, kind: str, extra: dict,
    ) -> None:
        result = adapter.validate_python({
            "kind": kind, "step_id": "x", **extra,
        })
        assert result.kind == kind

    def test_unknown_kind_rejected(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "kind": "drag", "step_id": "x",
                "from_selector": "a", "to_selector": "b",
            })


# ---------------------------------------------------------------------------
# Body construction
# ---------------------------------------------------------------------------

class TestUIRecipeBodyConstruction:
    @pytest.mark.parametrize("framework", [
        "playwright", "selenium", "lightning_test_service",
    ])
    def test_all_frameworks_accepted(self, framework: str) -> None:
        body = UIRecipeBody.model_validate(_body(framework=framework))
        assert body.framework == framework

    def test_unknown_framework_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UIRecipeBody.model_validate(_body(framework="cypress"))

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            UIRecipeBody.model_validate(_body(kind="data-recipe"))


class TestUIRecipeIdentityCoupling:
    def test_run_as_user_with_user_ok(self) -> None:
        body = UIRecipeBody.model_validate(_body(
            identity_context="run_as_user",
            run_as_user=_logical_user(),
        ))
        assert isinstance(body.run_as_user, LogicalRef)

    def test_run_as_user_without_user_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UIRecipeBody.model_validate(_body(
                identity_context="run_as_user",
            ))

    def test_system_with_user_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UIRecipeBody.model_validate(_body(
                identity_context="system",
                run_as_user=_logical_user(),
            ))


# ---------------------------------------------------------------------------
# ORDERED marker + round-trip
# ---------------------------------------------------------------------------

class TestUIRecipeOrderedSteps:
    def test_steps_field_marked_ordered(self) -> None:
        metadata = UIRecipeBody.model_fields["steps"].metadata
        assert ArraySemantics.ORDERED in metadata


class TestUIRecipeMixedRoundTrip:
    def test_full_recipe_round_trips(self) -> None:
        body = UIRecipeBody.model_validate(_body(
            framework="playwright",
            steps=[
                {"kind": "navigate", "step_id": "s1",
                 "url_or_route": "/lightning/o/Account/new"},
                {"kind": "type", "step_id": "s2",
                 "element_selector": "input[name='Name']",
                 "text": "Acme"},
                {"kind": "click", "step_id": "s3",
                 "element_selector": "button[name='SaveEdit']"},
                {"kind": "wait", "step_id": "s4",
                 "condition": "toast.success visible",
                 "timeout_seconds": 10},
                {"kind": "capture", "step_id": "s5",
                 "element_selector": "h1.recordTitle",
                 "capture_kind": "text"},
                {"kind": "assert", "step_id": "s6",
                 "predicate": {"subject_ref": "s5",
                                "predicate": "equals",
                                "value": "Acme"}},
            ],
        ))
        roundtrip = UIRecipeBody.model_validate(body.model_dump())
        assert roundtrip == body
        kinds = [s.kind for s in roundtrip.steps]
        assert kinds == [
            "navigate", "type", "click", "wait", "capture", "assert",
        ]

    def test_universal_body_keys_present(self) -> None:
        body = UIRecipeBody.model_validate(_body())
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "ui-recipe"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestUIRecipeRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("ui-recipe", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model("ui-recipe", 1) is UIRecipeBody

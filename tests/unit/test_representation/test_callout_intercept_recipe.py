"""Tests for :class:`CalloutInterceptRecipeBody`.

Per SPEC §3 + D-054. Covers:

  - 4 step variants + union dispatch.
  - interception_mechanism closed taxonomy.
  - MockResponse construction (str + dict body shapes).
  - mock_response coupling: required for ``mock_endpoint``,
    optional for ``proxy``.
  - OperationalRef behaviour on target_named_credential.
  - ORDERED marker.
  - Registry registration.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.common import ArraySemantics
from primeqa.test_representation.models.recipes.callout_intercept import (
    ActivateInterceptStep,
    AssertStep,
    CalloutInterceptRecipeBody,
    CalloutInterceptStep,
    DeactivateInterceptStep,
    MockResponse,
    WaitForCalloutStep,
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


def _logical_named_cred() -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": "NamedCredential",
        "external_id": "Stripe_Api",
    }


def _pinned_named_cred() -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": "NamedCredential",
        "entity_id": str(uuid4()),
        "version_seq": 1,
        "external_id": "Stripe_Api",
    }


def _mock_ok(body: object = None) -> dict:
    return {
        "status_code": 200,
        "headers": {"Content-Type": "application/json"},
        "body": body if body is not None else {"status": "ok"},
    }


def _body(**overrides) -> dict:
    base = {
        "interception_mechanism": "mock_endpoint",
        "mock_response": _mock_ok(),
        "steps": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# MockResponse
# ---------------------------------------------------------------------------

class TestMockResponse:
    def test_dict_body(self) -> None:
        m = MockResponse.model_validate(_mock_ok({"x": 1}))
        assert isinstance(m.body, dict)

    def test_string_body(self) -> None:
        m = MockResponse.model_validate({
            "status_code": 200, "body": "raw text",
        })
        assert m.body == "raw text"

    def test_headers_optional(self) -> None:
        m = MockResponse.model_validate({
            "status_code": 204, "body": "",
        })
        assert m.headers is None

    def test_status_code_required(self) -> None:
        with pytest.raises(ValidationError):
            MockResponse.model_validate({"body": ""})

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            MockResponse.model_validate({
                "status_code": 200, "body": "", "surprise": True,
            })

    def test_round_trip(self) -> None:
        m = MockResponse.model_validate(_mock_ok())
        roundtrip = MockResponse.model_validate(m.model_dump())
        assert roundtrip == m

    def test_frozen(self) -> None:
        m = MockResponse.model_validate({"status_code": 200, "body": ""})
        with pytest.raises(ValidationError):
            m.status_code = 500  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Step variants
# ---------------------------------------------------------------------------

class TestCalloutInterceptSteps:
    @pytest.fixture
    def adapter(self) -> TypeAdapter:
        return TypeAdapter(CalloutInterceptStep)

    def test_activate(self) -> None:
        s = ActivateInterceptStep(step_id="s1", intercept_id="ix_a")
        assert s.kind == "activate_intercept"

    def test_wait_for_callout(self) -> None:
        s = WaitForCalloutStep(
            step_id="s2", timeout_seconds=15, into_capture="req",
        )
        assert s.into_capture == "req"

    def test_assert(self) -> None:
        s = AssertStep.model_validate({
            "step_id": "s3",
            "predicate": {"subject_ref": "req.method",
                           "predicate": "equals",
                           "value": "POST"},
        })
        assert s.kind == "assert"

    def test_deactivate(self) -> None:
        s = DeactivateInterceptStep(step_id="s4", intercept_id="ix_a")
        assert s.kind == "deactivate_intercept"

    @pytest.mark.parametrize("kind,extra", [
        ("activate_intercept", {"intercept_id": "x"}),
        ("wait_for_callout", {"timeout_seconds": 5, "into_capture": "x"}),
        ("assert", {"predicate": {"subject_ref": "x",
                                    "predicate": "exists"}}),
        ("deactivate_intercept", {"intercept_id": "x"}),
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
            adapter.validate_python({"kind": "block", "step_id": "x"})


# ---------------------------------------------------------------------------
# Body construction + coupling validator
# ---------------------------------------------------------------------------

class TestCalloutInterceptBody:
    def test_mock_endpoint_with_mock_response_ok(self) -> None:
        body = CalloutInterceptRecipeBody.model_validate(_body())
        assert body.interception_mechanism == "mock_endpoint"

    def test_mock_endpoint_without_mock_response_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CalloutInterceptRecipeBody.model_validate(_body(
                mock_response=None,
            ))
        assert "mock_response" in str(exc_info.value)

    def test_proxy_without_mock_response_ok(self) -> None:
        body = CalloutInterceptRecipeBody.model_validate(_body(
            interception_mechanism="proxy",
            mock_response=None,
        ))
        assert body.mock_response is None

    def test_proxy_with_mock_response_ok(self) -> None:
        """Proxy-with-fallback patterns occasionally provide a
        fallback mock_response. The substrate doesn't forbid it."""
        body = CalloutInterceptRecipeBody.model_validate(_body(
            interception_mechanism="proxy",
        ))
        assert body.mock_response is not None

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            CalloutInterceptRecipeBody.model_validate(
                _body(kind="ui-recipe"),
            )

    def test_unknown_mechanism_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CalloutInterceptRecipeBody.model_validate(_body(
                interception_mechanism="vcr",
            ))


class TestCalloutInterceptOperationalRef:
    def test_pinned_named_credential_lands_as_pinned_ref(self) -> None:
        body = CalloutInterceptRecipeBody.model_validate(_body(
            target_named_credential=_pinned_named_cred(),
        ))
        assert type(body.target_named_credential) is PinnedRef
        assert not isinstance(
            body.target_named_credential, IdentityBearingRef,
        )

    def test_logical_named_credential_lands_as_logical_ref(self) -> None:
        body = CalloutInterceptRecipeBody.model_validate(_body(
            target_named_credential=_logical_named_cred(),
        ))
        assert type(body.target_named_credential) is LogicalRef


class TestCalloutInterceptOrderedSteps:
    def test_steps_field_marked_ordered(self) -> None:
        metadata = CalloutInterceptRecipeBody.model_fields[
            "steps"
        ].metadata
        assert ArraySemantics.ORDERED in metadata


class TestCalloutInterceptRoundTrip:
    def test_full_recipe_round_trips(self) -> None:
        body = CalloutInterceptRecipeBody.model_validate(_body(
            target_named_credential=_logical_named_cred(),
            target_endpoint_pattern="https://api.stripe.com/.*",
            steps=[
                {"kind": "activate_intercept", "step_id": "s1",
                 "intercept_id": "stripe"},
                {"kind": "wait_for_callout", "step_id": "s2",
                 "timeout_seconds": 30, "into_capture": "req"},
                {"kind": "assert", "step_id": "s3",
                 "predicate": {"subject_ref": "req.method",
                                "predicate": "equals", "value": "POST"}},
                {"kind": "deactivate_intercept", "step_id": "s4",
                 "intercept_id": "stripe"},
            ],
        ))
        roundtrip = CalloutInterceptRecipeBody.model_validate(
            body.model_dump(),
        )
        assert roundtrip == body
        kinds = [s.kind for s in roundtrip.steps]
        assert kinds == [
            "activate_intercept", "wait_for_callout",
            "assert", "deactivate_intercept",
        ]


class TestCalloutInterceptRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("callout-intercept-recipe", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model(
            "callout-intercept-recipe", 1,
        ) is CalloutInterceptRecipeBody

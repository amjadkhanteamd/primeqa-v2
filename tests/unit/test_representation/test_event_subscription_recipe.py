"""Tests for :class:`EventSubscriptionRecipeBody`.

Per SPEC §3 + D-054. Covers:

  - 4 step variants + union dispatch.
  - channel_type + subscription_mode closed taxonomies.
  - OperationalRef behaviour on channel_target.
  - subscription_filter optional.
  - ORDERED marker.
  - Registry registration.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.common import ArraySemantics
from primeqa.test_representation.models.recipes.event_subscription import (
    AssertStep,
    EventSubscriptionRecipeBody,
    EventSubscriptionStep,
    SubscribeStep,
    UnsubscribeStep,
    WaitForEventStep,
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


def _logical_channel() -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": "PlatformEvent",
        "external_id": "AccountUpdated__e",
    }


def _pinned_channel() -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": "PlatformEvent",
        "entity_id": str(uuid4()),
        "version_seq": 2,
        "external_id": "AccountUpdated__e",
    }


def _body(**overrides) -> dict:
    base = {
        "channel_type": "platform_event",
        "subscription_mode": "ephemeral",
        "channel_target": _logical_channel(),
        "steps": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Step variants
# ---------------------------------------------------------------------------

class TestEventSubscriptionSteps:
    @pytest.fixture
    def adapter(self) -> TypeAdapter:
        return TypeAdapter(EventSubscriptionStep)

    def test_subscribe(self) -> None:
        s = SubscribeStep(step_id="s1", subscription_id="sub_a")
        assert s.kind == "subscribe"

    def test_wait_for_event(self) -> None:
        s = WaitForEventStep(
            step_id="s2", timeout_seconds=30, into_capture="evt",
        )
        assert s.into_capture == "evt"

    def test_assert(self) -> None:
        s = AssertStep.model_validate({
            "step_id": "s3",
            "predicate": {"subject_ref": "evt", "predicate": "exists"},
        })
        assert s.kind == "assert"

    def test_unsubscribe(self) -> None:
        s = UnsubscribeStep(step_id="s4", subscription_id="sub_a")
        assert s.kind == "unsubscribe"

    @pytest.mark.parametrize("kind,extra", [
        ("subscribe", {"subscription_id": "x"}),
        ("wait_for_event", {"timeout_seconds": 1, "into_capture": "x"}),
        ("assert", {"predicate": {"subject_ref": "x",
                                    "predicate": "exists"}}),
        ("unsubscribe", {"subscription_id": "x"}),
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
            adapter.validate_python({"kind": "poll", "step_id": "x"})


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------

class TestEventSubscriptionBody:
    @pytest.mark.parametrize("channel_type", [
        "platform_event", "outbound_message", "cdc",
    ])
    def test_all_channel_types(self, channel_type: str) -> None:
        body = EventSubscriptionRecipeBody.model_validate(
            _body(channel_type=channel_type),
        )
        assert body.channel_type == channel_type

    @pytest.mark.parametrize("mode", ["durable", "ephemeral"])
    def test_both_subscription_modes(self, mode: str) -> None:
        body = EventSubscriptionRecipeBody.model_validate(
            _body(subscription_mode=mode),
        )
        assert body.subscription_mode == mode

    def test_subscription_filter_optional(self) -> None:
        body = EventSubscriptionRecipeBody.model_validate(_body())
        assert body.subscription_filter is None

    def test_subscription_filter_round_trip(self) -> None:
        body = EventSubscriptionRecipeBody.model_validate(_body(
            subscription_filter="Account.Industry = 'Tech'",
        ))
        roundtrip = EventSubscriptionRecipeBody.model_validate(
            body.model_dump(),
        )
        assert roundtrip == body

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            EventSubscriptionRecipeBody.model_validate(
                _body(kind="data-recipe"),
            )

    def test_unknown_channel_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EventSubscriptionRecipeBody.model_validate(
                _body(channel_type="webhook"),
            )


class TestEventSubscriptionOperationalRef:
    def test_pinned_channel_target_lands_as_pinned_ref(self) -> None:
        body = EventSubscriptionRecipeBody.model_validate(
            _body(channel_target=_pinned_channel()),
        )
        assert type(body.channel_target) is PinnedRef
        assert not isinstance(body.channel_target, IdentityBearingRef)

    def test_logical_channel_target_lands_as_logical_ref(self) -> None:
        body = EventSubscriptionRecipeBody.model_validate(_body())
        assert type(body.channel_target) is LogicalRef


class TestEventSubscriptionOrderedSteps:
    def test_steps_field_marked_ordered(self) -> None:
        metadata = EventSubscriptionRecipeBody.model_fields[
            "steps"
        ].metadata
        assert ArraySemantics.ORDERED in metadata


class TestEventSubscriptionRoundTrip:
    def test_full_recipe_round_trips(self) -> None:
        body = EventSubscriptionRecipeBody.model_validate(_body(
            channel_type="cdc",
            subscription_mode="durable",
            steps=[
                {"kind": "subscribe", "step_id": "s1",
                 "subscription_id": "cdc_account"},
                {"kind": "wait_for_event", "step_id": "s2",
                 "timeout_seconds": 30, "into_capture": "evt"},
                {"kind": "assert", "step_id": "s3",
                 "predicate": {"subject_ref": "evt.recordIds",
                                "predicate": "exists"}},
                {"kind": "unsubscribe", "step_id": "s4",
                 "subscription_id": "cdc_account"},
            ],
        ))
        roundtrip = EventSubscriptionRecipeBody.model_validate(
            body.model_dump(),
        )
        assert roundtrip == body
        kinds = [s.kind for s in roundtrip.steps]
        assert kinds == [
            "subscribe", "wait_for_event", "assert", "unsubscribe",
        ]


class TestEventSubscriptionRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("event-subscription-recipe", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model(
            "event-subscription-recipe", 1,
        ) is EventSubscriptionRecipeBody

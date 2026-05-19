"""event-subscription-recipe body (observation-realization layer).

Per SPEC §3 + D-054. An event-subscription-recipe observes events
that Salesforce publishes — platform events, outbound messages,
Change Data Capture streams. The recipe subscribes, waits for the
expected event, asserts on its payload, and unsubscribes.

Per D-054: this recipe-kind is *semantic-vocabulary* distinct
from callout-intercept-recipe — both observe out-of-band signals,
but event-subscription observes events Salesforce *publishes*
while callout-intercept stands in for the receiver of an outbound
callout.

Operational-layer; refs use :data:`OperationalRef`. Steps are
ORDERED.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from primeqa.test_representation.models.common import (
    ArraySemantics,
    BodyBase,
)
from primeqa.test_representation.models.primitives import AssertionPredicate
from primeqa.test_representation.models.references import OperationalRef
from primeqa.test_representation.models.registry import register_body


# ---------------------------------------------------------------------------
# Step variants
# ---------------------------------------------------------------------------

class _StepBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: str


class SubscribeStep(_StepBase):
    """Open a subscription. ``subscription_id`` is a recipe-local
    handle (free-form string) that later wait/unsubscribe steps
    reference."""

    kind: Literal["subscribe"] = "subscribe"
    subscription_id: str


class WaitForEventStep(_StepBase):
    """Block until an event arrives on the subscription (or the
    timeout) and bind the payload into ``into_capture``."""

    kind: Literal["wait_for_event"] = "wait_for_event"
    timeout_seconds: int
    into_capture: str


class AssertStep(_StepBase):
    """Assert a predicate over a captured event payload."""

    kind: Literal["assert"] = "assert"
    predicate: AssertionPredicate


class UnsubscribeStep(_StepBase):
    """Close the subscription opened by an earlier
    :class:`SubscribeStep`."""

    kind: Literal["unsubscribe"] = "unsubscribe"
    subscription_id: str


EventSubscriptionStep = Annotated[
    Union[SubscribeStep, WaitForEventStep, AssertStep, UnsubscribeStep],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# EventSubscriptionRecipeBody
# ---------------------------------------------------------------------------

@register_body("event-subscription-recipe", 1)
class EventSubscriptionRecipeBody(BodyBase):
    """The event-subscription-recipe body shape (v1).

    Sub-discriminators per D-054:
      - ``channel_type``: which event surface (platform_event /
        outbound_message / cdc).
      - ``subscription_mode``: ``durable`` resumes after recipe
        restarts; ``ephemeral`` does not.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["event-subscription-recipe"] = "event-subscription-recipe"

    channel_type: Literal[
        "platform_event",
        "outbound_message",
        "cdc",
    ]

    subscription_mode: Literal["durable", "ephemeral"]

    channel_target: OperationalRef
    """The S1 entity representing the event channel (the platform
    event type, the outbound message definition, the CDC channel
    object). Operational-layer ref."""

    subscription_filter: Optional[str] = None
    """Free-form filter expression (e.g., ``"Account.Industry =
    'Tech'"``). Diagnostic at this layer; the recipe runner parses
    it."""

    steps: Annotated[list[EventSubscriptionStep], ArraySemantics.ORDERED]

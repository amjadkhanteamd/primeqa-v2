"""callout-intercept-recipe body (observation-realization layer).

Per SPEC §3 + D-054. A callout-intercept-recipe stands in for the
target of an outbound HTTP callout — substituting a mocked endpoint
or a proxy that records the request and returns a controlled
response. The recipe asserts on the inbound request from
Salesforce.

Per D-054: distinct from event-subscription-recipe via a
semantic-vocabulary split — event-subscription observes events
that Salesforce *publishes*; callout-intercept stands in as the
receiver of an outbound callout that Salesforce *makes*.

Operational-layer; refs use :data:`OperationalRef`. Steps are
ORDERED. The mock-response coupling validator enforces that
``mock_response`` is present when ``interception_mechanism ==
"mock_endpoint"`` (the mock must have something to return).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from primeqa.test_representation.models.common import (
    ArraySemantics,
    BodyBase,
)
from primeqa.test_representation.models.primitives import AssertionPredicate
from primeqa.test_representation.models.references import OperationalRef
from primeqa.test_representation.models.registry import register_body


# ---------------------------------------------------------------------------
# MockResponse
# ---------------------------------------------------------------------------

class MockResponse(BaseModel):
    """The canned response a mock endpoint returns to Salesforce.

    ``body`` is ``Union[str, dict[str, Any]]`` for the same reason
    :class:`PayloadDescriptor.content` is: JSON / form-encoded
    responses are naturally dicts, blob / text responses are
    strings. The recipe runner serialises whichever shape on the
    wire.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status_code: int
    headers: Optional[dict[str, str]] = None
    body: Union[str, dict[str, Any]]


# ---------------------------------------------------------------------------
# Step variants
# ---------------------------------------------------------------------------

class _StepBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: str


class ActivateInterceptStep(_StepBase):
    """Begin intercepting callouts. ``intercept_id`` is a
    recipe-local handle (free-form string) referenced by later
    wait/deactivate steps."""

    kind: Literal["activate_intercept"] = "activate_intercept"
    intercept_id: str


class WaitForCalloutStep(_StepBase):
    """Block until a callout arrives at the intercept (or
    timeout) and bind the captured request into ``into_capture``."""

    kind: Literal["wait_for_callout"] = "wait_for_callout"
    timeout_seconds: int
    into_capture: str


class AssertStep(_StepBase):
    """Assert a predicate over a captured callout request."""

    kind: Literal["assert"] = "assert"
    predicate: AssertionPredicate


class DeactivateInterceptStep(_StepBase):
    """End the intercept opened by an earlier
    :class:`ActivateInterceptStep`."""

    kind: Literal["deactivate_intercept"] = "deactivate_intercept"
    intercept_id: str


CalloutInterceptStep = Annotated[
    Union[
        ActivateInterceptStep, WaitForCalloutStep,
        AssertStep, DeactivateInterceptStep,
    ],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# CalloutInterceptRecipeBody
# ---------------------------------------------------------------------------

@register_body("callout-intercept-recipe", 1)
class CalloutInterceptRecipeBody(BodyBase):
    """The callout-intercept-recipe body shape (v1).

    Cross-field invariant: ``mock_response`` is required when
    ``interception_mechanism == "mock_endpoint"`` (the mock must
    have a response to send). ``proxy`` interception passes
    through to the real endpoint — no mock response needed; if a
    mock_response is provided with ``proxy``, it's silently
    ignored at execution time, but we don't forbid it because
    proxy-with-fallback patterns occasionally use it.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["callout-intercept-recipe"] = "callout-intercept-recipe"

    interception_mechanism: Literal["mock_endpoint", "proxy"]

    target_named_credential: Optional[OperationalRef] = None
    """If the interception is scoped to a specific named
    credential, the reference. Optional because some intercepts
    catch any matching endpoint regardless of credential."""

    target_endpoint_pattern: Optional[str] = None
    """Free-form pattern (substring / regex) matching the
    endpoint URL. The recipe runner interprets it."""

    mock_response: Optional[MockResponse] = None

    steps: Annotated[list[CalloutInterceptStep], ArraySemantics.ORDERED]

    @model_validator(mode="after")
    def _mock_response_required_for_mock_endpoint(
        self,
    ) -> "CalloutInterceptRecipeBody":
        if (
            self.interception_mechanism == "mock_endpoint"
            and self.mock_response is None
        ):
            raise ValueError(
                "interception_mechanism='mock_endpoint' requires "
                "mock_response to be set"
            )
        return self

"""ui-recipe body (observation-realization layer).

Per SPEC §3 + D-054. A ui-recipe drives the Lightning UI to
observe/assert. Steps form a navigate→click→type→assert sequence
familiar from Playwright/Selenium-style test runners.

Per D-058 §5.1: operational-layer; refs use
:data:`OperationalRef`. Per D-059 §6.3.4: steps are ORDERED — the
sequence ("first navigate, then click Submit") is semantic.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class NavigateStep(_StepBase):
    """Navigate the browser to a URL or named route."""

    kind: Literal["navigate"] = "navigate"
    url_or_route: str


class ClickStep(_StepBase):
    """Click an element identified by a free-form selector."""

    kind: Literal["click"] = "click"
    element_selector: str


class TypeStep(_StepBase):
    """Type ``text`` into an element identified by the selector."""

    kind: Literal["type"] = "type"
    element_selector: str
    text: str


class SelectStep(_StepBase):
    """Choose an option from a select/dropdown element."""

    kind: Literal["select"] = "select"
    element_selector: str
    option_value: str


class WaitStep(_StepBase):
    """Wait until a condition holds (or until the timeout)."""

    kind: Literal["wait"] = "wait"
    condition: str
    timeout_seconds: int


class CaptureStep(_StepBase):
    """Capture an element's text / value / screenshot / DOM."""

    kind: Literal["capture"] = "capture"
    element_selector: str
    capture_kind: Literal["text", "value", "screenshot", "dom"]


class AssertStep(_StepBase):
    """Assert a predicate over a captured value."""

    kind: Literal["assert"] = "assert"
    predicate: AssertionPredicate


UIRecipeStep = Annotated[
    Union[
        NavigateStep, ClickStep, TypeStep, SelectStep,
        WaitStep, CaptureStep, AssertStep,
    ],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# UIRecipeBody
# ---------------------------------------------------------------------------

@register_body("ui-recipe", 1)
class UIRecipeBody(BodyBase):
    """The ui-recipe body shape (v1).

    Cross-field invariant: bidirectional ``run_as_user`` coupling
    (same pattern as :class:`DataRecipeBody` and
    :class:`DataMutationTriggerBody`).
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["ui-recipe"] = "ui-recipe"

    framework: Literal[
        "playwright",
        "selenium",
        "lightning_test_service",
    ]
    """The UI test-driver framework. Drives executor selection."""

    identity_context: Literal["system", "run_as_user"]
    run_as_user: Optional[OperationalRef] = None

    steps: Annotated[list[UIRecipeStep], ArraySemantics.ORDERED]

    @model_validator(mode="after")
    def _identity_context_coupling(self) -> "UIRecipeBody":
        if self.identity_context == "run_as_user":
            if self.run_as_user is None:
                raise ValueError(
                    "identity_context='run_as_user' requires "
                    "run_as_user to be set"
                )
        else:
            if self.run_as_user is not None:
                raise ValueError(
                    "identity_context='system' forbids run_as_user; "
                    "got a non-None reference"
                )
        return self

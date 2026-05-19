"""data-recipe body (observation-realization layer).

Per SPEC §3 + D-054. A data-recipe observes and asserts via the
data APIs — REST/SOAP/Bulk DML on records. Distinct from
metadata-recipe (which operates on the configuration model);
data-recipes drive runtime-data state.

Per D-058 §5.1 (hybrid-by-layer): refs in operational-layer
bodies default to logical (:data:`OperationalRef`). Per D-059
§6.3.4: the steps list carries semantic order, marked with
:class:`ArraySemantics.ORDERED` for the canonicalization step.
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
# Step variants — discriminated by ``kind``
#
# Each variant carries ``step_id: str`` so prior steps can be
# referenced by later assertions / SOQL substitutions. The
# Coordinator validates the cross-step graph (does step_3 actually
# reference a real step_2?) per D-060 §4.7.6; the Pydantic layer
# treats step references as opaque strings.
# ---------------------------------------------------------------------------


class _StepBase(BaseModel):
    """Shared shape for data-recipe steps. Holds the step_id so
    every concrete variant inherits the same on-the-wire shape
    discipline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str


class CreateStep(_StepBase):
    """Insert a new record."""

    kind: Literal["create"] = "create"
    target_object: OperationalRef
    field_values: dict[str, Any]


class ReadStep(_StepBase):
    """Read a record or query a set of records.

    ``soql`` is optional because a read can also be by-id (the
    ``target`` resolves the row and ``fields_to_capture`` names
    the projection). When ``soql`` is set, ``target`` typically
    references the FROM object."""

    kind: Literal["read"] = "read"
    target: OperationalRef
    soql: Optional[str] = None
    fields_to_capture: list[str] = []


class UpdateStep(_StepBase):
    """Update an existing record."""

    kind: Literal["update"] = "update"
    target: OperationalRef
    field_changes: dict[str, Any]


class DeleteStep(_StepBase):
    """Delete a record."""

    kind: Literal["delete"] = "delete"
    target: OperationalRef


class AssertStep(_StepBase):
    """Assert a predicate over a prior step's captured output."""

    kind: Literal["assert"] = "assert"
    predicate: AssertionPredicate


class ApexStep(_StepBase):
    """Run anonymous Apex and capture named outputs.

    ``body`` is the raw Apex source; ``captured_outputs`` names
    the variables / debug-log markers the recipe expects to find
    in the response. The recipe runner is responsible for
    extracting them."""

    kind: Literal["apex"] = "apex"
    body: str
    captured_outputs: list[str] = []


DataRecipeStep = Annotated[
    Union[
        CreateStep, ReadStep, UpdateStep, DeleteStep,
        AssertStep, ApexStep,
    ],
    Field(discriminator="kind"),
]
"""Discriminated union over the six data-recipe step variants."""


# ---------------------------------------------------------------------------
# DataRecipeBody
# ---------------------------------------------------------------------------

@register_body("data-recipe", 1)
class DataRecipeBody(BodyBase):
    """The data-recipe body shape (v1).

    Cross-field invariant: ``run_as_user`` is required iff
    ``identity_context == "run_as_user"``. Same bi-directional
    coupling as :class:`DataMutationTriggerBody`.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["data-recipe"] = "data-recipe"

    api_choice: Literal["rest", "bulk", "composite"]
    """The data-API surface the recipe uses."""

    identity_context: Literal["system", "run_as_user"]
    run_as_user: Optional[OperationalRef] = None
    """User reference if ``identity_context == "run_as_user"``."""

    execution_mechanism: Literal["direct_api", "anonymous_apex"]
    """``direct_api`` issues calls via the REST/SOAP/Bulk
    surface; ``anonymous_apex`` wraps the operations in an
    Apex block."""

    steps: Annotated[list[DataRecipeStep], ArraySemantics.ORDERED]
    """Ordered list of recipe steps. Per D-059 §6.3.4, marked
    ``ORDERED`` so canonicalization preserves step sequence (the
    order is semantically meaningful — step_2 reads what step_1
    created)."""

    @model_validator(mode="after")
    def _identity_context_coupling(self) -> "DataRecipeBody":
        if self.identity_context == "run_as_user":
            if self.run_as_user is None:
                raise ValueError(
                    "identity_context='run_as_user' requires "
                    "run_as_user to be set"
                )
        else:  # "system"
            if self.run_as_user is not None:
                raise ValueError(
                    "identity_context='system' forbids run_as_user; "
                    "got a non-None reference"
                )
        return self

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
from primeqa.test_representation.models.primitives import (
    AssertionPredicate,
    RejectionExpectation,
)
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
    """Insert a new record.

    ``expect_rejection`` (D-110.1) makes this a *behavioral-negative*
    step: when set, the create is expected to be **rejected** by the
    org (a validation rule / constraint), and the recipe verifies the
    rejection matches the carried :class:`RejectionExpectation`. It is
    the operational projection of the prohibition claim's
    ``expected_rejection`` — scalars only (no IdentityBearingRef), so
    the recipe stays in the operational layer. ``None`` (the default)
    is an ordinary create — including the *setup* create of a 2-step
    update/delete-rejected negative (D-203).
    """

    kind: Literal["create"] = "create"
    target_object: OperationalRef
    field_values: dict[str, Any]
    expect_rejection: Optional[RejectionExpectation] = None
    expect_acceptance: bool = False
    """D-305: ``expect_rejection``'s mirror — this create IS the assertion
    (the acceptance archetype). When True, S4 grades a STRUCTURED business
    rejection (HTTP 400 + a parseable error body) as ``failed`` with the
    rejecting rule attributed — for an acceptance claim the rejection is the
    FINDING, never an indeterminate staging error; transport/ambiguous stays
    ``errored``. Default False = every pre-D-305 recipe byte-identical."""


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
    """Update an existing record.

    ``expect_rejection`` (D-203) makes this the rejected mutation of a
    *behavioral-negative* recipe: the update is expected to be rejected
    by the org, verified against the carried
    :class:`RejectionExpectation`. The subject record comes from the
    recipe's prior setup :class:`CreateStep` (positional binding — the
    bridge resolves it; see D-203). ``None`` is an ordinary update.
    """

    kind: Literal["update"] = "update"
    target: OperationalRef
    field_changes: dict[str, Any]
    expect_rejection: Optional[RejectionExpectation] = None
    expect_acceptance: bool = False
    """D-306: the update IS (part of) the assertion — a business
    rejection of it grades ``failed`` (the org refused a change that
    must succeed), never an indeterminate. Mirrors CreateStep (D-305).
    Default False = every pre-D-306 recipe byte-identical."""


class DeleteStep(_StepBase):
    """Delete a record.

    ``expect_rejection`` (D-203): same behavioral-negative semantics as
    :class:`UpdateStep` — the delete is expected to be rejected by the
    org. ``None`` is an ordinary delete.
    """

    kind: Literal["delete"] = "delete"
    target: OperationalRef
    expect_rejection: Optional[RejectionExpectation] = None


class ApprovalActionStep(_StepBase):
    """Perform an approval action on the recipe's subject record (D-333,
    the approval-action arc).

    ``submit`` submits the record for approval (creates a ProcessInstance);
    ``approve`` / ``reject`` act on the record's pending workitem. The
    subject record binds POSITIONALLY — the record the recipe's setup
    (terminal) create made, exactly like the D-203 rejected-mutation
    binding — so the step carries no target of its own. ``comment`` rides
    the action verbatim (evidence context; never asserted on).

    Operational only: the arc's IDENTITY lives on the claim body
    (``approval_actions`` — prohibition-claim v2 / acceptance-claim v3);
    this step realizes it."""

    kind: Literal["approval_action"] = "approval_action"
    action: Literal["submit", "approve", "reject"]
    comment: Optional[str] = None


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
        AssertStep, ApexStep, ApprovalActionStep,
    ],
    Field(discriminator="kind"),
]
"""Discriminated union over the seven data-recipe step variants.
``ApprovalActionStep`` (D-333) widens v1 additively — persisted
pre-D-333 recipes carry none, so they decode byte-identically (the
D-203 ``expect_rejection`` / D-305 ``expect_acceptance`` precedent)."""


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

    @model_validator(mode="after")
    def _at_most_one_expect_rejection(self) -> "DataRecipeBody":
        """At-most-one ``expect_rejection`` across mutation steps
        (D-110.1): 0 = ordinary recipe, 1 = behavioral negative; ≥2
        rejected — a recipe asserts at most one prohibition.

        At-most-one (not exactly-one) so positive data-recipes (zero
        expect_rejection) stay valid. Update / delete steps carry the
        flag since D-203 and are counted here — a 2-step negative
        (setup create with ``None`` + one flagged mutation) passes;
        two flagged steps do not.
        """
        n = sum(
            1 for s in self.steps
            if getattr(s, "expect_rejection", None) is not None
        )
        if n > 1:
            raise ValueError(
                f"at most one step may carry expect_rejection (a recipe "
                f"asserts at most one prohibition); found {n}"
            )
        return self

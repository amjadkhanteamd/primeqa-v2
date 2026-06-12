"""metadata-recipe body (observation-realization layer).

Per SPEC §3 + D-054. A metadata-recipe observes/asserts via the
Metadata API or Tooling API — reading current metadata for
assertion, or deploying metadata changes (when used as a
test-fixture mutation surface).

Per D-054 sub-discriminators: ``metadata-recipe`` has a ``mode``
sub-discriminator distinguishing read-only observation from
write deployment. The Pydantic layer enforces that ``DeployStep``
instances only appear in ``metadata_write`` recipes — read-only
recipes that try to deploy fail validation.
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
# Step variants
# ---------------------------------------------------------------------------

class _StepBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: str


class ReadMetadataStep(_StepBase):
    """Fetch one or more fields from the live metadata of an
    entity.

    D-224: when the capture is an S1 edge whose realization needs the
    edge's FAR endpoint to scope correctly (``GRANTS_*_ACCESS``,
    ``INCLUDES_FIELD``), the step carries it as ``edge_target`` plus an
    optional ``edge_qualifier`` (the granted capability, e.g. ``"edit"``).
    The operational realization is self-contained — S4 never re-derives
    scope from the claim body at run time. Both default to ``None`` so
    pre-D-224 persisted recipes rehydrate unchanged.
    """

    kind: Literal["read_metadata"] = "read_metadata"
    target_entity: OperationalRef
    fields_to_capture: list[str] = []
    edge_target: Optional[OperationalRef] = None
    edge_qualifier: Optional[str] = None


class DeployStep(_StepBase):
    """Deploy a metadata payload.

    Only valid in ``metadata_write`` mode; the body-level
    validator rejects DeployStep instances when mode is
    ``metadata_read``.

    ``deployment_payload`` is loosely typed because the Metadata
    / Tooling API payload shape varies wildly by component type
    (CustomObject, ValidationRule, FlowDefinition...). Structured
    per-component representation deferred.
    """

    kind: Literal["deploy"] = "deploy"
    deployment_payload: dict[str, Any]


class RetrieveStep(_StepBase):
    """Retrieve metadata and bind it into a named capture slot
    for later assertion."""

    kind: Literal["retrieve"] = "retrieve"
    target_entity: OperationalRef
    into_field: str


class AssertStep(_StepBase):
    """Assert a predicate over a captured value."""

    kind: Literal["assert"] = "assert"
    predicate: AssertionPredicate


MetadataRecipeStep = Annotated[
    Union[ReadMetadataStep, DeployStep, RetrieveStep, AssertStep],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# MetadataRecipeBody
# ---------------------------------------------------------------------------

@register_body("metadata-recipe", 1)
class MetadataRecipeBody(BodyBase):
    """The metadata-recipe body shape (v1).

    Body-level validator: a ``metadata_read`` recipe must NOT
    contain :class:`DeployStep` instances. ``metadata_write``
    recipes can mix deploy + retrieve + assert.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["metadata-recipe"] = "metadata-recipe"

    mode: Literal["metadata_read", "metadata_write"]
    """Sub-discriminator per D-054: read-only observation vs
    deploying-then-observing."""

    api_choice: Literal["metadata_api", "tooling_api"]
    """The Salesforce API surface the recipe uses."""

    steps: Annotated[list[MetadataRecipeStep], ArraySemantics.ORDERED]

    @model_validator(mode="after")
    def _deploy_steps_only_in_write_mode(self) -> "MetadataRecipeBody":
        if self.mode == "metadata_read":
            for idx, step in enumerate(self.steps):
                if isinstance(step, DeployStep):
                    raise ValueError(
                        f"steps[{idx}] is a DeployStep but mode is "
                        f"'metadata_read'; deploys require "
                        f"mode='metadata_write'"
                    )
        return self

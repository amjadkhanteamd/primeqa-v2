"""data-mutation-trigger body (operational layer, runtime plane).

Per SPEC §3 + D-055. A data-mutation-trigger represents DML on
records inside Salesforce — record create / update / delete /
undelete / upsert. Distinct from inbound-trigger (which is the
external system pushing payloads in); here the actor is inside
Salesforce, even if a workflow or user-action originally caused
the mutation.

Operational layer; refs use :data:`OperationalRef` (logical by
default, pinned opt-in).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import ConfigDict, model_validator

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import OperationalRef
from primeqa.test_representation.models.registry import register_body


@register_body("data-mutation-trigger", 1)
class DataMutationTriggerBody(BodyBase):
    """The data-mutation-trigger body shape (v1).

    Cross-field invariant: ``run_as_user`` is required iff
    ``identity_context == "run_as_user"``. Enforced by a
    model_validator so misconfigured triggers fail at validation
    rather than producing confusing run-as semantics at execution
    time.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["data-mutation-trigger"] = "data-mutation-trigger"

    operation: Literal[
        "create",
        "update",
        "delete",
        "undelete",
        "upsert",
    ]
    """The DML operation that fires the trigger."""

    target: OperationalRef
    """The S1 entity the operation acts on (typically the Object,
    or a specific record-typed reference). Operational-layer ref:
    logical by default, pinned opt-in."""

    identity_context: Literal["system", "run_as_user"]
    """Whether the DML is performed under system context (no
    user, full FLS bypass) or under a specific user's identity."""

    run_as_user: Optional[OperationalRef] = None
    """The user reference if ``identity_context == "run_as_user"``.
    Required in that case; forbidden when identity_context is
    ``"system"``. Enforced by the cross-field validator below."""

    volume: Literal["single", "bulk"]
    """Whether the operation handles one record (``single``) or
    multiple in the same DML batch (``bulk``). Drives recipe
    selection for bulk-only paths."""

    field_changes: Optional[dict[str, Any]] = None
    """For ``update`` / ``upsert``: the field values being written.
    Loosely typed at v1 (``dict[str, Any]``) so the substrate
    doesn't second-guess source-system shapes. The Coordinator
    enforces that referenced fields exist on the target type."""

    @model_validator(mode="after")
    def _identity_context_coupling(self) -> "DataMutationTriggerBody":
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

"""time-trigger body (operational layer, runtime plane).

Per SPEC §3 + D-055. A time-trigger represents Salesforce
firing automation because elapsed-time predicates were met —
scheduled flows, batch apex, time-based workflows, time-dependent
field updates, and the test-set-created-date variant for
provisioning fixture data with predictable timestamps.

Operational layer; refs use :data:`OperationalRef`.

The TimePredicate cross-field validator enforces that exactly one
context field is set, matching the declared predicate_kind:
  - after_duration ↔ duration_seconds
  - at_specific_time ↔ specific_time_iso
  - on_recurring_schedule ↔ recurrence_description

Multi-context or missing-context configurations are caught at
validation time.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import OperationalRef
from primeqa.test_representation.models.registry import register_body


# ---------------------------------------------------------------------------
# TimePredicate
# ---------------------------------------------------------------------------

# Mapping from predicate_kind → the field that must be non-None.
_PREDICATE_FIELD = {
    "after_duration": "duration_seconds",
    "at_specific_time": "specific_time_iso",
    "on_recurring_schedule": "recurrence_description",
}
_ALL_CONTEXT_FIELDS = set(_PREDICATE_FIELD.values())


class TimePredicate(BaseModel):
    """The time predicate that triggers the firing.

    Exactly one of (``duration_seconds``, ``specific_time_iso``,
    ``recurrence_description``) is non-None, and the non-None
    field MUST match the declared ``predicate_kind`` per
    :data:`_PREDICATE_FIELD`. Enforced by a model_validator.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    predicate_kind: Literal[
        "after_duration",
        "at_specific_time",
        "on_recurring_schedule",
    ]
    """The kind of time predicate."""

    duration_seconds: Optional[int] = None
    """For ``after_duration``: how many seconds after the
    triggering event the action fires."""

    specific_time_iso: Optional[str] = None
    """For ``at_specific_time``: an ISO-8601 timestamp string."""

    recurrence_description: Optional[str] = None
    """For ``on_recurring_schedule``: free-form description of
    the recurrence (cron expression, Salesforce schedule string,
    human-readable cadence). Free-form at v1; a structured
    recurrence model is deferred."""

    @model_validator(mode="after")
    def _exactly_one_context_field(self) -> "TimePredicate":
        expected = _PREDICATE_FIELD[self.predicate_kind]
        present = {
            field for field in _ALL_CONTEXT_FIELDS
            if getattr(self, field) is not None
        }
        if expected not in present:
            raise ValueError(
                f"predicate_kind={self.predicate_kind!r} requires "
                f"{expected!r} to be set"
            )
        extra = present - {expected}
        if extra:
            raise ValueError(
                f"predicate_kind={self.predicate_kind!r} permits only "
                f"{expected!r}; got additional context fields "
                f"{sorted(extra)}"
            )
        return self


# ---------------------------------------------------------------------------
# TimeTriggerBody
# ---------------------------------------------------------------------------

@register_body("time-trigger", 1)
class TimeTriggerBody(BodyBase):
    """The time-trigger body shape (v1).

    ``scheduled_target`` references the automation that's been
    scheduled to fire (the scheduled Flow, the batch Apex class,
    the time-based workflow rule). Optional because
    ``test_set_created_date`` triggers don't pin to a scheduled
    automation — they're fixture-data primitives that pre-date a
    record's createdDate to a chosen point so subsequent
    time-based logic sees the desired elapsed time.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["time-trigger"] = "time-trigger"

    mechanism: Literal[
        "scheduled_flow",
        "batch_apex",
        "time_based_workflow",
        "time_dependent_field_update",
        "test_set_created_date",
    ]
    """The Salesforce mechanism that realises the time-based
    firing."""

    scheduled_target: Optional[OperationalRef] = None
    """The automation entity being scheduled (the Flow, the
    Apex class, the workflow rule). Optional — see body
    docstring."""

    time_predicate: TimePredicate
    """The time predicate. Required because every time-trigger
    has *some* time semantics."""

"""state-transition-claim body shape (data-behavior archetype).

Per SPEC §3 + D-053. A state-transition-claim asserts: "when the
triggering event occurs against this subject in the ``from_state``,
the subject moves to the ``to_state``." This is the canonical
shape for record-state changes driven by user or system actions.

Use cases:
  - "When user clicks Submit on Opportunity in stage=Prospecting,
    it moves to stage=Submitted." (UI trigger)
  - "When a Case is closed, its ClosedDate is populated."
    (data-mutation trigger; the ``to_state`` includes the populated
    date field.)

Distinct from :mod:`automation_effect_claim`: state-transition
captures *what* the record state becomes; automation-effect captures
*what an automation produced* (which may or may not be a state
change — it could be a side effect or a block).
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import (
    ArraySemantics,
    BodyBase,
)
from primeqa.test_representation.models.primitives import (
    EventDescriptor,
    StateDescriptor,
)
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("state-transition-claim", 1)
class StateTransitionClaimBody(BodyBase):
    """The state-transition-claim body shape (v1).

    Per D-058 §5.4: ``subject`` and ``subject_fields`` are both
    walkable for coverage extraction without crawling into
    :class:`StateDescriptor`. The dict keys inside
    ``from_state``/``to_state`` are field external_ids (flat
    strings) for clean JSON round-tripping; the
    ``subject_fields`` list pins the IdentityBearingRefs to those
    fields so coverage extraction has direct access to them
    without parsing external_ids.

    The duplication between ``subject_fields`` and the
    ``from_state.field_values`` / ``to_state.field_values`` keys
    is intentional and well-known — see the StateDescriptor
    docstring in :mod:`..primitives` for the trade-off.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["state-transition-claim"] = "state-transition-claim"

    subject: IdentityBearingRef
    """The record-type whose state transitions (e.g., the
    Opportunity Object reference). NOT a field reference — this
    is the Object that owns the changing fields."""

    subject_fields: Annotated[
        list[IdentityBearingRef], ArraySemantics.SET,
    ]
    """The Field references mentioned in ``from_state`` and
    ``to_state``. Required for D-058 §5.4 coverage extraction:
    state dicts key by external_id string, so the explicit
    IdentityBearingRefs live here. Marked
    :class:`ArraySemantics.SET` per D-059 §6.3.4 — order is
    incidental, identity is by the set of entity_id values. May
    be empty if the transition is observed via a different
    mechanism (e.g., record existence rather than field values),
    though that's atypical for v1."""

    from_state: StateDescriptor
    """The pre-event state. Field values keyed by field
    external_id."""

    to_state: StateDescriptor
    """The post-event state. Same keying as ``from_state``."""

    triggering_event: EventDescriptor
    """The causal initiation that drives the transition. Per
    D-055, the trigger_kind is operational-by-default; if the
    trigger mechanism is semantically asserted, the Coordinator
    routes that assertion to the conditions body."""

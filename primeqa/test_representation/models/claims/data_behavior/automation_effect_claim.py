"""automation-effect-claim body shape (data-behavior archetype).

Per SPEC §3 + D-053. An automation-effect-claim asserts: "when
the triggering action fires, the named automation produces
``expected_effect``." Distinct from state-transition: this claim
centers the *automation* (the Validation Rule, Flow, Apex
Trigger, etc.) and asserts *what it does*; the state-transition
claim centers the *subject record* and asserts what its state
becomes.

Per D-053's mechanism vs semantic guardrail: this single
claim-kind covers validation-rule-firing, flow-firing,
apex-trigger-firing, etc., distinguished by the
``automation_primitive`` sub-discriminator. Different mechanisms,
same semantic ("an automation produced an effect"). Splitting
each mechanism into its own claim-kind would conflate mechanism
with semantic and is explicitly disallowed by §3.

Use cases:
  - "When user inserts a Case with no Status, the Required
    Validation Rule fires and the operation is blocked."
  - "When Opportunity.IsClosed flips to true, the
    'Stamp ClosedDate' Flow updates ClosedDate to today."
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.primitives import (
    EffectDescriptor,
    EventDescriptor,
)
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("automation-effect-claim", 1)
class AutomationEffectClaimBody(BodyBase):
    """The automation-effect-claim body shape (v1).

    Per D-053's "different mechanism alone → not a new
    claim-kind" guardrail, the ``automation_primitive``
    sub-discriminator captures the Salesforce mechanism without
    creating a separate claim-kind per primitive.

    Per D-058 §5.4: ``automation`` and ``affected_fields`` are
    walkable for coverage extraction. The ``affected_fields``
    list mirrors any field external_ids appearing inside
    ``expected_effect.changes`` (when the effect is a
    FieldChangeEffect) for the same reason as the
    StateTransitionClaimBody's ``subject_fields``: dict keys are
    strings, so the IdentityBearingRefs live in an explicit list.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["automation-effect-claim"] = "automation-effect-claim"

    automation: IdentityBearingRef
    """The automation entity that fires (e.g., the Validation
    Rule or Flow). Pinned per D-058 §5: replacing the automation
    changes the test's meaning."""

    automation_primitive: Literal[
        "validation_rule",
        "flow",
        "apex_trigger",
        "process_builder",
        "approval_process",
    ]
    """Sub-discriminator for the Salesforce mechanism. Per
    D-053's guardrail, mechanisms occupy a sub-discriminator slot
    rather than spawning per-mechanism claim-kinds."""

    triggering_action: EventDescriptor
    """The causal action that makes the automation fire (e.g., a
    record insert, a UI button click)."""

    expected_effect: EffectDescriptor
    """The effect the automation produces — field changes, an
    operation block, or a side effect outside the record.
    Discriminated union over the three effect shapes."""

    affected_fields: list[IdentityBearingRef]
    """The Field references mentioned in
    ``expected_effect.changes`` (when applicable). Required for
    D-058 §5.4 coverage extraction; empty for effects that
    don't touch fields (BlockedOperationEffect / SideEffect)."""

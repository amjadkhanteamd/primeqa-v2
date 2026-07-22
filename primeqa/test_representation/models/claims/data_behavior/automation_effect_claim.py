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

from typing import Annotated, Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import (
    ArraySemantics,
    BodyBase,
)
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
        "formula",
    ]
    """Sub-discriminator for the Salesforce mechanism. Per
    D-053's guardrail, mechanisms occupy a sub-discriminator slot
    rather than spawning per-mechanism claim-kinds. ``formula``
    (D-304): the automation is a CALCULATED FIELD — the
    ``automation`` ref is that Field, the org's formula engine is
    the mechanism, and the D-299 ``trigger_fields`` carry the
    formula's inputs."""

    triggering_action: EventDescriptor
    """The causal action that makes the automation fire (e.g., a
    record insert, a UI button click)."""

    expected_effect: EffectDescriptor
    """The effect the automation produces — field changes, an
    operation block, or a side effect outside the record.
    Discriminated union over the three effect shapes."""

    affected_fields: Annotated[
        list[IdentityBearingRef], ArraySemantics.SET,
    ]
    """The Field references mentioned in
    ``expected_effect.changes`` (when applicable). Required for
    D-058 §5.4 coverage extraction; empty for effects that
    don't touch fields (BlockedOperationEffect / SideEffect).
    Marked :class:`ArraySemantics.SET` per D-059 §6.3.4 — order
    is incidental, identity is by the set of entity_id values."""


@register_body("automation-effect-claim", 2)
class AutomationAbsenceClaimBody(BodyBase):
    """The automation-effect-claim body shape (v2) — the ABSENCE case
    (D-307). "When the triggering action fires under this state, the
    automation correctly produces NO correlated record."

    Why a new version, not a v1 field (the D-306.1 B1 law):
    canonicalization includes every model field, so adding a slot to v1
    would re-key every existing automation-effect claim (dedup misses,
    duplicate claims). v2's distinct key-set keeps absence and presence
    claims hashing apart by construction.

    v1 scope (deliberately narrow): absence of the CORRELATED RECORD as
    a whole — the cross-object shape's mirror (TC-038/039: Medium/Low
    band → NO follow-up Task). A field-conditional absence ("no record
    WITH Level=ERROR") is not expressible and refuses at the stash gate.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[2] = 2
    kind: Literal["automation-effect-claim"] = "automation-effect-claim"

    automation: IdentityBearingRef
    """The automation asserted to stay silent. Pinned per D-058 §5."""

    automation_primitive: Literal[
        "validation_rule",
        "flow",
        "apex_trigger",
        "process_builder",
        "approval_process",
        "formula",
    ]
    """Same sub-discriminator as v1 (D-053's guardrail)."""

    triggering_action: EventDescriptor
    """The causal action under which the automation must NOT produce
    the record — the staged entry state rides its description (the
    D-299 idiom), so 'no task at Medium' and 'no task at Low' are
    distinct claims."""

    expected_absence: Literal[True] = True
    """The discriminating assertion: the correlated record must NOT
    exist after the trigger. Literal[True] — an absence body IS the
    assertion; a False would be a presence claim (v1's job)."""


@register_body("automation-effect-claim", 3)
class AutomationConditionalAbsenceClaimBody(BodyBase):
    """The automation-effect-claim body shape (v3) — the CONDITIONAL
    absence (D-381). "When the triggering action fires, correlated records
    MATCHING the protecting condition keep their value — the automation's
    fan-out provably excludes them" (AC11: cancelling an order leaves
    already-Completed fulfilment tasks untouched).

    A new version, not a v2 field (the D-306.1 B1 law): canonicalization
    includes every model field, so extending v2 would re-key every existing
    absence claim; v3's distinct key-set keeps conditional and plain absence
    hashing apart by construction.

    Grounding law (D-381): expressible ONLY when the bound flow's own
    update-op filter pins the condition field to a DIFFERENT value — the
    exclusion is proven from what the op itself declares, never assumed.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[3] = 3
    kind: Literal["automation-effect-claim"] = "automation-effect-claim"

    automation: IdentityBearingRef
    """The automation whose fan-out must exclude the protected rows."""

    automation_primitive: Literal[
        "validation_rule",
        "flow",
        "apex_trigger",
        "process_builder",
        "approval_process",
        "formula",
    ]
    """Same sub-discriminator as v1/v2 (D-053's guardrail)."""

    triggering_action: EventDescriptor
    """The causal action (the staged entry transition) under which the
    protected rows must stay untouched."""

    protected_field: IdentityBearingRef
    """The condition field on the EFFECT object (pinned per D-058 §5)."""

    protected_value: str
    """The protecting value — a correlated row staged at this value must
    still carry it after the trigger."""

    expected_absence: Literal[True] = True
    """The discriminating assertion family: an absence-of-EFFECT on the
    protected set (not absence of the record itself — that is v2)."""

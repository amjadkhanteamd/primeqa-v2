"""Tests for :class:`AutomationEffectClaimBody`.

Per SPEC §3 + D-053. Covers:

  - Construction across all three EffectDescriptor variants
    (field_change / blocked_operation / side_effect).
  - All five automation_primitive sub-discriminator values
    accepted.
  - Identity-bearing slots (``automation``, every
    ``affected_fields`` entry) constructed as
    :class:`IdentityBearingRef` instances.
  - Logical refs rejected at identity-bearing slots.
  - Registry registration on import.
  - Full round-trip preserves nested type discipline.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from primeqa.test_representation.models.claims.data_behavior.automation_effect_claim import (
    AutomationEffectClaimBody,
)
from primeqa.test_representation.models.primitives import (
    BlockedOperationEffect,
    EventDescriptor,
    FieldChangeEffect,
    LiteralValue,
    SideEffect,
    StateDescriptor,
)
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)


def _ib_ref(
    name: str = "ValidationRule",
    entity_type: str = "ValidationRule",
) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type=entity_type,
        entity_id=uuid4(),
        version_seq=1,
        external_id=name,
    )


def _field_ref(name: str) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type="Field",
        entity_id=uuid4(),
        version_seq=1,
        external_id=name,
    )


class TestAutomationEffectConstruction:
    @pytest.mark.parametrize("primitive", [
        "validation_rule", "flow", "apex_trigger",
        "process_builder", "approval_process",
    ])
    def test_all_primitives_accepted(self, primitive: str) -> None:
        body = AutomationEffectClaimBody(
            automation=_ib_ref(),
            automation_primitive=primitive,  # type: ignore[arg-type]
            triggering_action=EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description="record insert",
            ),
            expected_effect=BlockedOperationEffect(reason="x"),
            affected_fields=[],
        )
        assert body.automation_primitive == primitive

    def test_unknown_primitive_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutomationEffectClaimBody(
                automation=_ib_ref(),
                automation_primitive="webhook",  # type: ignore[arg-type]
                triggering_action=EventDescriptor(
                    trigger_kind="ui-trigger", description="x",
                ),
                expected_effect=SideEffect(description="x"),
                affected_fields=[],
            )

    def test_with_field_change_effect(self) -> None:
        body = AutomationEffectClaimBody(
            automation=_ib_ref("Stamp Close Date", entity_type="Flow"),
            automation_primitive="flow",
            triggering_action=EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description="IsClosed flips to true",
            ),
            expected_effect=FieldChangeEffect(
                changes=StateDescriptor(field_values={
                    "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
                }),
            ),
            affected_fields=[_field_ref("Opportunity.CloseDate")],
        )
        assert type(body.expected_effect) is FieldChangeEffect
        assert body.affected_fields[0].external_id == "Opportunity.CloseDate"

    def test_with_blocked_operation(self) -> None:
        body = AutomationEffectClaimBody(
            automation=_ib_ref("Required Industry"),
            automation_primitive="validation_rule",
            triggering_action=EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description="record insert",
            ),
            expected_effect=BlockedOperationEffect(
                reason="Industry is required",
            ),
            affected_fields=[],
        )
        assert type(body.expected_effect) is BlockedOperationEffect

    def test_with_side_effect(self) -> None:
        body = AutomationEffectClaimBody(
            automation=_ib_ref("Notify Owner", entity_type="Flow"),
            automation_primitive="flow",
            triggering_action=EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description="status change",
            ),
            expected_effect=SideEffect(description="email to owner"),
            affected_fields=[],
        )
        assert type(body.expected_effect) is SideEffect

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            AutomationEffectClaimBody(
                automation=_ib_ref(),
                automation_primitive="flow",
                triggering_action=EventDescriptor(
                    trigger_kind="ui-trigger", description="x",
                ),
                expected_effect=SideEffect(description="x"),
                affected_fields=[],
                kind="prohibition-claim",  # type: ignore[arg-type]
            )

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            AutomationEffectClaimBody(  # type: ignore[call-arg]
                automation=_ib_ref(),
                automation_primitive="flow",
                triggering_action=EventDescriptor(
                    trigger_kind="ui-trigger", description="x",
                ),
                expected_effect=SideEffect(description="x"),
                affected_fields=[],
                surprise=True,
            )


class TestAutomationEffectRefDiscipline:
    def test_automation_constructed_as_identity_bearing(self) -> None:
        body = AutomationEffectClaimBody.model_validate({
            "automation": {
                "ref_kind": "pinned",
                "entity_type": "Flow",
                "entity_id": str(uuid4()),
                "version_seq": 1,
                "external_id": "Stamp Date",
            },
            "automation_primitive": "flow",
            "triggering_action": {
                "trigger_kind": "ui-trigger", "description": "x",
            },
            "expected_effect": {"kind": "side_effect", "description": "x"},
            "affected_fields": [],
        })
        assert type(body.automation) is IdentityBearingRef

    def test_affected_fields_each_identity_bearing(self) -> None:
        body = AutomationEffectClaimBody.model_validate({
            "automation": {
                "ref_kind": "pinned",
                "entity_type": "Flow",
                "entity_id": str(uuid4()),
                "version_seq": 1,
                "external_id": "F",
            },
            "automation_primitive": "flow",
            "triggering_action": {
                "trigger_kind": "ui-trigger", "description": "x",
            },
            "expected_effect": {
                "kind": "field_change",
                "changes": {"field_values": {
                    "X.Y": {"kind": "literal", "value": 1},
                }},
            },
            "affected_fields": [{
                "ref_kind": "pinned",
                "entity_type": "Field",
                "entity_id": str(uuid4()),
                "version_seq": 1,
                "external_id": "X.Y",
            }],
        })
        for ref in body.affected_fields:
            assert type(ref) is IdentityBearingRef

    def test_logical_ref_rejected_at_automation_slot(self) -> None:
        with pytest.raises(ValidationError):
            AutomationEffectClaimBody.model_validate({
                "automation": {
                    "ref_kind": "logical",
                    "entity_type": "Flow",
                    "external_id": "F",
                },
                "automation_primitive": "flow",
                "triggering_action": {
                    "trigger_kind": "ui-trigger", "description": "x",
                },
                "expected_effect": {
                    "kind": "side_effect", "description": "x",
                },
                "affected_fields": [],
            })


class TestAutomationEffectSerialization:
    def test_universal_body_keys_present(self) -> None:
        body = AutomationEffectClaimBody(
            automation=_ib_ref(),
            automation_primitive="flow",
            triggering_action=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
            expected_effect=SideEffect(description="x"),
            affected_fields=[],
        )
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "automation-effect-claim"

    def test_full_round_trip(self) -> None:
        body = AutomationEffectClaimBody(
            automation=_ib_ref("F", entity_type="Flow"),
            automation_primitive="flow",
            triggering_action=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
            expected_effect=FieldChangeEffect(
                changes=StateDescriptor(field_values={
                    "A.B": LiteralValue(value="z"),
                }),
            ),
            affected_fields=[_field_ref("A.B")],
        )
        roundtrip = AutomationEffectClaimBody.model_validate(
            body.model_dump(),
        )
        assert roundtrip == body
        assert type(roundtrip.expected_effect) is FieldChangeEffect


class TestAutomationEffectRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("automation-effect-claim", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model(
            "automation-effect-claim", 1,
        ) is AutomationEffectClaimBody


class TestAutomationEffectArraySemanticsMarker:
    def test_affected_fields_marked_set(self) -> None:
        """Per Track C: affected_fields is a set of field
        references; order is incidental. The
        :class:`ArraySemantics.SET` marker is required for the
        automation-effect canonicalizer to sort the list and to
        match the field_values dict keys correctly when
        ``expected_effect`` is a
        :class:`FieldChangeEffect`."""
        from primeqa.test_representation.models.common import ArraySemantics

        metadata = AutomationEffectClaimBody.model_fields[
            "affected_fields"
        ].metadata
        assert ArraySemantics.SET in metadata

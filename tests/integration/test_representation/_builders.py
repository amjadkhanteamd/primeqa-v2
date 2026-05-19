"""Body-construction helpers for write_claim integration tests.

Centralizes the fixture-data builders so each test file can focus
on the orchestration concern under test rather than the body
shape. All helpers produce realistic claim bodies of the four
data-behavior kinds plus the conditions body.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from primeqa.test_representation import (
    AutomationEffectClaimBody,
    Condition,
    EventDescriptor,
    FieldChangeEffect,
    IdentityBearingRef,
    LiteralValue,
    NullValue,
    ProhibitionClaimBody,
    RejectionSignal,
    SemanticConditionsBody,
    SideEffect,
    StateDescriptor,
    StateTransitionClaimBody,
    ValueClaimBody,
)


def ib(
    entity_type: str = "Field",
    entity_id: UUID | None = None,
    external_id: str = "Account.Industry",
    version_seq: int = 1,
) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type=entity_type,
        entity_id=entity_id or uuid4(),
        version_seq=version_seq,
        external_id=external_id,
    )


def empty_conditions() -> SemanticConditionsBody:
    return SemanticConditionsBody()


def make_value_claim(
    subject: IdentityBearingRef | None = None,
    value: str = "Tech",
) -> ValueClaimBody:
    return ValueClaimBody(
        subject=subject or ib(),
        expected_value=LiteralValue(value=value),
    )


def make_state_transition(
    subject_id: UUID | None = None,
    stage_id: UUID | None = None,
    from_stage: str = "Prospecting",
    to_stage: str = "Closed Won",
) -> StateTransitionClaimBody:
    subj = ib(
        entity_type="Object",
        entity_id=subject_id or uuid4(),
        external_id="Opportunity",
    )
    stage_field = ib(
        entity_type="Field",
        entity_id=stage_id or uuid4(),
        external_id="Opportunity.StageName",
    )
    return StateTransitionClaimBody(
        subject=subj,
        subject_fields=[stage_field],
        from_state=StateDescriptor(field_values={
            "Opportunity.StageName": LiteralValue(value=from_stage),
        }),
        to_state=StateDescriptor(field_values={
            "Opportunity.StageName": LiteralValue(value=to_stage),
        }),
        triggering_event=EventDescriptor(
            trigger_kind="ui-trigger", description="user clicks Close",
        ),
    )


def make_automation_effect(
    automation_id: UUID | None = None,
    field_id: UUID | None = None,
) -> AutomationEffectClaimBody:
    automation = ib(
        entity_type="Flow",
        entity_id=automation_id or uuid4(),
        external_id="StampCloseDate",
    )
    field_ref = ib(
        entity_type="Field",
        entity_id=field_id or uuid4(),
        external_id="Opportunity.CloseDate",
    )
    return AutomationEffectClaimBody(
        automation=automation,
        automation_primitive="flow",
        triggering_action=EventDescriptor(
            trigger_kind="data-mutation-trigger",
            description="Opportunity update",
        ),
        expected_effect=FieldChangeEffect(
            changes=StateDescriptor(field_values={
                "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
            }),
        ),
        affected_fields=[field_ref],
    )


def make_prohibition(
    target_id: UUID | None = None,
) -> ProhibitionClaimBody:
    return ProhibitionClaimBody(
        target=ib(
            entity_type="Object",
            entity_id=target_id or uuid4(),
            external_id="Opportunity",
        ),
        operation="delete",
        prohibition_mechanism="validation_rule",
        expected_rejection=RejectionSignal(
            error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION",
        ),
    )

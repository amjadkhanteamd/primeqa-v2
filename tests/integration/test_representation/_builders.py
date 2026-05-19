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


# ---------------------------------------------------------------------------
# Operational-layer body builders (Track D-β.3)
#
# Recipe bodies and trigger bodies use ``OperationalRef``
# (PinnedRef | LogicalRef), not IdentityBearingRef. The builders
# below produce minimally-valid bodies; callers override fields
# as needed.
# ---------------------------------------------------------------------------


from primeqa.test_representation import (
    CalloutInterceptRecipeBody,
    ConfigurationTriggerBody,
    DataMutationTriggerBody,
    DataRecipeBody,
    EventSubscriptionRecipeBody,
    ExecutionEnvironmentBody,
    InboundTriggerBody,
    LogicalRef,
    MetadataRecipeBody,
    PageContext,
    PayloadDescriptor,
    TimePredicate,
    TimeTriggerBody,
    UIRecipeBody,
    UITriggerBody,
)


def logical_ref(
    entity_type: str = "Object",
    external_id: str = "Opportunity",
) -> LogicalRef:
    return LogicalRef(
        entity_type=entity_type,
        external_id=external_id,
    )


# ----- Trigger bodies -----


def build_minimal_inbound_trigger_body() -> InboundTriggerBody:
    return InboundTriggerBody(
        channel="rest",
        payload=PayloadDescriptor(format="json", content={"a": 1}),
    )


def build_minimal_data_mutation_trigger_body(
) -> DataMutationTriggerBody:
    return DataMutationTriggerBody.model_validate({
        "operation": "create",
        "target": logical_ref().model_dump(),
        "identity_context": "system",
        "volume": "single",
    })


def build_minimal_ui_trigger_body() -> UITriggerBody:
    return UITriggerBody(
        action_type="button_click",
        page_context=PageContext(page_kind="record_detail"),
    )


def build_minimal_time_trigger_body() -> TimeTriggerBody:
    return TimeTriggerBody(
        mechanism="scheduled_flow",
        time_predicate=TimePredicate(
            predicate_kind="after_duration", duration_seconds=60,
        ),
    )


def build_minimal_configuration_trigger_body(
) -> ConfigurationTriggerBody:
    return ConfigurationTriggerBody.model_validate({
        "deploy_target": "activation_flag",
        "affected_entity": logical_ref(
            entity_type="ValidationRule",
            external_id="Account.RequireIndustry",
        ).model_dump(),
        "change_description": {
            "kind": "activation", "new_state": "active",
        },
    })


# ----- Recipe bodies -----


def build_minimal_data_recipe_body() -> DataRecipeBody:
    return DataRecipeBody.model_validate({
        "api_choice": "rest",
        "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": [],
    })


def build_minimal_metadata_recipe_body() -> MetadataRecipeBody:
    return MetadataRecipeBody.model_validate({
        "mode": "metadata_read",
        "api_choice": "metadata_api",
        "steps": [],
    })


def build_minimal_ui_recipe_body() -> UIRecipeBody:
    return UIRecipeBody.model_validate({
        "framework": "playwright",
        "identity_context": "system",
        "steps": [],
    })


def build_minimal_event_subscription_recipe_body(
) -> EventSubscriptionRecipeBody:
    return EventSubscriptionRecipeBody.model_validate({
        "channel_type": "platform_event",
        "subscription_mode": "ephemeral",
        "channel_target": logical_ref(
            entity_type="PlatformEvent",
            external_id="AccountUpdated__e",
        ).model_dump(),
        "steps": [],
    })


def build_minimal_callout_intercept_recipe_body(
) -> CalloutInterceptRecipeBody:
    return CalloutInterceptRecipeBody(
        interception_mechanism="proxy",
        steps=[],
    )


# ----- Execution environment -----


def build_minimal_execution_environment_body(
) -> ExecutionEnvironmentBody:
    """A minimally-valid environment body — empty assumption
    lists per the B-δ permissive structure."""
    return ExecutionEnvironmentBody()


# ----- Convenience: produce a written claim row to anchor recipes -


def write_anchor_claim(session, coord) -> tuple:
    """Helper for recipe tests: write a minimal value-claim
    against the test DB and return ``(test_id, version_seq)``
    for the recipe to reference."""
    body = make_value_claim()
    result = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    return result.test_id, result.version_seq

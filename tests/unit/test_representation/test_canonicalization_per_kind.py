"""Per-kind sensitivity + drift suites for canonicalization.

Per SPEC §6.3.3 + §6.3.4 + §6.3.9 + D-059. For each
identity-bearing body kind shipped to date, two parallel test
classes:

  - **Sensitivity** — semantic changes MUST change the hash.
    Adding a Condition, changing an entity_id, swapping a
    discriminator value, changing an ExpectedValue payload, etc.

  - **Drift preservation** — operational / informational changes
    MUST NOT change the hash. version_seq bumps inside
    IdentityBearingRef, external_id renames, SET-list reordering,
    dict-key reordering, etc. This is the substrate's
    autonomous-evolution invariant per D-059 §6.3.3 (the C0/C1
    drift cases).

Bodies covered:
  - :class:`ValueClaimBody`
  - :class:`StateTransitionClaimBody`
  - :class:`AutomationEffectClaimBody`
  - :class:`ProhibitionClaimBody`
  - :class:`SemanticConditionsBody` (via embedding under
    different claims to vary the conditions input)

All hashes are computed via :func:`compute_identity_hash` so the
suites exercise the full archetype + claim_kind +
canonicalize(asserted_truth) + canonicalize(semantic_conditions)
pipeline.
"""
from __future__ import annotations

from uuid import UUID

import pytest

from primeqa.test_representation import (
    AutomationEffectClaimBody,
    BlockedOperationEffect,
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
    compute_identity_hash,
)


# Stable test entity IDs — using fixed UUIDs so the suite is
# deterministic and easy to reason about.
EID_ACCOUNT = UUID("00000000-0000-0000-0000-000000000010")
EID_OPP = UUID("00000000-0000-0000-0000-000000000011")
EID_INDUSTRY = UUID("00000000-0000-0000-0000-000000000020")
EID_STAGE = UUID("00000000-0000-0000-0000-000000000021")
EID_CLOSE = UUID("00000000-0000-0000-0000-000000000022")
EID_OTHER = UUID("00000000-0000-0000-0000-0000000000aa")
EID_FLOW = UUID("00000000-0000-0000-0000-000000000030")
EID_VR = UUID("00000000-0000-0000-0000-000000000031")
EID_PROFILE = UUID("00000000-0000-0000-0000-000000000040")


def _ib(
    entity_type: str = "Field",
    entity_id: UUID = EID_INDUSTRY,
    version_seq: int = 1,
    external_id: str = "Account.Industry",
) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type=entity_type,
        entity_id=entity_id,
        version_seq=version_seq,
        external_id=external_id,
    )


def _no_conditions() -> SemanticConditionsBody:
    return SemanticConditionsBody()


def _hash(archetype: str, kind: str, body, conditions) -> str:
    return compute_identity_hash(archetype, kind, body, conditions)


# ---------------------------------------------------------------------------
# ValueClaimBody
# ---------------------------------------------------------------------------


def _value_claim(
    subject: IdentityBearingRef = None,
    value=None,
    null: bool = False,
) -> ValueClaimBody:
    if null:
        ev = NullValue()
    else:
        ev = LiteralValue(value=value if value is not None else "Tech")
    return ValueClaimBody(
        subject=subject or _ib(),
        expected_value=ev,
    )


def _vh(body, cond=None) -> str:
    return _hash(
        "data_behavior", "value-claim", body, cond or _no_conditions(),
    )


class TestValueClaimSensitivity:
    def test_different_entity_id_changes_hash(self) -> None:
        b1 = _value_claim(subject=_ib(entity_id=EID_INDUSTRY))
        b2 = _value_claim(subject=_ib(entity_id=EID_OTHER))
        assert _vh(b1) != _vh(b2)

    def test_different_entity_type_changes_hash(self) -> None:
        b1 = _value_claim(subject=_ib(entity_type="Field"))
        b2 = _value_claim(subject=_ib(entity_type="Object"))
        assert _vh(b1) != _vh(b2)

    def test_different_expected_value_kind_changes_hash(self) -> None:
        b1 = _value_claim(value="Tech")
        b2 = _value_claim(null=True)
        assert _vh(b1) != _vh(b2)

    def test_different_expected_value_payload_changes_hash(self) -> None:
        b1 = _value_claim(value="Tech")
        b2 = _value_claim(value="Finance")
        assert _vh(b1) != _vh(b2)

    def test_adding_condition_changes_hash(self) -> None:
        b = _value_claim()
        c1 = _no_conditions()
        # Condition predicates: equals / not_equals / in_set /
        # matches_pattern / is_null / is_not_null (recipe-side
        # AssertionPredicate has a different set).
        c2 = SemanticConditionsBody(conditions=[
            Condition(subject=_ib(), predicate="is_not_null"),
        ])
        assert _vh(b, c1) != _vh(b, c2)

    def test_changing_condition_predicate_changes_hash(self) -> None:
        b = _value_claim()
        c1 = SemanticConditionsBody(conditions=[
            Condition(subject=_ib(), predicate="is_null"),
        ])
        c2 = SemanticConditionsBody(conditions=[
            Condition(subject=_ib(), predicate="is_not_null"),
        ])
        assert _vh(b, c1) != _vh(b, c2)


class TestValueClaimDriftPreservation:
    def test_version_seq_does_not_change_hash(self) -> None:
        """C0 invariant: bumping version_seq on the subject
        IdentityBearingRef must NOT change the hash. This is the
        S8 autonomous-evolution guarantee."""
        b1 = _value_claim(subject=_ib(version_seq=1))
        b2 = _value_claim(subject=_ib(version_seq=99))
        assert _vh(b1) == _vh(b2)

    def test_external_id_rename_does_not_change_hash(self) -> None:
        """C1 invariant: renaming the entity's external_id (with
        entity_id unchanged) preserves the hash."""
        b1 = _value_claim(subject=_ib(external_id="Account.Industry"))
        b2 = _value_claim(
            subject=_ib(external_id="Account.NewIndustry__c"),
        )
        assert _vh(b1) == _vh(b2)


# ---------------------------------------------------------------------------
# StateTransitionClaimBody (C1-B is the critical test)
# ---------------------------------------------------------------------------


def _state_transition(
    object_ref: IdentityBearingRef = None,
    stage_field: IdentityBearingRef = None,
    from_stage: str = "Prospecting",
    to_stage: str = "Closed Won",
    trigger_kind: str = "ui-trigger",
    extra_fields: list = None,
    extra_from_state: dict = None,
    extra_to_state: dict = None,
) -> StateTransitionClaimBody:
    object_ref = object_ref or _ib(
        entity_type="Object", entity_id=EID_OPP,
        external_id="Opportunity",
    )
    stage_field = stage_field or _ib(
        entity_type="Field", entity_id=EID_STAGE,
        external_id="Opportunity.StageName",
    )
    extra_fields = extra_fields or []
    from_state = {stage_field.external_id: LiteralValue(value=from_stage)}
    to_state = {stage_field.external_id: LiteralValue(value=to_stage)}
    if extra_from_state:
        from_state.update(extra_from_state)
    if extra_to_state:
        to_state.update(extra_to_state)
    return StateTransitionClaimBody(
        subject=object_ref,
        subject_fields=[stage_field, *extra_fields],
        from_state=StateDescriptor(field_values=from_state),
        to_state=StateDescriptor(field_values=to_state),
        triggering_event=EventDescriptor(
            trigger_kind=trigger_kind, description="x",
        ),
    )


def _sh(body) -> str:
    return _hash(
        "data_behavior", "state-transition-claim", body,
        _no_conditions(),
    )


class TestStateTransitionSensitivity:
    def test_different_subject_entity_id_changes_hash(self) -> None:
        b1 = _state_transition()
        b2 = _state_transition(
            object_ref=_ib(
                entity_type="Object", entity_id=EID_OTHER,
                external_id="Opportunity",
            ),
        )
        assert _sh(b1) != _sh(b2)

    def test_different_field_entity_id_changes_hash(self) -> None:
        b1 = _state_transition()
        b2 = _state_transition(
            stage_field=_ib(
                entity_type="Field", entity_id=EID_OTHER,
                external_id="Opportunity.StageName",
            ),
        )
        assert _sh(b1) != _sh(b2)

    def test_different_from_state_value_changes_hash(self) -> None:
        b1 = _state_transition(from_stage="Prospecting")
        b2 = _state_transition(from_stage="Qualification")
        assert _sh(b1) != _sh(b2)

    def test_different_to_state_value_changes_hash(self) -> None:
        b1 = _state_transition(to_stage="Closed Won")
        b2 = _state_transition(to_stage="Closed Lost")
        assert _sh(b1) != _sh(b2)

    def test_different_trigger_kind_changes_hash(self) -> None:
        b1 = _state_transition(trigger_kind="ui-trigger")
        b2 = _state_transition(trigger_kind="data-mutation-trigger")
        assert _sh(b1) != _sh(b2)

    def test_adding_field_to_state_changes_hash(self) -> None:
        close_field = _ib(
            entity_type="Field", entity_id=EID_CLOSE,
            external_id="Opportunity.CloseDate",
        )
        b1 = _state_transition()
        b2 = _state_transition(
            extra_fields=[close_field],
            extra_from_state={"Opportunity.CloseDate": NullValue()},
            extra_to_state={
                "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
            },
        )
        assert _sh(b1) != _sh(b2)


class TestStateTransitionDriftPreservation:
    def test_external_id_rename_preserves_hash(self) -> None:
        """The critical C1-B invariant: same entity_ids on
        subject + subject_fields, different external_id naming
        throughout (including in the from_state / to_state dict
        keys) → SAME hash. This is what the custom canonicalizer
        guarantees."""
        b1 = _state_transition()
        # Rename everything:
        b2 = _state_transition(
            object_ref=IdentityBearingRef(
                entity_type="Object", entity_id=EID_OPP,
                version_seq=42, external_id="Opportunity_V2",
            ),
            stage_field=IdentityBearingRef(
                entity_type="Field", entity_id=EID_STAGE,
                version_seq=42,
                external_id="Opportunity_V2.NewStageName__c",
            ),
            from_stage="Prospecting",
            to_stage="Closed Won",
        )
        assert _sh(b1) == _sh(b2)

    def test_version_seq_does_not_change_hash(self) -> None:
        b1 = _state_transition()
        b2 = _state_transition(
            object_ref=_ib(
                entity_type="Object", entity_id=EID_OPP,
                version_seq=99, external_id="Opportunity",
            ),
        )
        assert _sh(b1) == _sh(b2)

    def test_subject_fields_reorder_preserves_hash(self) -> None:
        """subject_fields is a SET — reordering must preserve the
        hash."""
        close_field = _ib(
            entity_type="Field", entity_id=EID_CLOSE,
            external_id="Opportunity.CloseDate",
        )
        b1 = StateTransitionClaimBody(
            subject=_ib(
                entity_type="Object", entity_id=EID_OPP,
                external_id="Opportunity",
            ),
            subject_fields=[
                _ib(entity_type="Field", entity_id=EID_STAGE,
                    external_id="Opportunity.StageName"),
                close_field,
            ],
            from_state=StateDescriptor(field_values={
                "Opportunity.StageName": LiteralValue(value="P"),
                "Opportunity.CloseDate": NullValue(),
            }),
            to_state=StateDescriptor(field_values={
                "Opportunity.StageName": LiteralValue(value="W"),
                "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
            }),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
        )
        b2 = StateTransitionClaimBody(
            subject=_ib(
                entity_type="Object", entity_id=EID_OPP,
                external_id="Opportunity",
            ),
            subject_fields=[
                close_field,  # reordered
                _ib(entity_type="Field", entity_id=EID_STAGE,
                    external_id="Opportunity.StageName"),
            ],
            from_state=StateDescriptor(field_values={
                "Opportunity.StageName": LiteralValue(value="P"),
                "Opportunity.CloseDate": NullValue(),
            }),
            to_state=StateDescriptor(field_values={
                "Opportunity.StageName": LiteralValue(value="W"),
                "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
            }),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
        )
        assert _sh(b1) == _sh(b2)

    def test_state_dict_key_reorder_preserves_hash(self) -> None:
        """Inserting field_values in different orders → same
        canonical form (the custom canonicalizer sorts by
        entity_id)."""
        close_field = _ib(
            entity_type="Field", entity_id=EID_CLOSE,
            external_id="Opportunity.CloseDate",
        )
        stage_field = _ib(
            entity_type="Field", entity_id=EID_STAGE,
            external_id="Opportunity.StageName",
        )
        b1 = StateTransitionClaimBody(
            subject=_ib(
                entity_type="Object", entity_id=EID_OPP,
                external_id="Opportunity",
            ),
            subject_fields=[stage_field, close_field],
            from_state=StateDescriptor(field_values={
                "Opportunity.StageName": LiteralValue(value="P"),
                "Opportunity.CloseDate": NullValue(),
            }),
            to_state=StateDescriptor(field_values={
                "Opportunity.StageName": LiteralValue(value="W"),
                "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
            }),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
        )
        b2 = StateTransitionClaimBody(
            subject=_ib(
                entity_type="Object", entity_id=EID_OPP,
                external_id="Opportunity",
            ),
            subject_fields=[stage_field, close_field],
            from_state=StateDescriptor(field_values={
                # Reverse insertion order:
                "Opportunity.CloseDate": NullValue(),
                "Opportunity.StageName": LiteralValue(value="P"),
            }),
            to_state=StateDescriptor(field_values={
                "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
                "Opportunity.StageName": LiteralValue(value="W"),
            }),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
        )
        assert _sh(b1) == _sh(b2)


# ---------------------------------------------------------------------------
# AutomationEffectClaimBody
# ---------------------------------------------------------------------------


def _automation_effect(
    automation: IdentityBearingRef = None,
    primitive: str = "flow",
    trigger_kind: str = "data-mutation-trigger",
    effect=None,
    affected: list = None,
) -> AutomationEffectClaimBody:
    automation = automation or _ib(
        entity_type="Flow", entity_id=EID_FLOW,
        external_id="StampCloseDate",
    )
    affected = affected or []
    return AutomationEffectClaimBody(
        automation=automation,
        automation_primitive=primitive,
        triggering_action=EventDescriptor(
            trigger_kind=trigger_kind, description="x",
        ),
        expected_effect=effect or SideEffect(description="email"),
        affected_fields=affected,
    )


def _ah(body) -> str:
    return _hash(
        "data_behavior", "automation-effect-claim", body,
        _no_conditions(),
    )


class TestAutomationEffectSensitivity:
    def test_different_automation_entity_id_changes_hash(self) -> None:
        b1 = _automation_effect()
        b2 = _automation_effect(
            automation=_ib(
                entity_type="Flow", entity_id=EID_OTHER,
                external_id="StampCloseDate",
            ),
        )
        assert _ah(b1) != _ah(b2)

    def test_different_primitive_changes_hash(self) -> None:
        b1 = _automation_effect(primitive="flow")
        b2 = _automation_effect(primitive="validation_rule")
        assert _ah(b1) != _ah(b2)

    def test_different_effect_kind_changes_hash(self) -> None:
        b1 = _automation_effect(effect=SideEffect(description="x"))
        b2 = _automation_effect(
            effect=BlockedOperationEffect(reason="x"),
        )
        assert _ah(b1) != _ah(b2)

    def test_different_field_change_value_changes_hash(self) -> None:
        affected = _ib(
            entity_type="Field", entity_id=EID_CLOSE,
            external_id="Opportunity.CloseDate",
        )
        b1 = _automation_effect(
            effect=FieldChangeEffect(
                changes=StateDescriptor(field_values={
                    "Opportunity.CloseDate": LiteralValue(
                        value="2026-12-31",
                    ),
                }),
            ),
            affected=[affected],
        )
        b2 = _automation_effect(
            effect=FieldChangeEffect(
                changes=StateDescriptor(field_values={
                    "Opportunity.CloseDate": LiteralValue(
                        value="2027-01-01",
                    ),
                }),
            ),
            affected=[affected],
        )
        assert _ah(b1) != _ah(b2)


class TestAutomationEffectDriftPreservation:
    def test_automation_external_id_rename_preserves_hash(self) -> None:
        b1 = _automation_effect()
        b2 = _automation_effect(
            automation=_ib(
                entity_type="Flow", entity_id=EID_FLOW,
                version_seq=99, external_id="StampCloseDate_V2",
            ),
        )
        assert _ah(b1) == _ah(b2)

    def test_field_change_external_id_rename_preserves_hash(self) -> None:
        """The C1-B parallel for automation-effect: same
        affected_fields entity_id, different external_id naming
        (including the field_values dict key) → same hash."""
        b1 = _automation_effect(
            effect=FieldChangeEffect(
                changes=StateDescriptor(field_values={
                    "Opportunity.CloseDate": LiteralValue(
                        value="2026-12-31",
                    ),
                }),
            ),
            affected=[
                _ib(entity_type="Field", entity_id=EID_CLOSE,
                    external_id="Opportunity.CloseDate"),
            ],
        )
        b2 = _automation_effect(
            effect=FieldChangeEffect(
                changes=StateDescriptor(field_values={
                    "Opportunity.NewCloseDate__c": LiteralValue(
                        value="2026-12-31",
                    ),
                }),
            ),
            affected=[
                _ib(entity_type="Field", entity_id=EID_CLOSE,
                    version_seq=42,
                    external_id="Opportunity.NewCloseDate__c"),
            ],
        )
        assert _ah(b1) == _ah(b2)

    def test_affected_fields_reorder_preserves_hash(self) -> None:
        f1 = _ib(
            entity_type="Field", entity_id=EID_CLOSE,
            external_id="Opportunity.CloseDate",
        )
        f2 = _ib(
            entity_type="Field", entity_id=EID_STAGE,
            external_id="Opportunity.StageName",
        )
        b1 = _automation_effect(affected=[f1, f2])
        b2 = _automation_effect(affected=[f2, f1])
        assert _ah(b1) == _ah(b2)


# ---------------------------------------------------------------------------
# ProhibitionClaimBody
# ---------------------------------------------------------------------------


def _prohibition(
    target: IdentityBearingRef = None,
    operation: str = "delete",
    mechanism: str = "validation_rule",
    rejection: RejectionSignal = None,
) -> ProhibitionClaimBody:
    target = target or _ib(
        entity_type="Object", entity_id=EID_OPP,
        external_id="Opportunity",
    )
    rejection = rejection or RejectionSignal(error_code="X")
    return ProhibitionClaimBody(
        target=target,
        operation=operation,
        prohibition_mechanism=mechanism,
        expected_rejection=rejection,
    )


def _ph(body) -> str:
    return _hash(
        "data_behavior", "prohibition-claim", body, _no_conditions(),
    )


class TestProhibitionSensitivity:
    def test_different_target_entity_id_changes_hash(self) -> None:
        b1 = _prohibition()
        b2 = _prohibition(
            target=_ib(
                entity_type="Object", entity_id=EID_OTHER,
                external_id="Opportunity",
            ),
        )
        assert _ph(b1) != _ph(b2)

    def test_different_operation_changes_hash(self) -> None:
        b1 = _prohibition(operation="delete")
        b2 = _prohibition(operation="modify_field")
        assert _ph(b1) != _ph(b2)

    def test_different_mechanism_changes_hash(self) -> None:
        b1 = _prohibition(mechanism="validation_rule")
        b2 = _prohibition(mechanism="apex_trigger")
        assert _ph(b1) != _ph(b2)

    def test_different_rejection_signal_changes_hash(self) -> None:
        b1 = _prohibition(
            rejection=RejectionSignal(error_code="A"),
        )
        b2 = _prohibition(
            rejection=RejectionSignal(error_code="B"),
        )
        assert _ph(b1) != _ph(b2)

    def test_adding_error_field_changes_hash(self) -> None:
        b1 = _prohibition(rejection=RejectionSignal(error_code="X"))
        b2 = _prohibition(rejection=RejectionSignal(
            error_code="X",
            error_field=_ib(
                entity_type="Field", entity_id=EID_INDUSTRY,
                external_id="Account.Industry",
            ),
        ))
        assert _ph(b1) != _ph(b2)


class TestProhibitionDriftPreservation:
    def test_target_external_id_rename_preserves_hash(self) -> None:
        b1 = _prohibition()
        b2 = _prohibition(
            target=_ib(
                entity_type="Object", entity_id=EID_OPP,
                version_seq=99, external_id="Opportunity_V2",
            ),
        )
        assert _ph(b1) == _ph(b2)

    def test_error_field_external_id_rename_preserves_hash(self) -> None:
        b1 = _prohibition(rejection=RejectionSignal(
            error_code="X",
            error_field=_ib(
                entity_type="Field", entity_id=EID_INDUSTRY,
                external_id="Account.Industry",
            ),
        ))
        b2 = _prohibition(rejection=RejectionSignal(
            error_code="X",
            error_field=_ib(
                entity_type="Field", entity_id=EID_INDUSTRY,
                version_seq=42,
                external_id="Account.RenamedIndustry__c",
            ),
        ))
        assert _ph(b1) == _ph(b2)


# ---------------------------------------------------------------------------
# SemanticConditionsBody (via ValueClaimBody embedding)
# ---------------------------------------------------------------------------


class TestSemanticConditionsSensitivity:
    def test_adding_condition_changes_hash(self) -> None:
        body = _value_claim()
        c1 = _no_conditions()
        c2 = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib(), predicate="equals", value="Tech",
            ),
        ])
        assert _vh(body, c1) != _vh(body, c2)

    def test_different_condition_value_changes_hash(self) -> None:
        body = _value_claim()
        c1 = SemanticConditionsBody(conditions=[
            Condition(subject=_ib(), predicate="equals", value="A"),
        ])
        c2 = SemanticConditionsBody(conditions=[
            Condition(subject=_ib(), predicate="equals", value="B"),
        ])
        assert _vh(body, c1) != _vh(body, c2)

    def test_different_condition_subject_changes_hash(self) -> None:
        body = _value_claim()
        c1 = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib(entity_id=EID_INDUSTRY),
                predicate="is_not_null",
            ),
        ])
        c2 = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib(entity_id=EID_OTHER),
                predicate="is_not_null",
            ),
        ])
        assert _vh(body, c1) != _vh(body, c2)


class TestSemanticConditionsDriftPreservation:
    def test_conditions_reorder_preserves_hash(self) -> None:
        """conditions list is SET — reordering must preserve the
        hash."""
        body = _value_claim()
        c_a = Condition(
            subject=_ib(entity_id=EID_INDUSTRY),
            predicate="equals", value="Tech",
        )
        c_b = Condition(
            subject=_ib(entity_id=EID_OTHER),
            predicate="is_not_null",
        )
        c1 = SemanticConditionsBody(conditions=[c_a, c_b])
        c2 = SemanticConditionsBody(conditions=[c_b, c_a])
        assert _vh(body, c1) == _vh(body, c2)

    def test_condition_subject_external_id_rename_preserves_hash(
        self,
    ) -> None:
        body = _value_claim()
        c1 = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib(
                    entity_id=EID_INDUSTRY,
                    external_id="Account.Industry",
                ),
                predicate="equals", value="Tech",
            ),
        ])
        c2 = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib(
                    entity_id=EID_INDUSTRY,
                    version_seq=99,
                    external_id="Account.RenamedIndustry__c",
                ),
                predicate="equals", value="Tech",
            ),
        ])
        assert _vh(body, c1) == _vh(body, c2)

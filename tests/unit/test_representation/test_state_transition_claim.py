"""Tests for :class:`StateTransitionClaimBody`.

Per SPEC §3 + D-053 + D-055 (trigger taxonomy) + D-058 §5.4
(coverage extraction). Covers:

  - Construction with from/to StateDescriptors + EventDescriptor.
  - ``subject`` + ``subject_fields`` constructed as
    :class:`IdentityBearingRef` instances.
  - Logical refs rejected at any identity-bearing slot.
  - Empty ``subject_fields`` permitted (atypical but legal).
  - Registry registration on import.
  - Full round-trip preserves nested type discipline.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from primeqa.test_representation.models.claims.data_behavior.state_transition_claim import (
    StateTransitionClaimBody,
)
from primeqa.test_representation.models.primitives import (
    EventDescriptor,
    LiteralValue,
    NullValue,
    StateDescriptor,
)
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)


def _ib_ref(
    name: str = "Account.Industry", entity_type: str = "Field",
) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type=entity_type,
        entity_id=uuid4(),
        version_seq=1,
        external_id=name,
    )


def _build_body() -> StateTransitionClaimBody:
    """Canonical stage-transition fixture: Opportunity moves from
    Prospecting to Closed Won."""
    return StateTransitionClaimBody(
        subject=_ib_ref("Opportunity", entity_type="Object"),
        subject_fields=[
            _ib_ref("Opportunity.StageName"),
            _ib_ref("Opportunity.CloseDate"),
        ],
        from_state=StateDescriptor(field_values={
            "Opportunity.StageName": LiteralValue(value="Prospecting"),
            "Opportunity.CloseDate": NullValue(),
        }),
        to_state=StateDescriptor(field_values={
            "Opportunity.StageName": LiteralValue(value="Closed Won"),
            "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
        }),
        triggering_event=EventDescriptor(
            trigger_kind="ui-trigger",
            description="user clicks Close Opportunity action",
        ),
    )


class TestStateTransitionConstruction:
    def test_canonical_fixture(self) -> None:
        body = _build_body()
        assert body.kind == "state-transition-claim"
        assert body.body_schema_version == 1
        assert body.subject.entity_type == "Object"
        assert len(body.subject_fields) == 2
        assert body.from_state.field_values["Opportunity.StageName"].value == "Prospecting"  # type: ignore[union-attr]
        assert body.triggering_event.trigger_kind == "ui-trigger"

    def test_empty_subject_fields_permitted(self) -> None:
        """Atypical but legal — some transitions are observed via
        record-level state rather than field-value bindings."""
        body = StateTransitionClaimBody(
            subject=_ib_ref("Opportunity", entity_type="Object"),
            subject_fields=[],
            from_state=StateDescriptor(field_values={}),
            to_state=StateDescriptor(field_values={}),
            triggering_event=EventDescriptor(
                trigger_kind="time-trigger",
                description="scheduled close",
            ),
        )
        assert body.subject_fields == []

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            StateTransitionClaimBody(
                subject=_ib_ref(entity_type="Object"),
                subject_fields=[],
                from_state=StateDescriptor(field_values={}),
                to_state=StateDescriptor(field_values={}),
                triggering_event=EventDescriptor(
                    trigger_kind="ui-trigger", description="x",
                ),
                kind="value-claim",  # type: ignore[arg-type]
            )

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            StateTransitionClaimBody(  # type: ignore[call-arg]
                subject=_ib_ref(entity_type="Object"),
                subject_fields=[],
                from_state=StateDescriptor(field_values={}),
                to_state=StateDescriptor(field_values={}),
                triggering_event=EventDescriptor(
                    trigger_kind="ui-trigger", description="x",
                ),
                surprise=True,
            )


class TestStateTransitionRefDiscipline:
    def test_subject_constructed_as_identity_bearing(self) -> None:
        body = _build_body()
        # Round-trip and re-validate to exercise the type-construction path.
        roundtrip = StateTransitionClaimBody.model_validate(body.model_dump())
        assert type(roundtrip.subject) is IdentityBearingRef

    def test_subject_fields_each_identity_bearing(self) -> None:
        body = _build_body()
        roundtrip = StateTransitionClaimBody.model_validate(body.model_dump())
        for ref in roundtrip.subject_fields:
            assert type(ref) is IdentityBearingRef

    def test_logical_ref_rejected_for_subject(self) -> None:
        with pytest.raises(ValidationError):
            StateTransitionClaimBody.model_validate({
                "subject": {
                    "ref_kind": "logical",
                    "entity_type": "Object",
                    "external_id": "Opportunity",
                },
                "subject_fields": [],
                "from_state": {"field_values": {}},
                "to_state": {"field_values": {}},
                "triggering_event": {
                    "trigger_kind": "ui-trigger",
                    "description": "x",
                },
            })

    def test_logical_ref_rejected_inside_subject_fields(self) -> None:
        with pytest.raises(ValidationError):
            StateTransitionClaimBody.model_validate({
                "subject": {
                    "ref_kind": "pinned",
                    "entity_type": "Object",
                    "entity_id": str(uuid4()),
                    "version_seq": 1,
                    "external_id": "Opportunity",
                },
                "subject_fields": [{
                    "ref_kind": "logical",
                    "entity_type": "Field",
                    "external_id": "Opportunity.StageName",
                }],
                "from_state": {"field_values": {}},
                "to_state": {"field_values": {}},
                "triggering_event": {
                    "trigger_kind": "ui-trigger",
                    "description": "x",
                },
            })


class TestStateTransitionSerialization:
    def test_universal_body_keys_present(self) -> None:
        body = _build_body()
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "state-transition-claim"

    def test_full_round_trip(self) -> None:
        body = _build_body()
        roundtrip = StateTransitionClaimBody.model_validate(body.model_dump())
        assert roundtrip == body
        # Nested type discipline:
        assert type(roundtrip.subject) is IdentityBearingRef
        assert type(roundtrip.from_state) is StateDescriptor
        assert type(roundtrip.triggering_event) is EventDescriptor
        # Discriminated union nested in StateDescriptor.field_values
        # survives:
        assert type(
            roundtrip.from_state.field_values["Opportunity.CloseDate"],
        ) is NullValue


class TestStateTransitionRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("state-transition-claim", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model(
            "state-transition-claim", 1,
        ) is StateTransitionClaimBody


class TestStateTransitionArraySemanticsMarker:
    def test_subject_fields_marked_set(self) -> None:
        """Per Track C: subject_fields is a set of field
        references (entity_id-based); order is incidental.
        :class:`ArraySemantics.SET` is required for the
        canonicalizer to sort the list before hashing."""
        from primeqa.test_representation.models.common import ArraySemantics

        metadata = StateTransitionClaimBody.model_fields[
            "subject_fields"
        ].metadata
        assert ArraySemantics.SET in metadata

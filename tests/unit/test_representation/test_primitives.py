"""Tests for substrate-2 claim-body primitives.

Per SPEC §3 + §4.7.2 + D-058 §5.4. Covers:

  - :class:`LiteralValue` / :class:`NullValue` construction +
    :data:`ExpectedValue` discriminated-union dispatch.
  - :class:`StateDescriptor` accepts empty + non-empty dicts of
    field external_id → ExpectedValue.
  - :class:`EventDescriptor` accepts all 5 trigger-kinds locked
    by D-055 and rejects everything else.
  - :class:`EffectDescriptor` discriminator dispatches between
    field_change / blocked_operation / side_effect.
  - :class:`RejectionSignal` enforces "at least one of error_code,
    error_message_pattern, error_field is set".
  - :class:`RejectionSignal.error_field` is constructed as an
    :class:`IdentityBearingRef` instance when Pydantic validates a
    pinned-shaped dict (the B-α empirical pattern, verified here
    on a nested field).
  - All primitives are frozen post-construction (D-061 §7.2).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.primitives import (
    BlockedOperationEffect,
    EffectDescriptor,
    EventDescriptor,
    ExpectedValue,
    FieldChangeEffect,
    LiteralValue,
    NullValue,
    RejectionSignal,
    SideEffect,
    StateDescriptor,
)
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    PinnedRef,
)


def _ib_ref() -> IdentityBearingRef:
    """Build a fresh IdentityBearingRef for tests that need one."""
    return IdentityBearingRef(
        entity_type="Field",
        entity_id=uuid4(),
        version_seq=1,
        external_id="Account.Name",
    )


# ---------------------------------------------------------------------------
# ExpectedValue
# ---------------------------------------------------------------------------

class TestExpectedValue:
    @pytest.fixture
    def adapter(self) -> TypeAdapter:
        return TypeAdapter(ExpectedValue)

    def test_literal_value_constructs(self) -> None:
        lv = LiteralValue(value="Prospecting")
        assert lv.kind == "literal"
        assert lv.value == "Prospecting"

    def test_literal_value_accepts_any_scalar(self) -> None:
        # ``Any``-typed so SF type constraints aren't enforced here.
        assert LiteralValue(value=42).value == 42
        assert LiteralValue(value=3.14).value == 3.14
        assert LiteralValue(value=True).value is True
        assert LiteralValue(value="x").value == "x"
        assert LiteralValue(value=["a", "b"]).value == ["a", "b"]

    def test_null_value_constructs(self) -> None:
        nv = NullValue()
        assert nv.kind == "null"

    def test_null_value_has_no_value_field(self) -> None:
        """NullValue is intentionally distinct from
        ``LiteralValue(value=None)``."""
        nv = NullValue()
        dumped = nv.model_dump()
        assert dumped == {"kind": "null"}
        assert "value" not in dumped

    def test_union_dispatches_literal(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({"kind": "literal", "value": "x"})
        assert type(result) is LiteralValue
        assert result.value == "x"

    def test_union_dispatches_null(self, adapter: TypeAdapter) -> None:
        result = adapter.validate_python({"kind": "null"})
        assert type(result) is NullValue

    def test_union_rejects_unknown_kind(self, adapter: TypeAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({"kind": "computed", "value": "x"})

    def test_union_rejects_missing_discriminator(
        self, adapter: TypeAdapter,
    ) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({"value": "x"})

    def test_literal_value_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            LiteralValue(  # type: ignore[call-arg]
                value="x", surprise=True,
            )

    def test_literal_value_is_frozen(self) -> None:
        lv = LiteralValue(value="x")
        with pytest.raises(ValidationError):
            lv.value = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# StateDescriptor
# ---------------------------------------------------------------------------

class TestStateDescriptor:
    def test_empty_state_is_valid(self) -> None:
        sd = StateDescriptor(field_values={})
        assert sd.field_values == {}

    def test_state_with_literal_values(self) -> None:
        sd = StateDescriptor(field_values={
            "Account.Industry": LiteralValue(value="Tech"),
            "Account.AnnualRevenue": LiteralValue(value=1_000_000),
        })
        assert len(sd.field_values) == 2
        assert sd.field_values["Account.Industry"].value == "Tech"  # type: ignore[union-attr]

    def test_state_with_mixed_value_kinds(self) -> None:
        sd = StateDescriptor(field_values={
            "Account.Industry": LiteralValue(value="Tech"),
            "Account.ParentId": NullValue(),
        })
        assert isinstance(sd.field_values["Account.ParentId"], NullValue)

    def test_state_round_trip(self) -> None:
        sd = StateDescriptor(field_values={
            "Account.Industry": LiteralValue(value="Tech"),
            "Account.ParentId": NullValue(),
        })
        roundtrip = StateDescriptor.model_validate(sd.model_dump())
        assert roundtrip == sd
        # Confirm discriminated-union dispatch survived the trip.
        assert type(roundtrip.field_values["Account.Industry"]) is LiteralValue
        assert type(roundtrip.field_values["Account.ParentId"]) is NullValue

    def test_state_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            StateDescriptor(  # type: ignore[call-arg]
                field_values={}, extra="x",
            )

    def test_state_is_frozen(self) -> None:
        sd = StateDescriptor(field_values={})
        with pytest.raises(ValidationError):
            sd.field_values = {"x": LiteralValue(value=1)}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EventDescriptor
# ---------------------------------------------------------------------------

class TestEventDescriptor:
    @pytest.mark.parametrize("trigger_kind", [
        "inbound-trigger",
        "data-mutation-trigger",
        "ui-trigger",
        "time-trigger",
        "configuration-trigger",
    ])
    def test_all_five_trigger_kinds_accepted(
        self, trigger_kind: str,
    ) -> None:
        ev = EventDescriptor(
            trigger_kind=trigger_kind,  # type: ignore[arg-type]
            description="x",
        )
        assert ev.trigger_kind == trigger_kind

    def test_unknown_trigger_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EventDescriptor(
                trigger_kind="batch-trigger",  # type: ignore[arg-type]
                description="x",
            )

    def test_description_required(self) -> None:
        with pytest.raises(ValidationError):
            EventDescriptor(  # type: ignore[call-arg]
                trigger_kind="ui-trigger",
            )

    def test_event_is_frozen(self) -> None:
        ev = EventDescriptor(
            trigger_kind="ui-trigger", description="x",
        )
        with pytest.raises(ValidationError):
            ev.description = "y"  # type: ignore[misc]

    def test_event_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            EventDescriptor(  # type: ignore[call-arg]
                trigger_kind="ui-trigger",
                description="x",
                surprise=True,
            )


# ---------------------------------------------------------------------------
# EffectDescriptor
# ---------------------------------------------------------------------------

class TestEffectDescriptor:
    @pytest.fixture
    def adapter(self) -> TypeAdapter:
        return TypeAdapter(EffectDescriptor)

    def test_field_change_effect(self) -> None:
        ef = FieldChangeEffect(changes=StateDescriptor(field_values={
            "Account.Industry": LiteralValue(value="Tech"),
        }))
        assert ef.kind == "field_change"
        assert isinstance(ef.changes, StateDescriptor)

    def test_blocked_operation_with_reason(self) -> None:
        ef = BlockedOperationEffect(reason="Validation: Industry required")
        assert ef.kind == "blocked_operation"
        assert ef.reason == "Validation: Industry required"

    def test_blocked_operation_without_reason(self) -> None:
        ef = BlockedOperationEffect()
        assert ef.reason is None

    def test_side_effect_with_description(self) -> None:
        ef = SideEffect(description="email sent to owner")
        assert ef.kind == "side_effect"

    def test_side_effect_requires_description(self) -> None:
        with pytest.raises(ValidationError):
            SideEffect()  # type: ignore[call-arg]

    def test_union_dispatches_field_change(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "kind": "field_change",
            "changes": {"field_values": {
                "Account.Industry": {"kind": "literal", "value": "Tech"},
            }},
        })
        assert type(result) is FieldChangeEffect

    def test_union_dispatches_blocked(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "kind": "blocked_operation",
            "reason": "x",
        })
        assert type(result) is BlockedOperationEffect

    def test_union_dispatches_side(
        self, adapter: TypeAdapter,
    ) -> None:
        result = adapter.validate_python({
            "kind": "side_effect",
            "description": "x",
        })
        assert type(result) is SideEffect

    def test_union_rejects_unknown_kind(
        self, adapter: TypeAdapter,
    ) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({"kind": "noop"})

    def test_field_change_round_trip(
        self, adapter: TypeAdapter,
    ) -> None:
        ef = FieldChangeEffect(changes=StateDescriptor(field_values={
            "Account.Industry": LiteralValue(value="Tech"),
            "Account.ParentId": NullValue(),
        }))
        roundtrip = adapter.validate_python(ef.model_dump())
        assert type(roundtrip) is FieldChangeEffect
        assert roundtrip == ef

    def test_effects_are_frozen(self) -> None:
        ef = SideEffect(description="x")
        with pytest.raises(ValidationError):
            ef.description = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RejectionSignal
# ---------------------------------------------------------------------------

class TestRejectionSignal:
    def test_with_error_code_only(self) -> None:
        rs = RejectionSignal(error_code="DUPLICATE_VALUE")
        assert rs.error_code == "DUPLICATE_VALUE"
        assert rs.error_message_pattern is None
        assert rs.error_field is None

    def test_with_pattern_only(self) -> None:
        rs = RejectionSignal(error_message_pattern="Industry is required")
        assert rs.error_message_pattern == "Industry is required"

    def test_with_field_only(self) -> None:
        ref = _ib_ref()
        rs = RejectionSignal(error_field=ref)
        assert rs.error_field is ref

    def test_with_all_three(self) -> None:
        rs = RejectionSignal(
            error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION",
            error_message_pattern="required",
            error_field=_ib_ref(),
        )
        assert rs.error_code == "FIELD_CUSTOM_VALIDATION_EXCEPTION"
        assert rs.error_message_pattern == "required"
        assert isinstance(rs.error_field, IdentityBearingRef)

    def test_empty_signal_rejected(self) -> None:
        """An empty signal can't be verified by a recipe — the
        validator must reject it."""
        with pytest.raises(ValidationError) as exc_info:
            RejectionSignal()
        # Surface the "at least one" diagnostic.
        assert "at least one" in str(exc_info.value).lower()

    def test_error_field_is_constructed_as_identity_bearing(self) -> None:
        """The critical B-α empirical pattern: when a field's
        declared type is :class:`IdentityBearingRef`, Pydantic
        constructs an IdentityBearingRef instance during validation
        (not a plain PinnedRef). Verified here on a nested field."""
        rs = RejectionSignal.model_validate({
            "error_field": {
                "ref_kind": "pinned",
                "entity_type": "Field",
                "entity_id": str(uuid4()),
                "version_seq": 1,
                "external_id": "Account.Industry",
            },
        })
        assert type(rs.error_field) is IdentityBearingRef
        # And isinstance(... PinnedRef) still holds — the subclass
        # relationship is preserved.
        assert isinstance(rs.error_field, PinnedRef)

    def test_error_field_rejects_logical_ref_shape(self) -> None:
        """Pinned slots reject logical-shaped payloads. The
        Coordinator path also rejects this; the Pydantic layer is
        defence-in-depth."""
        with pytest.raises(ValidationError):
            RejectionSignal.model_validate({
                "error_field": {
                    "ref_kind": "logical",
                    "entity_type": "Field",
                    "external_id": "Account.Industry",
                },
            })

    def test_signal_is_frozen(self) -> None:
        rs = RejectionSignal(error_code="X")
        with pytest.raises(ValidationError):
            rs.error_code = "Y"  # type: ignore[misc]

    def test_signal_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            RejectionSignal(  # type: ignore[call-arg]
                error_code="X",
                surprise=True,
            )

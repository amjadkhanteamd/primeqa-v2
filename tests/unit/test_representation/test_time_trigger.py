"""Tests for :class:`TimeTriggerBody` + :class:`TimePredicate`.

Per SPEC §3 + D-055. Covers:

  - All 5 mechanism + 3 predicate_kind values accepted.
  - TimePredicate cross-field validator: exactly one context
    field set, matching predicate_kind.
  - scheduled_target optional; OperationalRef behaviour preserved.
  - Registry registration.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
    PinnedRef,
)
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)
from primeqa.test_representation.models.triggers.time import (
    TimePredicate,
    TimeTriggerBody,
)


def _logical_flow() -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": "Flow",
        "external_id": "DailyHealthCheck",
    }


def _pinned_apex() -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": "ApexClass",
        "entity_id": str(uuid4()),
        "version_seq": 4,
        "external_id": "AccountCleanupBatch",
    }


# ---------------------------------------------------------------------------
# TimePredicate
# ---------------------------------------------------------------------------

class TestTimePredicateConstruction:
    def test_after_duration(self) -> None:
        tp = TimePredicate(
            predicate_kind="after_duration",
            duration_seconds=86400,
        )
        assert tp.duration_seconds == 86400
        assert tp.specific_time_iso is None

    def test_at_specific_time(self) -> None:
        tp = TimePredicate(
            predicate_kind="at_specific_time",
            specific_time_iso="2026-12-31T23:59:00Z",
        )
        assert tp.specific_time_iso == "2026-12-31T23:59:00Z"

    def test_on_recurring_schedule(self) -> None:
        tp = TimePredicate(
            predicate_kind="on_recurring_schedule",
            recurrence_description="every weekday at 09:00 PT",
        )
        assert tp.recurrence_description == "every weekday at 09:00 PT"

    def test_unknown_predicate_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimePredicate(
                predicate_kind="continuous",  # type: ignore[arg-type]
                duration_seconds=1,
            )

    def test_predicate_is_frozen(self) -> None:
        tp = TimePredicate(
            predicate_kind="after_duration", duration_seconds=60,
        )
        with pytest.raises(ValidationError):
            tp.duration_seconds = 120  # type: ignore[misc]

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            TimePredicate(  # type: ignore[call-arg]
                predicate_kind="after_duration",
                duration_seconds=60,
                surprise=True,
            )


class TestTimePredicateCrossFieldValidator:
    @pytest.mark.parametrize("predicate_kind,field_name", [
        ("after_duration", "duration_seconds"),
        ("at_specific_time", "specific_time_iso"),
        ("on_recurring_schedule", "recurrence_description"),
    ])
    def test_missing_required_field_rejected(
        self, predicate_kind: str, field_name: str,
    ) -> None:
        with pytest.raises(ValidationError) as exc_info:
            TimePredicate(predicate_kind=predicate_kind)  # type: ignore[arg-type]
        msg = str(exc_info.value)
        assert field_name in msg

    def test_wrong_field_for_predicate_rejected(self) -> None:
        """``after_duration`` with ``specific_time_iso`` set must
        fail — the validator catches that the predicate-context
        coupling is wrong."""
        with pytest.raises(ValidationError) as exc_info:
            TimePredicate(
                predicate_kind="after_duration",
                specific_time_iso="2026-12-31T00:00:00Z",
            )
        msg = str(exc_info.value)
        # Either the missing required field OR the unexpected
        # extra field surfaces in the error.
        assert ("duration_seconds" in msg) or ("specific_time_iso" in msg)

    def test_multiple_context_fields_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            TimePredicate(
                predicate_kind="after_duration",
                duration_seconds=60,
                specific_time_iso="2026-12-31T00:00:00Z",
            )
        assert "permits only" in str(exc_info.value)


# ---------------------------------------------------------------------------
# TimeTriggerBody
# ---------------------------------------------------------------------------

class TestTimeTriggerConstruction:
    @pytest.mark.parametrize("mechanism", [
        "scheduled_flow", "batch_apex", "time_based_workflow",
        "time_dependent_field_update", "test_set_created_date",
    ])
    def test_all_mechanisms_accepted(self, mechanism: str) -> None:
        body = TimeTriggerBody(
            mechanism=mechanism,  # type: ignore[arg-type]
            time_predicate=TimePredicate(
                predicate_kind="after_duration",
                duration_seconds=60,
            ),
        )
        assert body.mechanism == mechanism

    def test_scheduled_target_optional(self) -> None:
        body = TimeTriggerBody(
            mechanism="test_set_created_date",
            time_predicate=TimePredicate(
                predicate_kind="after_duration",
                duration_seconds=60,
            ),
        )
        assert body.scheduled_target is None

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            TimeTriggerBody(
                mechanism="scheduled_flow",
                time_predicate=TimePredicate(
                    predicate_kind="after_duration",
                    duration_seconds=60,
                ),
                kind="inbound-trigger",  # type: ignore[arg-type]
            )


class TestTimeTriggerOperationalRef:
    def test_pinned_scheduled_target_lands_as_pinned_ref(self) -> None:
        body = TimeTriggerBody.model_validate({
            "mechanism": "batch_apex",
            "scheduled_target": _pinned_apex(),
            "time_predicate": {
                "predicate_kind": "on_recurring_schedule",
                "recurrence_description": "nightly at 02:00 UTC",
            },
        })
        assert type(body.scheduled_target) is PinnedRef
        assert not isinstance(body.scheduled_target, IdentityBearingRef)

    def test_logical_scheduled_target_lands_as_logical_ref(self) -> None:
        body = TimeTriggerBody.model_validate({
            "mechanism": "scheduled_flow",
            "scheduled_target": _logical_flow(),
            "time_predicate": {
                "predicate_kind": "after_duration",
                "duration_seconds": 3600,
            },
        })
        assert type(body.scheduled_target) is LogicalRef


class TestTimeTriggerSerialization:
    def test_universal_body_keys_present(self) -> None:
        body = TimeTriggerBody(
            mechanism="scheduled_flow",
            time_predicate=TimePredicate(
                predicate_kind="after_duration",
                duration_seconds=60,
            ),
        )
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "time-trigger"

    def test_full_round_trip(self) -> None:
        body = TimeTriggerBody.model_validate({
            "mechanism": "scheduled_flow",
            "scheduled_target": _logical_flow(),
            "time_predicate": {
                "predicate_kind": "at_specific_time",
                "specific_time_iso": "2026-12-31T23:59:00Z",
            },
        })
        roundtrip = TimeTriggerBody.model_validate(body.model_dump())
        assert roundtrip == body
        assert type(roundtrip.scheduled_target) is LogicalRef
        assert type(roundtrip.time_predicate) is TimePredicate


class TestTimeTriggerRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("time-trigger", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model("time-trigger", 1) is TimeTriggerBody

"""Tests for :class:`UITriggerBody` + :class:`PageContext`.

Per SPEC §3 + D-055. Covers:

  - All 6 action_type + 6 page_kind values accepted; unknown
    rejected.
  - PageContext: parent_record / parent_object both optional;
    OperationalRef discriminator works on each.
  - target_element optional; input_values optional and round-trip.
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
from primeqa.test_representation.models.triggers.ui import (
    PageContext,
    UITriggerBody,
)


def _logical_object(name: str = "Opportunity") -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": "Object",
        "external_id": name,
    }


def _pinned_record() -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": "Object",
        "entity_id": str(uuid4()),
        "version_seq": 1,
        "external_id": "Opportunity",
    }


# ---------------------------------------------------------------------------
# PageContext
# ---------------------------------------------------------------------------

class TestPageContext:
    @pytest.mark.parametrize("page_kind", [
        "record_detail", "list_view", "edit_form",
        "lightning_app", "experience_cloud", "console",
    ])
    def test_all_page_kinds_accepted(self, page_kind: str) -> None:
        pc = PageContext(page_kind=page_kind)  # type: ignore[arg-type]
        assert pc.page_kind == page_kind

    def test_unknown_page_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PageContext(page_kind="api_page")  # type: ignore[arg-type]

    def test_both_parents_optional(self) -> None:
        pc = PageContext(page_kind="lightning_app")
        assert pc.parent_record is None
        assert pc.parent_object is None

    def test_pinned_parent_record_lands_as_pinned_ref(self) -> None:
        pc = PageContext.model_validate({
            "page_kind": "record_detail",
            "parent_record": _pinned_record(),
        })
        assert type(pc.parent_record) is PinnedRef
        assert not isinstance(pc.parent_record, IdentityBearingRef)

    def test_logical_parent_object_lands_as_logical_ref(self) -> None:
        pc = PageContext.model_validate({
            "page_kind": "list_view",
            "parent_object": _logical_object(),
        })
        assert type(pc.parent_object) is LogicalRef

    def test_page_context_is_frozen(self) -> None:
        pc = PageContext(page_kind="lightning_app")
        with pytest.raises(ValidationError):
            pc.page_kind = "console"  # type: ignore[misc]

    def test_page_context_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            PageContext(  # type: ignore[call-arg]
                page_kind="lightning_app", surprise=True,
            )


# ---------------------------------------------------------------------------
# UITriggerBody
# ---------------------------------------------------------------------------

class TestUITriggerConstruction:
    @pytest.mark.parametrize("action_type", [
        "button_click", "form_submit", "navigation",
        "inline_edit", "list_view_action", "related_list_action",
    ])
    def test_all_action_types_accepted(self, action_type: str) -> None:
        body = UITriggerBody(
            action_type=action_type,  # type: ignore[arg-type]
            page_context=PageContext(page_kind="record_detail"),
        )
        assert body.action_type == action_type

    def test_unknown_action_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UITriggerBody(
                action_type="long_press",  # type: ignore[arg-type]
                page_context=PageContext(page_kind="record_detail"),
            )

    def test_target_element_optional(self) -> None:
        body = UITriggerBody(
            action_type="navigation",
            page_context=PageContext(page_kind="lightning_app"),
        )
        assert body.target_element is None

    def test_input_values_round_trip(self) -> None:
        body = UITriggerBody(
            action_type="form_submit",
            page_context=PageContext(page_kind="edit_form"),
            target_element="Save",
            input_values={"StageName": "Proposal", "Amount": 50_000},
        )
        assert body.input_values is not None
        assert body.input_values["StageName"] == "Proposal"
        roundtrip = UITriggerBody.model_validate(body.model_dump())
        assert roundtrip == body

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            UITriggerBody(
                action_type="button_click",
                page_context=PageContext(page_kind="record_detail"),
                kind="time-trigger",  # type: ignore[arg-type]
            )

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            UITriggerBody(  # type: ignore[call-arg]
                action_type="button_click",
                page_context=PageContext(page_kind="record_detail"),
                surprise=True,
            )


class TestUITriggerOperationalRefAtNestedDepth:
    def test_pinned_parent_record_via_body(self) -> None:
        body = UITriggerBody.model_validate({
            "action_type": "button_click",
            "page_context": {
                "page_kind": "record_detail",
                "parent_record": _pinned_record(),
            },
            "target_element": "Submit",
        })
        assert type(body.page_context.parent_record) is PinnedRef
        assert not isinstance(
            body.page_context.parent_record, IdentityBearingRef,
        )

    def test_logical_parent_object_via_body(self) -> None:
        body = UITriggerBody.model_validate({
            "action_type": "list_view_action",
            "page_context": {
                "page_kind": "list_view",
                "parent_object": _logical_object(),
            },
        })
        assert type(body.page_context.parent_object) is LogicalRef


class TestUITriggerSerialization:
    def test_universal_body_keys_present(self) -> None:
        body = UITriggerBody(
            action_type="button_click",
            page_context=PageContext(page_kind="record_detail"),
        )
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "ui-trigger"

    def test_full_round_trip(self) -> None:
        body = UITriggerBody.model_validate({
            "action_type": "form_submit",
            "page_context": {
                "page_kind": "edit_form",
                "parent_record": _pinned_record(),
                "parent_object": _logical_object(),
            },
            "target_element": "Save",
            "input_values": {"Amount": 12345},
        })
        roundtrip = UITriggerBody.model_validate(body.model_dump())
        assert roundtrip == body
        assert type(roundtrip.page_context) is PageContext
        assert type(roundtrip.page_context.parent_record) is PinnedRef
        assert type(roundtrip.page_context.parent_object) is LogicalRef


class TestUITriggerRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("ui-trigger", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model("ui-trigger", 1) is UITriggerBody

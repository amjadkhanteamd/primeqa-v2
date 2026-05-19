"""Tests for :class:`InboundTriggerBody` + :class:`PayloadDescriptor`.

Per SPEC §3 + D-055. Covers:

  - All 5 channel values accepted; unknown rejected.
  - All 5 payload-format values accepted; unknown rejected.
  - Payload ``content`` accepts both string and dict shapes.
  - ``endpoint`` (OperationalRef): pinned input → PinnedRef
    instance; logical input → LogicalRef instance; None permitted.
  - **OperationalRef pinned payload does NOT promote to
    IdentityBearingRef** — re-verified at trigger-body nesting
    depth per the B-α empirical finding.
  - Registry registration on import.
  - Full round-trip preserves nested type discipline.
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
from primeqa.test_representation.models.triggers.inbound import (
    InboundTriggerBody,
    PayloadDescriptor,
)


def _pinned_endpoint() -> dict:
    return {
        "ref_kind": "pinned",
        "entity_type": "ApexClass",
        "entity_id": str(uuid4()),
        "version_seq": 7,
        "external_id": "AccountWebhook",
    }


def _logical_endpoint() -> dict:
    return {
        "ref_kind": "logical",
        "entity_type": "ApexClass",
        "external_id": "AccountWebhook",
    }


# ---------------------------------------------------------------------------
# PayloadDescriptor
# ---------------------------------------------------------------------------

class TestPayloadDescriptor:
    @pytest.mark.parametrize("fmt", [
        "json", "xml", "form_encoded", "email_mime", "binary",
    ])
    def test_all_formats_accepted(self, fmt: str) -> None:
        p = PayloadDescriptor(
            format=fmt,  # type: ignore[arg-type]
            content="raw",
        )
        assert p.format == fmt

    def test_unknown_format_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PayloadDescriptor(
                format="protobuf",  # type: ignore[arg-type]
                content="x",
            )

    def test_dict_content_accepted(self) -> None:
        p = PayloadDescriptor(
            format="json",
            content={"AccountId": "001x", "Status": "Approved"},
        )
        assert isinstance(p.content, dict)
        assert p.content["Status"] == "Approved"

    def test_string_content_accepted(self) -> None:
        p = PayloadDescriptor(format="email_mime", content="From: ...")
        assert isinstance(p.content, str)

    def test_headers_optional(self) -> None:
        p = PayloadDescriptor(format="json", content={})
        assert p.headers is None

    def test_headers_round_trip(self) -> None:
        p = PayloadDescriptor(
            format="json", content={},
            headers={"Content-Type": "application/json"},
        )
        roundtrip = PayloadDescriptor.model_validate(p.model_dump())
        assert roundtrip == p

    def test_payload_is_frozen(self) -> None:
        p = PayloadDescriptor(format="json", content={})
        with pytest.raises(ValidationError):
            p.format = "xml"  # type: ignore[misc]

    def test_payload_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            PayloadDescriptor(  # type: ignore[call-arg]
                format="json", content={}, surprise=True,
            )


# ---------------------------------------------------------------------------
# InboundTriggerBody construction
# ---------------------------------------------------------------------------

class TestInboundTriggerConstruction:
    @pytest.mark.parametrize("channel", [
        "rest", "soap", "inbound_email",
        "streaming_push", "external_platform_event",
    ])
    def test_all_channels_accepted(self, channel: str) -> None:
        body = InboundTriggerBody(
            channel=channel,  # type: ignore[arg-type]
            payload=PayloadDescriptor(format="json", content={}),
        )
        assert body.channel == channel

    def test_unknown_channel_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InboundTriggerBody(
                channel="grpc",  # type: ignore[arg-type]
                payload=PayloadDescriptor(format="json", content={}),
            )

    def test_endpoint_optional(self) -> None:
        body = InboundTriggerBody(
            channel="streaming_push",
            payload=PayloadDescriptor(format="json", content={}),
        )
        assert body.endpoint is None

    def test_external_identity_optional(self) -> None:
        body = InboundTriggerBody(
            channel="rest",
            payload=PayloadDescriptor(format="json", content={}),
        )
        assert body.external_identity is None

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            InboundTriggerBody(
                channel="rest",
                payload=PayloadDescriptor(format="json", content={}),
                kind="ui-trigger",  # type: ignore[arg-type]
            )

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            InboundTriggerBody(  # type: ignore[call-arg]
                channel="rest",
                payload=PayloadDescriptor(format="json", content={}),
                surprise=True,
            )


# ---------------------------------------------------------------------------
# OperationalRef behaviour at trigger-body nesting depth
# ---------------------------------------------------------------------------

class TestInboundTriggerOperationalRef:
    def test_pinned_endpoint_lands_as_pinned_ref(self) -> None:
        body = InboundTriggerBody.model_validate({
            "channel": "rest",
            "payload": {"format": "json", "content": {}},
            "endpoint": _pinned_endpoint(),
        })
        assert type(body.endpoint) is PinnedRef
        # Critical re-verification of the B-α finding: NOT
        # IdentityBearingRef at the operational layer.
        assert not isinstance(body.endpoint, IdentityBearingRef)

    def test_logical_endpoint_lands_as_logical_ref(self) -> None:
        body = InboundTriggerBody.model_validate({
            "channel": "rest",
            "payload": {"format": "json", "content": {}},
            "endpoint": _logical_endpoint(),
        })
        assert type(body.endpoint) is LogicalRef

    def test_endpoint_unknown_discriminator_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InboundTriggerBody.model_validate({
                "channel": "rest",
                "payload": {"format": "json", "content": {}},
                "endpoint": {
                    "ref_kind": "mystery",
                    "entity_type": "ApexClass",
                    "external_id": "x",
                },
            })


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestInboundTriggerSerialization:
    def test_universal_body_keys_present(self) -> None:
        body = InboundTriggerBody(
            channel="rest",
            payload=PayloadDescriptor(format="json", content={}),
        )
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "inbound-trigger"

    def test_full_round_trip_with_pinned_endpoint(self) -> None:
        body = InboundTriggerBody.model_validate({
            "channel": "rest",
            "payload": {
                "format": "json",
                "content": {"AccountId": "001x"},
                "headers": {"X-Source": "Stripe"},
            },
            "endpoint": _pinned_endpoint(),
            "external_identity": "stripe-prod",
        })
        roundtrip = InboundTriggerBody.model_validate(body.model_dump())
        assert roundtrip == body
        assert type(roundtrip.endpoint) is PinnedRef
        assert type(roundtrip.payload) is PayloadDescriptor

    def test_full_round_trip_with_logical_endpoint(self) -> None:
        body = InboundTriggerBody.model_validate({
            "channel": "rest",
            "payload": {"format": "json", "content": {}},
            "endpoint": _logical_endpoint(),
        })
        roundtrip = InboundTriggerBody.model_validate(body.model_dump())
        assert type(roundtrip.endpoint) is LogicalRef


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestInboundTriggerRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("inbound-trigger", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model("inbound-trigger", 1) is InboundTriggerBody

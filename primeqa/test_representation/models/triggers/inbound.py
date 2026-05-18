"""inbound-trigger body (operational layer, runtime plane).

Per SPEC §3 + D-055. An inbound-trigger represents an external
system pushing a payload into Salesforce — REST/SOAP calls, inbound
email handlers, streaming-API pushes, external platform events
relayed in.

Per D-055 §"Trigger-kind identity nuance" + SPEC §5.1: this is an
operational-layer body. References here default to logical (the
endpoint resolves by name; the payload's content doesn't pin to a
specific S1 entity version). The Coordinator allows pinned overrides
when the test asserts reproducibility-critical semantics about a
specific endpoint version, but the slot type is
:data:`OperationalRef` (logical | pinned union) — NOT
``IdentityBearingRef``.

The two B-α empirical findings carried into B-γ:
  - Pydantic dispatches OperationalRef by ``ref_kind`` discriminator
    cleanly, even nested inside body shapes.
  - A pinned-shaped payload through OperationalRef deserializes as
    a plain :class:`PinnedRef` (NOT :class:`IdentityBearingRef`);
    the subclass-promotion path is Coordinator-side and never
    triggers in operational bodies.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import OperationalRef
from primeqa.test_representation.models.registry import register_body


# ---------------------------------------------------------------------------
# PayloadDescriptor — what the external system actually sends in
# ---------------------------------------------------------------------------

class PayloadDescriptor(BaseModel):
    """Describes the inbound payload sent across the channel.

    ``content`` is a ``Union[str, dict[str, Any]]`` because format
    drives the natural Python shape: JSON / form-encoded payloads
    arrive as a dict-of-fields; email-MIME / binary / raw-XML
    payloads arrive as a string blob. The substrate doesn't
    enforce format-vs-content coupling at v1 — the recipe and
    Coordinator decide whether a string is acceptable for a
    declared JSON format, etc.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal[
        "json",
        "xml",
        "form_encoded",
        "email_mime",
        "binary",
    ]
    """The wire-format of the inbound payload."""

    content: Union[str, dict[str, Any]]
    """The payload body. Dict for structured formats, string for
    blobs. ``Any`` value type for dict members so the substrate
    doesn't second-guess the source system's shape."""

    headers: Optional[dict[str, str]] = None
    """HTTP / protocol-level headers if any. Optional because
    inbound channels like inbound_email may not carry the same
    notion of headers."""


# ---------------------------------------------------------------------------
# InboundTriggerBody
# ---------------------------------------------------------------------------

@register_body("inbound-trigger", 1)
class InboundTriggerBody(BodyBase):
    """The inbound-trigger body shape (v1).

    Per D-055: this captures the *causal initiation* — what
    external system pushed what to Salesforce — and NOT what
    Salesforce did about it (that's the observation/automation-effect
    layer). The trigger is operational-by-default, so refs use
    :data:`OperationalRef`.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["inbound-trigger"] = "inbound-trigger"

    channel: Literal[
        "rest",
        "soap",
        "inbound_email",
        "streaming_push",
        "external_platform_event",
    ]
    """The transport / mechanism through which the payload arrived."""

    endpoint: Optional[OperationalRef] = None
    """The S1 entity representing the inbound endpoint (the Apex
    REST resource, the inbound-email handler, the platform-event
    subscription, etc.). Optional because some inbound channels —
    notably streaming pushes — terminate at platform-managed
    resources without a tenant-controlled S1 entity. Pinned vs
    logical per the slot's reproducibility needs."""

    payload: PayloadDescriptor
    """The payload itself — format, content, optional headers."""

    external_identity: Optional[str] = None
    """The external system's identity as far as Salesforce sees
    it (named credential principal, integration user name,
    callout sender). Free-form string at v1 — structured
    representation deferred to a future schema version."""

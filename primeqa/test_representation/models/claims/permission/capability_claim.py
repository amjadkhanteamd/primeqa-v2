"""capability-claim body shape (permission archetype).

Per SPEC §3 + D-080 / D-123. A capability-claim asserts: "a Profile or Permission
Set grants an access capability (read/edit) on an Object or Field" — e.g. "the
Admin profile can edit Account.AnnualRevenue". Layer-1-complete (D-079): verified
by the S1 ``GRANTS_OBJECT_ACCESS`` / ``GRANTS_FIELD_ACCESS`` edge directly.

v1 grounds **direct** grants (the configured grant edge). Capabilities derived
from sharing rules / OWD / role hierarchy (S1 Tier-2, unmodeled) have no direct
grant edge and refuse — the run-as-execution verification surface that would
check the *effective runtime* capability is deferred (D-080).
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("capability-claim", 1)
class CapabilityClaimBody(BodyBase):
    """The capability-claim body shape (v1).

    Asserts a granting subject (Profile/PermissionSet) grants ``granted_capability``
    on a target (Object/Field). Both endpoints are identity-bearing; verified by
    the S1 grant edge (`get_related`), Layer-1-complete per D-079.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["capability-claim"] = "capability-claim"

    granting_subject: IdentityBearingRef
    """The Profile or PermissionSet granting access."""

    target: IdentityBearingRef
    """The Object or Field the capability is granted on."""

    granted_capability: str
    """The asserted capability (e.g. ``"read"`` / ``"edit"``)."""

    grant_type: Literal["object", "field"]
    """Disambiguates ``GRANTS_OBJECT_ACCESS`` (object) vs ``GRANTS_FIELD_ACCESS`` (field)."""

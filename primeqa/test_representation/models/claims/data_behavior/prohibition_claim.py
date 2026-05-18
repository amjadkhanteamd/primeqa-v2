"""prohibition-claim body shape (data-behavior archetype).

Per SPEC §3 + D-053. A prohibition-claim asserts: "the platform
rejects this kind of operation against this target, via this
mechanism, and the rejection surfaces with this signal."

Distinct from :mod:`automation_effect_claim` with a
BlockedOperationEffect: prohibition-claim centers *that an
operation is rejected* as the primary asserted truth, while
automation-effect with a block centers *what the automation
does*. In practice, the prohibition shape is the right one when
the test is "this should be impossible"; the automation-effect
shape is the right one when the test is "this rule does its
job."

Use cases:
  - "Standard users cannot delete Opportunities in stage=Closed
    Won." (operation=delete, mechanism=validation_rule)
  - "Two Contacts with the same Email cannot be inserted into
    the same Account." (operation=create_duplicate,
    mechanism=apex_trigger or system_enforced)
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.primitives import RejectionSignal
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("prohibition-claim", 1)
class ProhibitionClaimBody(BodyBase):
    """The prohibition-claim body shape (v1).

    Per D-058 §5.4: ``target`` is walkable for coverage
    extraction. The ``expected_rejection`` carries a possibly-nested
    IdentityBearingRef (``error_field``) which the
    :class:`RejectionSignal` model exposes — coverage extraction
    walks the body tree generically and finds it.

    The mechanism vs operation split mirrors the automation-effect
    shape: ``operation`` is what's being attempted,
    ``prohibition_mechanism`` is the Salesforce primitive
    enforcing the prohibition. Per D-053's guardrail, same
    semantic ("the platform refuses to do X"), different
    mechanism → same claim-kind with a sub-discriminator.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["prohibition-claim"] = "prohibition-claim"

    target: IdentityBearingRef
    """The S1 entity the prohibited operation would act on (e.g.,
    the Opportunity Object, or a specific record-typed reference).
    Pinned per D-058 §5."""

    operation: Literal[
        "delete",
        "create_duplicate",
        "modify_field",
        "modify_record",
        "share",
        "transfer_ownership",
    ]
    """What's being attempted. Closed taxonomy at v1; extensions
    land via a new body_schema_version."""

    prohibition_mechanism: Literal[
        "validation_rule",
        "sharing_rule",
        "apex_trigger",
        "system_enforced",
    ]
    """The Salesforce primitive enforcing the prohibition.
    ``system_enforced`` covers platform-level rejections that
    aren't driven by a configurable artifact (e.g.,
    impossible-by-design operations)."""

    expected_rejection: RejectionSignal
    """How the rejection manifests. At least one of error_code /
    error_message_pattern / error_field MUST be set (enforced by
    RejectionSignal's own validator) — otherwise the claim is
    unverifiable by any recipe."""

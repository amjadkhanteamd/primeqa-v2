"""value-claim body shape (data-behavior archetype).

Per SPEC §3 (claim-kind taxonomy) + D-053 (per-kind definitions).
A value-claim asserts: "for this subject (typically a field, or a
record-typed reference whose value-shape is the record itself), the
value should be X" — where X is an :class:`ExpectedValue` covering
the literal-vs-null distinction.

Use cases:
  - "Account.Industry should be 'Technology'."
  - "Opportunity.IsClosed should be ``true``."
  - "The Tax__c field should be NULL when StateCode is 'CA'."
    (The condition lives in the conditions body; the value-claim
    asserts only the value half.)

This is the simplest of the four data-behavior claim-kinds: one
subject + one expected value. No state transitions, no automation
firing, no operations being rejected.
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.primitives import ExpectedValue
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("value-claim", 1)
class ValueClaimBody(BodyBase):
    """The value-claim body shape (v1).

    Per D-053 (value-claim semantic): the simplest data-behavior
    claim — pin a subject to an expected value. The subject is
    identity-bearing per D-058 §5: replacing the subject changes
    *what* the test claims, not just *how*, so it MUST be a
    pinned IdentityBearingRef.

    Per D-058 §5.4: the subject ref is walkable for coverage
    extraction without further structure — it's a flat field at
    the body root.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["value-claim"] = "value-claim"

    subject: IdentityBearingRef
    """The S1 entity (typically a field) whose value is asserted.
    Pinned per D-060 §4.7.6 — identity-bearing slots reject
    logical references."""

    expected_value: ExpectedValue
    """Discriminated union over literal vs null. Pydantic
    constructs the right concrete variant during validation."""

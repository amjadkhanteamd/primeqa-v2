"""acceptance-claim body shape (data-behavior archetype).

Per D-305. An acceptance-claim asserts: "the platform ACCEPTS this
operation against this target under this business state" — the
exact mirror of the prohibition-claim. The state (the field/value
clauses that define the case) rides ``semantic_conditions`` and is
IDENTITY-BEARING: unlike a prohibition (where the violating value
is recipe-operational because the state, not the value, is the
assertion — D-293/D-110.3), an acceptance case is DEFINED by its
values — "Loan 79,99,999 saves" and "Loan 80,00,000 saves" are
different assertions differing only by value, so they must hash
apart.

Use cases:
  - "A valid Home Loan Opportunity saves with no validation
    errors." (operation=create)
  - "A Personal Loan Opportunity saves WITHOUT the Home-Loan
    fields — the Home-Loan validations do not misfire."
    (negative-scope acceptance; the conditions state what IS set)
  - "Loan just below / at the property value saves." (the
    boundary acceptances TC-007/TC-008)
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("acceptance-claim", 1)
class AcceptanceClaimBody(BodyBase):
    """The acceptance-claim body shape (v1).

    Deliberately minimal: acceptance IS the assertion, so the body
    carries only the target + the operation. The business state
    lives in the claim's ``semantic_conditions`` (identity-bearing,
    the D-293 machinery verbatim); the create's staged values are
    those grounded condition values. Per D-058 §5.4: ``target`` is
    walkable for coverage extraction.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["acceptance-claim"] = "acceptance-claim"

    target: IdentityBearingRef
    """The S1 entity the accepted operation acts on (the Object
    being created). Pinned per D-058 §5."""

    operation: Literal["create", "update"]
    """What is being accepted. ``create`` (D-305) — the staged state
    must save. ``update`` (D-306, the stage-progress case) — given the
    staged initial state, the CHANGE must succeed; the recipe stages
    the initial clauses on the create and the update clauses on a
    positive UpdateStep carrying ``expect_acceptance``. Widened as an
    additive Literal within body_schema v1 (D-306 — supersedes the
    D-305 new-version reservation; old payloads validate unchanged)."""

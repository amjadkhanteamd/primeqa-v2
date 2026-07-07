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

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from primeqa.test_representation.models.common import ArraySemantics, BodyBase
from primeqa.test_representation.models.primitives import StateDescriptor
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
    """What is being accepted. New emissions are create-only on v1
    (D-306.1): the update case authors the v2 shape below, whose
    ``update_state`` makes the phase split identity-bearing.
    ``update`` stays decodable here ONLY for the pre-D-306.1
    persisted rows (the adversarial review found the v1 flat
    conditions encoding erased the phase split — those claims were
    deprecated, but history reads must not crash)."""


@register_body("acceptance-claim", 2)
class AcceptanceClaimUpdateBody(BodyBase):
    """The acceptance-claim body shape (v2) — the UPDATE case
    (D-306.1). "Given the initial state, the CHANGE to
    ``update_state`` is accepted."

    Why a new version, not a v1 field: canonicalization includes
    every model field (None → null), so adding a slot to v1 would
    re-key every existing create-acceptance claim (dedup misses,
    duplicate claims). And why the destination rides the BODY, not
    ``semantic_conditions``: the conditions layer is a commutative
    AND-composed SET — the initial/update partition and the change
    DIRECTION are erased by its sort (the adversarial review
    reproduced progress/regress and split-shift collisions). Here
    ``semantic_conditions`` carries ONLY the initial state (a
    satisfiable conjunction again) and ``update_state`` carries the
    destination — both identity-bearing, phase-distinct by
    construction.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[2] = 2
    kind: Literal["acceptance-claim"] = "acceptance-claim"

    target: IdentityBearingRef
    """The S1 entity the accepted operation acts on. Pinned per
    D-058 §5."""

    operation: Literal["update"]
    """v2 is the update case only; creates stay v1."""

    update_state: StateDescriptor
    """The destination state — the (field → value) changes the
    update stages and the org must ACCEPT. Values are
    ``LiteralValue``-wrapped, ``_identity_safe``-coerced at
    grounding (no floats per SPEC §6.3.2)."""


@register_body("acceptance-claim", 3)
class AcceptanceClaimArcBody(BodyBase):
    """The acceptance-claim body shape (v3) — the APPROVAL-ACTION ARC
    (D-333). "After these approval actions run against the subject record,
    the CHANGE to ``update_state`` is ACCEPTED."

    v2's update case plus the identity-bearing ``approval_actions``: "the
    move to Approved is accepted AFTER approval" and the plain "the move is
    accepted" are different assertions over the same conditions +
    update_state — a new version so neither v2 claims re-key nor the arc
    erases (the D-306.1 lesson applied to the approval phase).
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[3] = 3
    kind: Literal["acceptance-claim"] = "acceptance-claim"

    target: IdentityBearingRef
    """The S1 entity the accepted operation acts on. Pinned per D-058 §5."""

    operation: Literal["update"]
    """v3 is the arc's update case only (an arc needs a record to submit,
    so the create-accepted case cannot carry one)."""

    update_state: StateDescriptor
    """The destination state the org must ACCEPT after the arc — same
    contract as v2 (LiteralValue-wrapped, identity-safe)."""

    approval_actions: Annotated[
        list[Literal["submit", "approve", "reject"]],
        ArraySemantics.ORDERED,
    ] = Field(min_length=1)
    """The ordered approval actions run BEFORE the accepted update
    (``["submit", "approve"]`` = the granted case). ORDERED per D-059
    §6.3.4."""

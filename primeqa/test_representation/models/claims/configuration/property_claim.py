"""property-claim body shape (configuration archetype).

Per SPEC §3 + D-079 / D-122. A property-claim asserts: "a named S1-modeled
property of an entity holds a value" — e.g. "the Account.Industry field is
required", "Opportunity.Amount has length 18". Layer-1-complete (D-079):
verified by reading the S1 detail row (`get_entity_details`). An unmodeled
property is an honest ontology-gap refusal at grounding, never invented; the
value is read from S1, never taken from the requirement on faith.
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.primitives import ExpectedValue
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("property-claim", 1)
class PropertyClaimBody(BodyBase):
    """The property-claim body shape (v1).

    Asserts an S1-modeled property of a subject entity equals a value. The
    subject is identity-bearing; ``property_name`` names the S1 detail column
    (e.g. ``"is_required"`` / ``"length"`` / ``"field_type"``); ``expected_value``
    is the asserted value, read from the S1 detail row at grounding
    (invent-nothing, D-122).
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["property-claim"] = "property-claim"

    subject: IdentityBearingRef
    """The S1 entity whose property is asserted."""

    property_name: str
    """The S1-modeled detail-table property (e.g. ``"is_required"``, ``"length"``)."""

    expected_value: ExpectedValue
    """The asserted property value (LiteralValue / NullValue), read from S1."""

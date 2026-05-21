"""metadata-relationship-claim body shape (configuration archetype).

Per SPEC §3 + D-053, and the S3 draft-vertical debut (D-098.1). A
metadata-relationship-claim asserts: "a specific Tier-1 relationship exists in
the org between two named S1 entities" — e.g. "Validation Rule R applies to
Account" (an ``APPLIES_TO`` edge). Layer-1-complete (D-079): the assertion is
verified by S1 edge existence directly — there is no deeper formula layer.

The configuration archetype's debut body (D-098.1, superseding D-097.1's
value-claim debut on a grounding finding). existence-claim / property-claim
follow as configuration grows.
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("metadata-relationship-claim", 1)
class MetadataRelationshipClaimBody(BodyBase):
    """The metadata-relationship-claim body shape (v1).

    Asserts a directed Tier-1 relationship ``source --edge_type--> target``
    between two S1 entities. Both endpoints are identity-bearing (the claim's
    identity is the asserted relationship). Verified by S1 edge existence
    (`get_related`), Layer-1-complete per D-079.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["metadata-relationship-claim"] = "metadata-relationship-claim"

    edge_type: str
    """The asserted Tier-1 edge type (e.g. ``"APPLIES_TO"``). A plain string at
    this layer — substrate-2 does not import substrate-1's ``TIER_1_EDGES``
    registry; substrate-3 admissibility validates it against the live registry
    before emission (D-098.1)."""

    source: IdentityBearingRef
    """The relationship's source endpoint (S1 entity)."""

    target: IdentityBearingRef
    """The relationship's target endpoint (S1 entity)."""

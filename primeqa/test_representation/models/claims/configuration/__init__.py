"""Configuration archetype claim bodies.

The S3 draft-vertical debut archetype (D-098.1). metadata-relationship-claim
shipped first — the strongest fully-grounded artifact over the current substrate
boundary (a Tier-1 edge verified via `get_related`). existence-claim and
property-claim follow (D-122) as configuration grows.

Importing this package triggers ``@register_body`` on each shipped
configuration body.
"""
from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field

from primeqa.test_representation.models.claims.configuration.existence_claim import (
    ExistenceClaimBody,
)
from primeqa.test_representation.models.claims.configuration.metadata_relationship_claim import (
    MetadataRelationshipClaimBody,
)
from primeqa.test_representation.models.claims.configuration.property_claim import (
    PropertyClaimBody,
)

__all__ = [
    "MetadataRelationshipClaimBody",
    "ExistenceClaimBody",
    "PropertyClaimBody",
    "ConfigurationClaimBody",
]

# Archetype-scoped ``kind``-discriminated union (D-122 — the §3 plan as
# existence-claim / property-claim land).
ConfigurationClaimBody = Annotated[
    Union[
        MetadataRelationshipClaimBody,
        ExistenceClaimBody,
        PropertyClaimBody,
    ],
    Field(discriminator="kind"),
]

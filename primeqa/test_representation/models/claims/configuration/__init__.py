"""Configuration archetype claim bodies.

The S3 draft-vertical debut archetype (D-098.1). metadata-relationship-claim
ships first — the strongest fully-grounded artifact over the current substrate
boundary (a Tier-1 edge verified via `get_related`). existence-claim and
property-claim follow as configuration grows.

Importing this package triggers ``@register_body`` on each shipped
configuration body.
"""
from __future__ import annotations

from primeqa.test_representation.models.claims.configuration.metadata_relationship_claim import (
    MetadataRelationshipClaimBody,
)

__all__ = [
    "MetadataRelationshipClaimBody",
    "ConfigurationClaimBody",
]

# Archetype-scoped type. A single-member alias today; becomes a
# ``kind``-discriminated Union when existence-claim / property-claim land.
ConfigurationClaimBody = MetadataRelationshipClaimBody

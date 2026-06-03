"""Permission archetype claim bodies (D-080 / D-123).

``capability-claim`` ships first — the only S1-Tier-1-groundable permission kind
(direct grant edges). ``sharing-rule-claim`` follows when S1 Tier-2 (sharing
rules / OWD) lands.

Importing this package triggers ``@register_body`` on each shipped body.
"""
from __future__ import annotations

from primeqa.test_representation.models.claims.permission.capability_claim import (
    CapabilityClaimBody,
)

__all__ = [
    "CapabilityClaimBody",
    "PermissionClaimBody",
]

# Archetype-scoped type. A single-member alias today; becomes a
# ``kind``-discriminated Union when sharing-rule-claim lands.
PermissionClaimBody = CapabilityClaimBody

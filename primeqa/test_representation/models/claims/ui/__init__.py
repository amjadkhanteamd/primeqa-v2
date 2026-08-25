"""UI archetype claim bodies (D-081 / D-124).

``layout-claim`` ships first — the only S1-Tier-1-groundable UI kind (the
``INCLUDES_FIELD`` Layout->Field edge is synced). ``element-state-claim`` (runtime
Lightning render/enable, S1 Tier-3) and ``navigation-claim`` follow later.

Importing this package triggers ``@register_body`` on each shipped body.
"""
from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field

from primeqa.test_representation.models.claims.ui.conformance_claim import (
    ConformanceClaimBody,
)
from primeqa.test_representation.models.claims.ui.layout_claim import (
    LayoutClaimBody,
)

__all__ = [
    "ConformanceClaimBody",
    "LayoutClaimBody",
    "UIClaimBody",
]

# Archetype-scoped ``kind``-discriminated union (two members since 3A-2;
# element-state / navigation extend here when they land).
UIClaimBody = Annotated[
    Union[LayoutClaimBody, ConformanceClaimBody],
    Field(discriminator="kind"),
]

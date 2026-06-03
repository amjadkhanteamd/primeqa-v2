"""layout-claim body shape (UI archetype).

Per SPEC §3 + D-081 / D-124. A layout-claim asserts: "a Field appears on a
PageLayout" — e.g. "Account.AnnualRevenue appears on the Account (Sales) layout".
Layer-1-complete (D-079): verified by the S1 ``INCLUDES_FIELD`` edge directly
(Layout -> Field, carrying section/grid placement properties).

v1 grounds **placement-existence** (the field is on the layout). The
``INCLUDES_FIELD`` edge's existence IS the placement — there is no capability-style
bit to check. Section / row / column **assertions** (e.g. "F is in the 'Address'
section") are a v1.1 refinement (the edge carries those as properties; not yet
asserted). The *runtime* render/enable question (does the field actually surface
in the live Lightning page) is ``element-state-claim`` — a different verification
surface (S1 Tier-3, deferred); a layout-claim is a metadata fact, not a UI
interaction (D-124).
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("layout-claim", 1)
class LayoutClaimBody(BodyBase):
    """The layout-claim body shape (v1).

    Asserts a ``field`` is placed on a ``layout``. Both endpoints are
    identity-bearing; verified by the S1 ``INCLUDES_FIELD`` edge (`get_related`),
    Layer-1-complete per D-079.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["layout-claim"] = "layout-claim"

    layout: IdentityBearingRef
    """The PageLayout the field is placed on."""

    field: IdentityBearingRef
    """The Field asserted to appear on the layout."""

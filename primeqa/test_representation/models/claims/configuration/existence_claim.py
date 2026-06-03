"""existence-claim body shape (configuration archetype).

Per SPEC §3 + D-079 / D-122. An existence-claim asserts: "a specific S1 entity
exists in the org" — e.g. "the Account.Industry field exists". Layer-1-complete
(D-079): verified by S1 entity existence directly (a non-empty ``get_entities``),
there is no deeper formula layer. The configuration archetype's second body
(D-122), following metadata-relationship-claim (D-098.1).
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import IdentityBearingRef
from primeqa.test_representation.models.registry import register_body


@register_body("existence-claim", 1)
class ExistenceClaimBody(BodyBase):
    """The existence-claim body shape (v1).

    Asserts a single S1 entity exists. The subject is identity-bearing — the
    claim's identity IS the asserted entity. Verified by S1 existence
    (`get_entities`), Layer-1-complete per D-079.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["existence-claim"] = "existence-claim"

    subject: IdentityBearingRef
    """The S1 entity asserted to exist (Object / Field / Flow / ValidationRule / …)."""

"""conformance-claim body shape (UI archetype) — LLD 3A-2 §b / HLD DE-02.

Asserts: "surface S conforms to Plimsol rule R" — identity content is
``plimsol_rule_id × surface natural key`` (D2: the rule id is the Plimsol
ATOM; engines and standards are mappings, never identity). ENUMERATED-ONLY:
these claims are derived deterministically (active rules × surface
inventory, DE-05) and are deliberately ABSENT from the LLM generation
vocabulary — deterministic-before-LLM applied to the kind.

Verification is browser-plane (engine observations resolved through the S5
registry by the result processor, 3A-4); no in-process S4 vertical executes
it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, field_validator

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.registry import register_body
from primeqa.test_representation.models.surface import SurfaceNaturalKey

_RULE_ID_SHAPE = r"^PLM-[A-Z0-9]+-[0-9]{3}$"


@register_body("conformance-claim", 1)
class ConformanceClaimBody(BodyBase):
    """The conformance-claim body shape (v1)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["conformance-claim"] = "conformance-claim"

    plimsol_rule_id: str
    """The Plimsol rule ATOM (S5 registry id, e.g. PLM-A11Y-001)."""

    surface: SurfaceNaturalKey
    """The declared surface (the frozen v1 five-field natural key)."""

    @field_validator("plimsol_rule_id")
    @classmethod
    def _rule_id_shape(cls, v: str) -> str:
        import re
        if not re.match(_RULE_ID_SHAPE, v):
            raise ValueError(
                f"plimsol_rule_id {v!r} does not match {_RULE_ID_SHAPE}")
        return v

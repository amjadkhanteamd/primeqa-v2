"""ui-inspection recipe body — LLD 3A-2 §c.

Its OWN kind, deliberately NOT a reuse of ``ui-recipe``: a conformance scan
has no steps (UIRecipeBody's ordered step model is Mode B/R3 inheritance
and stays MUTATING); ``ui-inspection`` is declarative and READ_ONLY by
declaration (SAD A3; RECIPE_MODES). The recipe names WHAT (surface +
engine); the MANIFEST pins HOW (artifact pins, catalogue release,
stabilisation, persona/auth — D-461). Executed on the browser plane only.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.registry import register_body
from primeqa.test_representation.models.surface import SurfaceNaturalKey


class EngineRef(BaseModel):
    """The observation engine by NAME only — versions/hashes are manifest
    pins (D-461), never recipe content."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    name: str = "axe-core"


@register_body("ui-inspection", 1)
class UiInspectionBody(BodyBase):
    """The ui-inspection recipe body shape (v1) — declarative, stepless."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["ui-inspection"] = "ui-inspection"

    surface: SurfaceNaturalKey
    engine: EngineRef = EngineRef()

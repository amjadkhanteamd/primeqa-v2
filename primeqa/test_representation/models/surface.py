"""SurfaceNaturalKey — the D2 surface identity value object (LLD 3A-2 §b).

The FIVE fields are the FROZEN IDENTITY_HASH_VERSION v1 composition:
site | path | persona_scope | record_context_ref | viewport — viewport
participates ONLY where the criterion makes it semantic (FND-01b) and is
otherwise absent. ANY field change (addition, removal, semantics) is a NEW
IDENTITY_HASH_VERSION, never an in-place re-hash; a field-composition test
pins exactly these fields. Shared by the conformance-claim body and the
ui-inspection recipe body. When the S1 ``Surface`` entity lands (3A-5),
this natural key REMAINS the hashed identity permanently; the entity
linkage arrives as an identity-EXCLUDED operational field.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

# The frozen v1 composition, in canonical order. The canonicalizer and the
# field-composition test both read THIS tuple — one source of truth.
SURFACE_KEY_FIELDS_V1 = (
    "site", "path", "persona_scope", "record_context_ref", "viewport")


class SurfaceNaturalKey(BaseModel):
    """The declared-surface identity: what FND-01 calls a surface."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    site: str
    """The Experience Cloud site host (canonicalised lowercase)."""

    path: str
    """The page path within the site."""

    persona_scope: str
    """The persona the surface is declared for (persona-scoped identity)."""

    record_context_ref: Optional[str] = None
    """Declared record-context reference, when the page renders a record."""

    viewport: Optional[str] = None
    """ONLY where the criterion makes viewport semantic (FND-01b);
    absent otherwise and excluded from the canonical string as '-'."""


def canonical_surface_key(surface: "SurfaceNaturalKey") -> str:
    """The FROZEN v1 canonical string over the five D2 fields:
    host lowercased, path with a leading '/' and no trailing '/',
    absent components as '-'. Changing these rules is a new
    IDENTITY_HASH_VERSION."""
    site = surface.site.strip().lower()
    path = surface.path.strip()
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    parts = [site, path, surface.persona_scope.strip(),
             surface.record_context_ref or "-",
             surface.viewport or "-"]
    return "|".join(parts)

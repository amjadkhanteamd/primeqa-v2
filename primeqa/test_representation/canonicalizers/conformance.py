"""Custom canonicalizer for ConformanceClaimBody (LLD 3A-2 §b).

Identity content = ``{plimsol_rule_id, surface_key}`` where surface_key is
the FROZEN v1 canonical string over the five D2 fields (site | path |
persona_scope | record_context_ref | viewport-when-semantic). The natural
key IS the semantic identity per FND-01 — deliberately NOT entity-re-keyed:
when the S1 Surface entity lands (3A-5) it arrives as an identity-EXCLUDED
operational field and this canonical form never changes. Any change to the
field composition or normalisation is a NEW IDENTITY_HASH_VERSION.
"""
from __future__ import annotations

from typing import Any

from primeqa.test_representation.canonicalizers import register_canonicalizer
from primeqa.test_representation.models.claims.ui.conformance_claim import (
    ConformanceClaimBody,
)
from primeqa.test_representation.models.surface import canonical_surface_key


@register_canonicalizer(
    ConformanceClaimBody,
    1,
    description=(
        "3A-2/DE-02: identity = plimsol_rule_id × surface natural key. "
        "Collapses the SurfaceNaturalKey value object to the frozen v1 "
        "canonical string (five D2 fields, host lowercased, path "
        "slash-normalised, absent components as '-') so the surface "
        "identity is byte-stable across the 3A-5 Surface-entity landing "
        "and across dict-order/representation differences."
    ),
)
def canonicalize_conformance(body: ConformanceClaimBody) -> dict[str, Any]:
    return {
        "kind": body.kind,
        "plimsol_rule_id": body.plimsol_rule_id,
        "surface_key": canonical_surface_key(body.surface),
    }

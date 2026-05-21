"""Semantic-completeness registry (D-097.3, claim-kind-general per D-098.5).

The single centralized authority for "does this claim_kind have a Layer 2?" —
a deeper semantic verification (formula-style / runtime-firing) that is
*defined but unbuilt*. The conditional plausibility caveat (D-096.2 / D-097.3)
reads ONLY from here; it is claim_kind-derived, never a new
``AdmissibilityLayer`` enum value, and never scattered across emission code.

Distinct axes (D-097.3):
  - ``admissibility_layer`` (the MARKER) records how deep grounding actually
    went — universal, on every artifact.
  - ``has_layer_2`` (this registry) records whether deeper unimplemented
    semantics EXIST for the claim_kind — drives the conditional caveat.

A Layer-1-complete claim_kind (config existence / property / metadata-
relationship; value-claim positive — no deeper layer) → ``has_layer_2=False``
→ marker, no caveat. A Layer-1-plausible claim_kind (`data_behavior` negative —
Layer 2 defined, parser-deferred) → ``has_layer_2=True`` → marker + mandatory
caveat.

**Claim-kind-general by construction (D-098.5 gravity guard).** The registry
spans archetypes; configuration metadata-relationship just happens to be
``False``. Behavioral kinds slot in as ``True`` later with no config-shaped
retrofit — keeping behavioral semantic verification, not config existence, the
long-term center of gravity.
"""
from __future__ import annotations

# claim_kind -> a deeper Layer-2 semantic verification exists but is unbuilt.
# Sourced from D-078 (data_behavior) and D-079 (configuration).
_HAS_LAYER_2: dict[str, bool] = {
    # configuration (D-079): reading S1 metadata IS the full verification —
    # no deeper layer. Layer-1-complete.
    "metadata-relationship-claim": False,   # the D-098.1 debut
    "existence-claim": False,
    "property-claim": False,
    # data_behavior (D-078):
    "value-claim": False,                   # type + permission IS the verification
    "prohibition-claim": True,              # Layer 2 = formula confirms rejection (parser-deferred)
    "state-transition-claim": True,         # Layer 2 = formula (parser-deferred)
    "automation-effect-claim": True,        # Layer 2 = runtime-firing verification (unbuilt)
}


def has_layer_2(claim_kind: str) -> bool:
    """Whether ``claim_kind`` has a deeper, defined-but-unbuilt Layer-2 semantic
    verification. Unknown claim_kinds default to ``True`` — fail toward honesty
    (emit the caveat rather than oversell)."""
    return _HAS_LAYER_2.get(claim_kind, True)


def requires_caveat(claim_kind: str) -> bool:
    """The conditional plausibility caveat (D-096.2 / D-097.3) is required iff a
    deeper Layer 2 exists for this claim_kind. The ONLY caveat authority."""
    return has_layer_2(claim_kind)

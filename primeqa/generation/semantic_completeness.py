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
Layer 2 defined) → ``has_layer_2=True`` → marker + caveat UNLESS that layer was
discharged for this emission (D-107: the VR formula parsed and a violating value
was derived with certainty → a verified negative, marker LAYER_2, no caveat).
The structural fact (a Layer 2 EXISTS) lives here; the per-emission discharge is
the ``verified`` argument the caller passes.

**Claim-kind-general by construction (D-098.5 gravity guard).** The registry
spans archetypes; configuration metadata-relationship just happens to be
``False``. Behavioral kinds slot in as ``True`` later with no config-shaped
retrofit — keeping behavioral semantic verification, not config existence, the
long-term center of gravity.
"""
from __future__ import annotations

from typing import Optional

from primeqa.generation.enums import CaveatKind

# claim_kind -> a deeper Layer-2 semantic verification exists but is unbuilt.
# Sourced from D-078 (data_behavior) and D-079 (configuration).
_HAS_LAYER_2: dict[str, bool] = {
    # configuration (D-079): reading S1 metadata IS the full verification —
    # no deeper layer. Layer-1-complete.
    "metadata-relationship-claim": False,   # the D-098.1 debut
    "existence-claim": False,
    "property-claim": False,
    # permission (D-080): the configured grant edge IS the verification — no Layer 2.
    "capability-claim": False,
    # ui (D-081): the configured INCLUDES_FIELD placement IS the verification — no Layer 2.
    "layout-claim": False,
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


def requires_caveat(claim_kind: str, verified: bool = False) -> bool:
    """The conditional plausibility caveat (D-096.2 / D-097.3) is required iff a
    deeper Layer 2 exists for this claim_kind AND it was not discharged for this
    emission. ``verified=True`` (D-107) means the deeper layer was actually
    satisfied — the VR formula parsed and a violating value was derived with
    certainty — so the negative is verified and carries no caveat. Default
    ``verified=False`` preserves every existing caller (config is Layer-1-
    complete; an unverified negative stays caveated). The ONLY caveat authority."""
    return has_layer_2(claim_kind) and not verified


def caveat_kind(claim_kind: str, verified: bool = False) -> Optional[CaveatKind]:
    """The typed caveat posture for ``claim_kind``, or ``None`` when no caveat
    is required (D-101.3) — including when ``verified=True`` discharged the deeper
    layer for this emission (D-107). Sole authority alongside
    :func:`requires_caveat` — the persisted ``caveat_kind`` is this registry
    verdict, never re-derived.

    v1 yields one kind for every still-caveated Layer-1-plausible claim_kind: the
    deeper verification layer (Layer 2) is defined but unparsed for THIS emission
    (D-100). Future causes (partial grounding, runtime approximation) map to
    distinct kinds here without touching callers."""
    if not requires_caveat(claim_kind, verified):
        return None
    return CaveatKind.DEEPER_VERIFICATION_LAYER_UNPARSED

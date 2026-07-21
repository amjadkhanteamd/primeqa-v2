"""ResolvedGraph — the evidence-carrying output of resolution.

Grades are discrete and evidence-derived (never probabilities):

- ``BOUND_UNIQUE`` — a strictly dominant winner with structural support
  (>=1 bound sub-mention) or an exact api/label match.
- ``BOUND_WEAK`` — a strictly dominant winner on lexical evidence alone (the
  graph gave nothing to constrain with). Consumers must treat this as
  advisory, never load-bearing.
- ``AMBIGUOUS`` — >=2 non-dominated candidates. Resolution NEVER picks on a
  tie; the candidates (with evidence) are the disclosure payload.
- ``UNRESOLVED`` — no candidate cleared admission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

BOUND_UNIQUE = "bound_unique"
BOUND_WEAK = "bound_weak"
AMBIGUOUS = "ambiguous"
UNRESOLVED = "unresolved"
GRADES = (BOUND_UNIQUE, BOUND_WEAK, AMBIGUOUS, UNRESOLVED)


@dataclass(frozen=True)
class CandidateEvidence:
    """One candidate with its per-signal feature breakdown (the audit record —
    scores are shown, never collapsed into an opaque scalar)."""

    sf_api_name: str
    entity_type: str
    features: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"sf_api_name": self.sf_api_name,
                "entity_type": self.entity_type,
                "features": dict(self.features)}


@dataclass(frozen=True)
class Binding:
    """One node's resolution outcome. ``structural_coverage`` is
    ``(bound, total)`` over the node's dependent sub-mentions (attributes +
    states for an entity node)."""

    node_id: str
    grade: str
    entity_type: Optional[str] = None
    sf_api_name: Optional[str] = None
    entity_id: Optional[UUID] = None
    matched_via: Optional[str] = None
    structural_coverage: tuple[int, int] = (0, 0)
    candidates: tuple[CandidateEvidence, ...] = field(default=())

    def to_json(self) -> dict:
        return {
            "node_id": self.node_id,
            "grade": self.grade,
            "entity_type": self.entity_type,
            "sf_api_name": self.sf_api_name,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "matched_via": self.matched_via,
            "structural_coverage": list(self.structural_coverage),
            "candidates": [c.to_json() for c in self.candidates],
        }


@dataclass(frozen=True)
class ResolvedGraph:
    """All node bindings, pinned to the snapshot they were computed against.
    ``structural_coverage`` echoes the primary entity node's coverage."""

    bindings: dict[str, Binding]
    structural_coverage: tuple[int, int]
    s1_version_seq: Optional[int] = None
    connected_org_id: Optional[UUID] = None

    def binding(self, node_id: str) -> Optional[Binding]:
        return self.bindings.get(node_id)

    def to_json(self) -> dict:
        return {
            "bindings": {k: b.to_json() for k, b in sorted(self.bindings.items())},
            "structural_coverage": list(self.structural_coverage),
            "s1_version_seq": self.s1_version_seq,
            "connected_org_id": (str(self.connected_org_id)
                                 if self.connected_org_id else None),
        }

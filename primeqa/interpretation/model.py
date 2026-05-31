"""The Interpretation — S6's structured output (SPEC §3, D-111 slice 1).

S6-owned, evidence-referencing, and therefore reviewable / editable / versionable
(the S2-claim lifecycle discipline, one substrate over). It is the *meaning* of an
S4 run: the outcome **carried verbatim** (never recomputed — S4 owns the outcome),
a semantic **verdict** + **attribution** derived deterministically from the
evidence, and **evidence refs** pointing back into the `RunEvidence` so the
interpretation is auditable, not opaque.

Slice 1 is produce-only (no persistence — mirrors how the S4 executor started).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
from uuid import UUID

# The semantic verdict taxonomy (slice 1) — what an S4 outcome *means* for the
# requirement. Closed at v1; grows with recipe kinds / deeper attribution.
Verdict = Literal[
    # behavioral negative (data-recipe)
    "prohibition_enforced",          # passed: the violating create was rejected as asserted
    "prohibition_not_enforced",      # failed + create succeeded: the rule did NOT block it (a defect)
    "rejected_unasserted_reason",    # failed + rejected, but not with the asserted error_code
    # inspection (metadata-recipe)
    "asserted_metadata_present",     # passed: the asserted relationship/metadata is there
    "asserted_metadata_absent",      # failed: it is not
    # both verticals
    "not_evaluated",                 # errored: the run couldn't be evaluated
]


@dataclass(frozen=True)
class EvidenceRef:
    """A pointer into the interpreted ``RunEvidence`` backing a claim of the
    attribution — auditable provenance, not a copy. ``step_id`` is None for a
    run-level reference (e.g. the top-level error surface)."""

    step_id: Optional[str]
    detail: str                      # what at that location supports the verdict


@dataclass(frozen=True)
class Interpretation:
    """One S6 interpretation of one S4 run.

    ``outcome`` is **carried from S4, not recomputed** (S6 restates the truth, it
    does not re-judge it). ``verdict`` + ``attribution`` are the deterministic
    meaning; ``evidence_refs`` cite the evidence that backs them."""

    run_id: UUID
    recipe_id: UUID
    claim_test_id: UUID
    # carried verbatim from RunEvidence — S6 never re-derives the outcome.
    outcome: Literal["passed", "failed", "errored"]
    verdict: Verdict
    # a deterministic, evidence-derived explanation (what + why) — never generated.
    attribution: str
    evidence_refs: tuple[EvidenceRef, ...] = field(default_factory=tuple)

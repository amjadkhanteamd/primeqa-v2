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
    # behavioral negative (data-recipe, negative)
    "prohibition_enforced",          # passed: the violating create was rejected as asserted
    "prohibition_not_enforced",      # failed + create succeeded: the rule did NOT block it (a defect)
    "rejected_unasserted_reason",    # failed + rejected, but not with the asserted error_code
    # positive value-claim (data-recipe, positive create-and-verify — D-136)
    "value_persisted",               # passed: the create succeeded + the read-back value matched the assertion
    "value_not_persisted",           # failed: created, but the read-back value did not match the assertion
    # inspection — presence (metadata-recipe, `exists` assert: existence + metadata-relationship)
    "asserted_metadata_present",     # passed: the asserted relationship/metadata is there
    "asserted_metadata_absent",      # failed: it is not
    # inspection — value (metadata-recipe, `equals`/`is_null` assert: property — D-136)
    "asserted_value_matches",        # passed: the subject's metadata value equals the assertion
    "asserted_value_differs",        # failed: it does not
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


# The structured deeper-attribution cause (D-111.1, slice 2) — the *why* behind
# a failed behavioral verdict, derived deterministically from S1's VR metadata.
CauseKind = Literal[
    "vr_inactive",               # prohibition_not_enforced: the grounding VR is disabled
    "vr_formula_drift",          # prohibition_not_enforced: an active VR's current formula is EVALUABLE but not violated (VR edited)
    "vr_formula_indeterminate",  # prohibition_not_enforced: the current formula could NOT be evaluated on the payload (org-state / unset fields) — indeterminate, the rule may have been edited
    "no_active_vr",              # prohibition_not_enforced: no active VR on the object enforces the prohibition (removed / deactivated) — matches S8's no_active_vr
    "enforcement_gap",           # prohibition_not_enforced: VR active + current formula violated, yet the create succeeded (the defect)
    "other_vr_fired",            # rejected_unasserted_reason: a different validation rule rejected it
    "platform_constraint",       # rejected_unasserted_reason: a platform rule (not a VR) rejected it
]


@dataclass(frozen=True)
class Cause:
    """The structured deeper-attribution cause attached by ``attribute_run``
    (slice 2). Machine-structured companion to the prose ``attribution`` — so an
    interpretation is queryable / clusterable downstream. ``vr_name`` is the
    implicated validation rule when a specific one is identified."""

    cause_kind: CauseKind
    vr_name: Optional[str] = None
    detail: Optional[str] = None


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
    # the structured deeper-attribution cause (slice 2 enrichment); None until
    # ``attribute_run`` enriches a failed behavioral verdict.
    cause: Optional[Cause] = None

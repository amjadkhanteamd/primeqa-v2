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
    # positive state-transition (data-recipe, observe-the-org — D-210)
    "state_transitioned",            # passed: the org moved the record to the asserted to-state
    "state_not_transitioned",        # failed: the org did NOT produce the asserted to-state
    # positive automation-effect (data-recipe, observe-the-org — D-210)
    "automation_triggered",          # passed: the automation produced the asserted effect
    "automation_not_triggered",
    "creation_accepted",
    "creation_rejected",      # failed: the asserted effect never materialized
    # acceptance, update-op (D-306) — the CHANGE, not the creation, is the case
    "change_accepted",               # passed: the org accepted the asserted update
    "change_rejected",               # failed: the org refused a change that must succeed
    # acceptance, either op (D-306.1): the org ACCEPTED the mutation but the
    # record did not verify on read-back (e.g. an org automation removed it) —
    # never worded as a rejection (the review's fabricated-refusal fix)
    "acceptance_not_verified",
    # automation-effect, the ABSENCE mirror (D-307)
    "automation_absence_confirmed",  # passed: the automation correctly produced nothing
    "automation_fired_unexpectedly", # failed: it produced a record it must not
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
    # D-229: positive-vertical failure causes (automation/state + value-claim)
    "automation_inactive",       # automation_not_triggered / state_not_transitioned: no ACTIVE Flow triggers on the object — the grounding automation was deactivated or removed
    "automation_effect_absent",  # automation_not_triggered / state_not_transitioned: an active Flow triggers, but the asserted effect did not materialize (entry condition unmet / Flow logic changed)
    "field_not_createable",      # value_not_persisted: the asserted field is not createable in current S1 — SF dropped the posted value on insert
    "before_save_automation_overwrote",  # D-241: value_not_persisted with a CREATEABLE field — an active before-save Flow on the object overwrote the posted value before insert
    "grounding_incomplete",      # D-382 (SUB-4): the DECIDING S1 metadata is UNKNOWN (missing detail row) — attribution says "I don't know" instead of fabricating an active/createable state
    # D-425: value-aware splits of automation_effect_absent, decided from the
    # D-424 assert-evidence envelope. Pre-D-424 evidence (no envelope) keeps
    # emitting the unsplit automation_effect_absent hedge, byte-identically.
    "automation_effect_record_absent",  # the effect RECORD was never produced (observed_kind=no_row / exists observed 0) — decidable WHAT; WHY stays open (S1 carries no entry criteria)
    "automation_effect_divergent",      # a DIFFERENT value/count was observed — something wrote other than asserted; WHO is not provable from S4 (candidate writers enumerated; Apex triggers uncaptured in S1)
    "automation_effect_value_absent",   # row present, asserted field holds no value — the effect VALUE is absent; never-written vs written-blank is NOT decidable from one post-state read (no firing claim)
    "representation_mismatch",          # the asserted value is a human label where the field holds a Salesforce Id (the 0d81c6f9 specimen; D-399's invented-value species) — a claim-authoring defect (S3), not org behaviour
    # D-427: the absence mirror (automation_fired_unexpectedly, D-307) —
    # direction-correct causes; NEVER worded as a missing effect.
    "automation_effect_record_present",  # a correlated record was observed where the claim asserts none should exist; the bound automation is active — whether IT fired against the asserted suppression or another writer produced the record is not decidable from the run
    "other_writer_produced_record",      # the DECIDABLE sub-cause: the bound automation is inactive/retargeted, so it CANNOT have produced the observed record — another writer did (candidates enumerated from S1; Apex triggers are uncaptured, so never exhaustive)
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

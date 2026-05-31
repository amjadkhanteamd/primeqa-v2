"""The deterministic interpreter (SPEC §2/§4, D-111 slice 1).

`interpret_run(RunEvidence) → Interpretation`: a **pure, deterministic** mapping
from S4's captured truth to a semantic interpretation. No LLM. The attribution is
*derived from the evidence*, never generated — S6 reads what the evidence shows,
it does not guess. The outcome is **carried verbatim** from S4 (S6 never
re-judges it).

It dispatches on the vertical (a `CreateAttemptEvidence` step → behavioral
negative; `Read`/`AssertEvidence` → inspection) and the run outcome, reading the
step fields S4 recorded to produce the verdict + attribution + evidence refs.
"""
from __future__ import annotations

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CreateAttemptEvidence,
    RunEvidence,
)
from primeqa.interpretation.model import EvidenceRef, Interpretation


def interpret_run(evidence: RunEvidence) -> Interpretation:
    """Interpret one S4 run deterministically. ``evidence`` is the real
    `RunEvidence` S4 produced; the returned `Interpretation` carries the
    outcome verbatim + a semantic verdict + an evidence-derived attribution."""
    create = _create_step(evidence)
    if create is not None:
        verdict, attribution, refs = _interpret_behavioral(evidence, create)
    else:
        verdict, attribution, refs = _interpret_inspection(evidence)

    return Interpretation(
        run_id=evidence.run_id,
        recipe_id=evidence.recipe_id,
        claim_test_id=evidence.claim_test_id,
        outcome=evidence.outcome,         # carried, not recomputed
        verdict=verdict,
        attribution=attribution,
        evidence_refs=refs,
    )


# ---------------------------------------------------------------------------
# Behavioral negative (data-recipe)
# ---------------------------------------------------------------------------

def _interpret_behavioral(evidence: RunEvidence, create: CreateAttemptEvidence):
    if evidence.outcome == "errored":
        return _not_evaluated(evidence, create.step_id)

    if evidence.outcome == "passed":
        codes = _codes(create)
        return (
            "prohibition_enforced",
            (f"The violating create on {create.sobject} was rejected as asserted "
             f"(matched {create.error_code}). The prohibition enforces."),
            (EvidenceRef(create.step_id,
                         f"create rejected, http {create.http_status}, "
                         f"matched={create.matched}, codes={codes}"),),
        )

    # outcome == "failed" — two distinct shapes.
    if create.success:
        return (
            "prohibition_not_enforced",
            (f"The violating create on {create.sobject} SUCCEEDED — the org did "
             f"not reject it. The asserted prohibition did not enforce (a defect)."),
            (EvidenceRef(create.step_id,
                         f"create succeeded (http {create.http_status}), "
                         f"record_id={create.cleanup.record_id}"),),
        )
    codes = _codes(create)
    return (
        "rejected_unasserted_reason",
        (f"The create on {create.sobject} was rejected, but not with the asserted "
         f"code ({create.error_code or 'n/a'}); the org returned {codes}. The "
         f"prohibition's specific rejection was not verified."),
        (EvidenceRef(create.step_id,
                     f"rejected http {create.http_status}, matched=False, "
                     f"codes={codes}"),),
    )


# ---------------------------------------------------------------------------
# Inspection (metadata-recipe)
# ---------------------------------------------------------------------------

def _interpret_inspection(evidence: RunEvidence):
    if evidence.outcome == "errored":
        return _not_evaluated(evidence, _first_step_id(evidence))

    assertion = _assert_step(evidence)
    subject = _read_subject(evidence)
    if evidence.outcome == "passed":
        return (
            "asserted_metadata_present",
            (f"The asserted metadata for {subject} is present "
             f"(the inspection read returned it)."),
            _inspection_refs(evidence, assertion),
        )
    return (
        "asserted_metadata_absent",
        (f"The asserted metadata for {subject} is absent "
         f"(the inspection read returned nothing)."),
        _inspection_refs(evidence, assertion),
    )


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def _not_evaluated(evidence: RunEvidence, step_id):
    err = evidence.error
    detail = (f"{err.phase}: {err.error_type}: {err.message}" if err is not None
              else "errored (no error surface captured)")
    return (
        "not_evaluated",
        f"The run could not be evaluated against the org ({detail}).",
        (EvidenceRef(step_id, detail),),
    )


def _create_step(evidence: RunEvidence):
    for s in evidence.steps:
        if isinstance(s, CreateAttemptEvidence):
            return s
    return None


def _assert_step(evidence: RunEvidence):
    for s in evidence.steps:
        if isinstance(s, AssertEvidence):
            return s
    return None


def _read_subject(evidence: RunEvidence) -> str:
    for s in evidence.steps:
        if getattr(s, "kind", None) == "read":
            return f"{s.subject_entity_type} {s.subject_external_id}"
    return "the subject"


def _first_step_id(evidence: RunEvidence):
    return evidence.steps[0].step_id if evidence.steps else None


def _inspection_refs(evidence: RunEvidence, assertion):
    refs = []
    for s in evidence.steps:
        if getattr(s, "kind", None) == "read":
            refs.append(EvidenceRef(s.step_id, f"read returned {s.row_count} row(s)"))
    if assertion is not None:
        refs.append(EvidenceRef(
            assertion.step_id,
            f"assert {assertion.predicate} held={assertion.held}"))
    return tuple(refs)


def _codes(create: CreateAttemptEvidence) -> list:
    return [e.get("errorCode") for e in create.rejection_body if isinstance(e, dict)]

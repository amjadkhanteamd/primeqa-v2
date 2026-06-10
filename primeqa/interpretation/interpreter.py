"""The deterministic interpreter (SPEC §2/§4, D-111 slice 1).

`interpret_run(RunEvidence) → Interpretation`: a **pure, deterministic** mapping
from S4's captured truth to a semantic interpretation. No LLM. The attribution is
*derived from the evidence*, never generated — S6 reads what the evidence shows,
it does not guess. The outcome is **carried verbatim** from S4 (S6 never
re-judges it).

It dispatches on the vertical — the **rejection-bearing mutation step**
(`Update`/`DeleteAttemptEvidence`, D-203) → behavioral negative graded against
THAT step (never the setup create); a `CreateAttemptEvidence` step → behavioral
negative or positive (an assert alongside discriminates); `Read`/`AssertEvidence`
→ inspection — and the run outcome, reading the step fields S4 recorded to
produce the verdict + attribution + evidence refs.
"""
from __future__ import annotations

from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CreateAttemptEvidence,
    DeleteAttemptEvidence,
    RunEvidence,
    UpdateAttemptEvidence,
)
from primeqa.interpretation.model import EvidenceRef, Interpretation


def interpret_run(evidence: RunEvidence) -> Interpretation:
    """Interpret one S4 run deterministically. ``evidence`` is the real
    `RunEvidence` S4 produced; the returned `Interpretation` carries the
    outcome verbatim + a semantic verdict + an evidence-derived attribution."""
    mutation = _mutation_step(evidence)
    create = _create_step(evidence)
    assertion = _assert_step(evidence)
    if mutation is not None:
        # 2-step behavioral negative (D-203): graded against the rejected
        # MUTATION, never the setup create (which succeeded by construction —
        # grading it would falsely read prohibition_not_enforced).
        verdict, attribution, refs = _interpret_behavioral(evidence, mutation)
    elif create is not None and assertion is not None:
        # positive create-and-verify (D-136): create-expect-success → read-back →
        # value assert. The 1-step negative emits a *single* create step (no
        # assert), so the presence of an assert alongside is the discriminator.
        verdict, attribution, refs = _interpret_positive(evidence, create, assertion)
    elif create is not None:
        # 1-step behavioral negative: a single create the org should reject.
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

def _interpret_behavioral(evidence: RunEvidence, step):
    """Grade the rejection-bearing step — a flagged create (D-110.2) or a
    flagged update/delete (D-203). All three evidence kinds carry the fields
    read here (sobject, http_status, matched, success, error_code,
    rejection_body); ``op`` words the attribution per operation."""
    op = step.kind
    if evidence.outcome == "errored":
        return _not_evaluated(evidence, step.step_id)

    if evidence.outcome == "passed":
        codes = _codes(step)
        return (
            "prohibition_enforced",
            (f"The violating {op} on {step.sobject} was rejected as asserted "
             f"(matched {step.error_code}). The prohibition enforces."),
            (EvidenceRef(step.step_id,
                         f"{op} rejected, http {step.http_status}, "
                         f"matched={step.matched}, codes={codes}"),),
        )

    # outcome == "failed" — two distinct shapes.
    if step.success:
        return (
            "prohibition_not_enforced",
            (f"The violating {op} on {step.sobject} SUCCEEDED — the org did "
             f"not reject it. The asserted prohibition did not enforce (a defect)."),
            (EvidenceRef(step.step_id,
                         f"{op} succeeded (http {step.http_status}), "
                         f"record_id={_record_id(step)}"),),
        )
    codes = _codes(step)
    return (
        "rejected_unasserted_reason",
        (f"The {op} on {step.sobject} was rejected, but not with the asserted "
         f"code ({step.error_code or 'n/a'}); the org returned {codes}. The "
         f"prohibition's specific rejection was not verified."),
        (EvidenceRef(step.step_id,
                     f"rejected http {step.http_status}, matched=False, "
                     f"codes={codes}"),),
    )


# ---------------------------------------------------------------------------
# Positive value-claim (data-recipe, create-and-verify) — D-136
# ---------------------------------------------------------------------------

def _interpret_positive(evidence: RunEvidence, create: CreateAttemptEvidence, assertion):
    """The positive create-and-verify (D-115): a record is created, read back, and
    its value asserted. ``passed`` → the value persisted; ``failed`` → created but
    the read-back value differs; ``errored`` → the create failed or the read-back
    couldn't be evaluated."""
    if evidence.outcome == "errored":
        return _not_evaluated(evidence, create.step_id)

    refs = (
        EvidenceRef(create.step_id, f"create succeeded (http {create.http_status})"),
        EvidenceRef(assertion.step_id,
                    f"assert {assertion.predicate} held={assertion.held}"),
    )
    if evidence.outcome == "passed":
        return (
            "value_persisted",
            (f"A record was created on {create.sobject} and the read-back confirmed the "
             f"asserted value (assert {assertion.predicate} held). The value persists."),
            refs,
        )
    return (
        "value_not_persisted",
        (f"A record was created on {create.sobject}, but the read-back value did not "
         f"match the assertion (assert {assertion.predicate} held=False). The value did "
         f"not persist as asserted."),
        refs,
    )


# ---------------------------------------------------------------------------
# Inspection (metadata-recipe) — presence (`exists`) vs value (`equals`/`is_null`)
# ---------------------------------------------------------------------------

# Inspection assert predicates that check a metadata VALUE (property), not mere
# presence (existence / metadata-relationship). D-136.
_VALUE_PREDICATES = frozenset({"equals", "is_null"})


def _interpret_inspection(evidence: RunEvidence):
    if evidence.outcome == "errored":
        return _not_evaluated(evidence, _first_step_id(evidence))

    assertion = _assert_step(evidence)
    subject = _read_subject(evidence)
    refs = _inspection_refs(evidence, assertion)
    predicate = assertion.predicate if assertion is not None else "exists"

    # property — a value assert (equals / is_null): the field is present; its VALUE
    # is what was checked, so a mismatch is "value differs", not "metadata absent".
    if predicate in _VALUE_PREDICATES:
        if evidence.outcome == "passed":
            return (
                "asserted_value_matches",
                (f"The asserted value for {subject} matches "
                 f"(the inspection read {predicate!r} held)."),
                refs,
            )
        return (
            "asserted_value_differs",
            (f"The asserted value for {subject} differs "
             f"(the inspection read {predicate!r} did not hold)."),
            refs,
        )

    # presence — existence / metadata-relationship (`exists`).
    if evidence.outcome == "passed":
        return (
            "asserted_metadata_present",
            (f"The asserted metadata for {subject} is present "
             f"(the inspection read returned it)."),
            refs,
        )
    return (
        "asserted_metadata_absent",
        (f"The asserted metadata for {subject} is absent "
         f"(the inspection read returned nothing)."),
        refs,
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


def _mutation_step(evidence: RunEvidence):
    """The rejection-bearing update/delete attempt of a 2-step negative
    (D-203), if present."""
    for s in evidence.steps:
        if isinstance(s, (UpdateAttemptEvidence, DeleteAttemptEvidence)):
            return s
    return None


def _record_id(step) -> object:
    """The record a not-enforced mutation acted on: a create cleans up the
    record it made (cleanup.record_id); an update/delete carries the subject's
    id directly."""
    if isinstance(step, CreateAttemptEvidence):
        return step.cleanup.record_id
    return step.record_id


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


def _codes(step) -> list:
    return [e.get("errorCode") for e in step.rejection_body if isinstance(e, dict)]

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


def interpret_run(evidence: RunEvidence,
                  claim_kind: str | None = None) -> Interpretation:
    """Interpret one S4 run deterministically. ``evidence`` is the real
    `RunEvidence` S4 produced; the returned `Interpretation` carries the
    outcome verbatim + a semantic verdict + an evidence-derived attribution.

    ``claim_kind`` selects the positive vertical's verdict VOCABULARY (D-210):
    the create→read→assert evidence shape is shared by value-claim (the user's
    value persisting), state-transition (the ORG setting the to-state), and
    automation-effect (the automation producing its effect) — evidence alone
    cannot tell them apart. ``None`` keeps the value-claim vocabulary (every
    pre-D-210 caller)."""
    mutation = _mutation_step(evidence)
    create = _create_step(evidence)
    assertion = _assert_step(evidence)
    if create is not None and assertion is not None:
        # positive create-and-verify (D-136), including the D-306 update-
        # observe chain: a run that REACHED its assert grades on the assert —
        # a mid-chain positive update is never the graded step, and negatives
        # never carry an assert, so this branch dispatches FIRST. The
        # update-op acceptance picks the change vocabulary (the case is the
        # CHANGE, not the creation).
        vocab_kind = claim_kind
        if claim_kind == "acceptance-claim" and mutation is not None:
            vocab_kind = "acceptance-claim@update"
        verdict, attribution, refs = _interpret_positive(
            evidence, create, assertion, claim_kind=vocab_kind)
    elif (mutation is not None and claim_kind == "acceptance-claim"
            and not mutation.success and evidence.outcome != "passed"):
        # D-306: the FAILED-AT-UPDATE acceptance shape (create + update
        # evidence, no assert) — the org refused the CHANGE the requirement
        # says must succeed; the claim direction is INVERTED vs D-203.
        # Guarded like the create branch below (D-306.1, review SF-3): the
        # legitimate shape always carries a FAILED mutation on a non-passed
        # run — mislabeled-kind negative evidence keeps interpreting
        # behaviorally.
        verdict, attribution, refs = _interpret_acceptance_rejected(
            evidence, mutation)
    elif mutation is not None:
        # 2-step behavioral negative (D-203): graded against the rejected
        # MUTATION, never the setup create (which succeeded by construction —
        # grading it would falsely read prohibition_not_enforced). An ERRORED
        # positive update-observe run also lands here — behavioral grading of
        # an errored outcome is not_evaluated, the correct verdict for it.
        verdict, attribution, refs = _interpret_behavioral(evidence, mutation)
    elif (create is not None and claim_kind in _POSITIVE_VOCAB
            and evidence.outcome != "passed"):
        # D-305.1 (review B2) / D-306 live-proof fix: create-only evidence on
        # a POSITIVE claim kind is a FAILED-AT-CREATE run (an acceptance case
        # the org refused, or a rejected staging create graded under
        # expect_acceptance) — the direction is INVERTED vs the 1-step
        # negative: the org rejecting IS the finding. Grading it behavioral
        # produced prohibition prose on positive claims. A PASSED create-only
        # run cannot be a positive shape (positives pass through their
        # assert), so it falls through to the behavioral branch unchanged.
        # D-306.1 (review): grade the FAILED create, not the first — on a
        # multi-create chain (D-227 parent-stamp) the first create is the
        # successful parent, and citing it produced self-contradictory refs
        # ("create rejected (http 201)") while dropping the real rejection.
        failed_create = next(
            (s for s in evidence.steps
             if isinstance(s, CreateAttemptEvidence) and not s.success),
            create)
        verdict, attribution, refs = _interpret_acceptance_rejected(
            evidence, failed_create, claim_kind=claim_kind)
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

# D-210: the positive vertical's verdict vocabulary + attribution wording,
# keyed by claim_kind. The evidence shape is identical across the three; only
# WHO was supposed to produce the observed value differs (the user's create,
# the org's state machinery, the org's automation).
_POSITIVE_VOCAB = {
    "value-claim": (
        "value_persisted", "value_not_persisted",
        "the read-back confirmed the asserted value", "The value persists.",
        "the read-back value did not match the assertion",
        "The value did not persist as asserted."),
    "state-transition-claim": (
        "state_transitioned", "state_not_transitioned",
        "the read-back confirmed the org set the asserted to-state",
        "The state transition happened.",
        "the read-back did not show the asserted to-state",
        "The org did not produce the asserted transition."),
    "automation-effect-claim": (
        "automation_triggered", "automation_not_triggered",
        "the read-back confirmed the automation's asserted effect",
        "The automation fired.",
        "the asserted effect was not observed",
        "The automation did not produce its asserted effect."),
    # D-305: the acceptance archetype — passed = the org SAVED the case. The
    # FAIL slot here is the assert-failed shape only (the org ACCEPTED the
    # mutation but the read-back did not verify — a genuinely refused
    # creation/change never reaches the assert; it grades via
    # _interpret_acceptance_rejected). D-306.1: wording it as a rejection
    # fabricated a refusal the org never issued.
    "acceptance-claim": (
        "creation_accepted", "acceptance_not_verified",
        "the org accepted the creation (the record persisted)",
        "The case saves.",
        "the org accepted the creation but the record did not verify on "
        "read-back",
        "The accepted case could not be verified."),
    # D-306: the update-op acceptance — the CHANGE, not the creation, is the
    # case (a synthetic vocab key; interpret() selects it when acceptance
    # evidence carries a positive update).
    "acceptance-claim@update": (
        "change_accepted", "acceptance_not_verified",
        "the org accepted the change (the update persisted)",
        "The change succeeds.",
        "the org accepted the change but the record did not verify on "
        "read-back",
        "The accepted change could not be verified."),
}


def _interpret_acceptance_rejected(evidence: RunEvidence, step,
                                   claim_kind: str = "acceptance-claim"):
    """D-305.1 / D-306: the failed-at-mutation shapes on POSITIVE claims — the
    org REFUSED the creation (``step`` is the create) or the CHANGE (``step``
    is the positive update) the claim's staged state needs. ``failed`` is the
    graded business finding (the expect_acceptance grade); ``errored`` stays
    not-evaluated (transport/ambiguous — the org did not business-evaluate the
    case). For an acceptance claim the refusal IS the asserted case failing;
    for the other positive kinds (D-306: a rejected staging create under
    expect_acceptance) it means the claimed behavior was never provoked —
    worded accordingly, same direction."""
    if evidence.outcome == "errored":
        return _not_evaluated(evidence, step.step_id)
    is_update = step.kind == "update"
    op = {"update": "update", "delete": "deletion"}.get(step.kind, "creation")
    codes = sorted({e.get("errorCode") for e in (step.rejection_body or ())
                    if isinstance(e, dict) and e.get("errorCode")})
    detail = (f"the org rejected the {op} (" + ", ".join(codes) + ")"
              if codes else f"the org rejected the {op}")
    refs = (EvidenceRef(step.step_id,
                        f"{step.kind} rejected (http " + str(step.http_status) +
                        "): " + detail),)
    if claim_kind == "acceptance-claim":
        tail = ". The acceptance claim does not hold."
        need = "the requirement says must save"
    else:
        tail = (". The staged state was never established, so the claimed "
                "behavior was never provoked.")
        need = "the claim's staged state needs"
    if is_update:
        return (
            "change_rejected",
            ("The org refused to update the " + step.sobject + " case — the "
             "change the requirement says must succeed — " + detail + tail),
            refs,
        )
    return (
        "creation_rejected",
        ("The org refused to create the " + step.sobject + " case " + need +
         " — " + detail + tail),
        refs,
    )


def _interpret_positive(evidence: RunEvidence, create: CreateAttemptEvidence,
                        assertion, claim_kind: str | None = None):
    """The positive create-and-verify family (D-115 / D-210): a record is
    created, a read-back observed, an assert evaluated. ``passed`` → the
    asserted state was observed; ``failed`` → it was not; ``errored`` → the
    create failed or the read-back couldn't be evaluated. ``claim_kind``
    selects the vocabulary (value-claim is the default)."""
    if evidence.outcome == "errored":
        return _not_evaluated(evidence, create.step_id)

    pass_v, fail_v, pass_why, pass_tail, fail_why, fail_tail = _POSITIVE_VOCAB.get(
        claim_kind or "value-claim", _POSITIVE_VOCAB["value-claim"])
    refs = (
        EvidenceRef(create.step_id, f"create succeeded (http {create.http_status})"),
    )
    # D-306: the update-then-observe chain's trigger event is the update — the
    # causal step the claim is ABOUT rides the provenance refs + prose.
    mutation = _mutation_step(evidence)
    changed = ""
    if mutation is not None and mutation.success:
        refs += (EvidenceRef(
            mutation.step_id,
            f"update succeeded (http {mutation.http_status})"),)
        changed = " then updated"
    refs += (EvidenceRef(assertion.step_id,
                         f"assert {assertion.predicate} held={assertion.held}"),)
    if evidence.outcome == "passed":
        return (
            pass_v,
            (f"A record was created on {create.sobject}{changed} and {pass_why} "
             f"(assert {assertion.predicate} held). {pass_tail}"),
            refs,
        )
    return (
        fail_v,
        (f"A record was created on {create.sobject}{changed}, but {fail_why} "
         f"(assert {assertion.predicate} held=False). {fail_tail}"),
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
    """An errored run → the ``not_evaluated`` verdict. D-272 Slice 1: the verdict
    is unchanged, but the attribution now carries whether the run is INDETERMINATE
    (evidence-incomplete, re-runnable — credentials / transport / throttle /
    environment-not-satisfiable) or PERMANENT (an our-side malformed / un-buildable
    test that re-running as-is repeats). The class is derived from the SAME
    ``failure_category`` the result store persists (``failure_signature``), so it
    is consistent across persist and interpret and works retroactively from the
    captured evidence (which carries ``error_type``). Neither branch is a claim
    failure and neither is ``Verified``."""
    from primeqa.execution_engine.evidence import failure_signature
    from primeqa.integrations.failure_taxonomy import is_indeterminate

    err = evidence.error
    detail = (f"{err.phase}: {err.error_type}: {err.message}" if err is not None
              else "errored (no error surface captured)")
    category, _ = failure_signature(evidence)
    if is_indeterminate(category):
        attribution = (f"The run could not be evaluated against the org ({detail}). "
                       f"The evidence is incomplete — re-run.")
    else:
        attribution = (f"The run could not be evaluated because the test itself "
                       f"could not be built or run ({detail}). Re-running as-is "
                       f"will not change this — it needs attention.")
    return (
        "not_evaluated",
        attribution,
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

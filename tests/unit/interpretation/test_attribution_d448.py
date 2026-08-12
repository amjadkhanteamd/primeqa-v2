"""D-448 pins: the ordered-fold prior state and the graded-step selection.

Direction pins: a SUCCEEDED intermediate folds into the prior; a REJECTED
intermediate changed nothing in the org and contributes NOTHING; the D-441
guard survives — as a refusal — for exactly the cases the fold cannot
resolve (unknown intermediate outcome, deleted subject, no/multiple base
creates); the graded step is the one the evidence itself marks (``matched``
is not None); and the VR05 specimen shape evaluates TRUE where the naive
create-pairing produced the wrong-direction False.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.execution_engine.evidence import (
    CleanupRecord, CreateAttemptEvidence, DeleteAttemptEvidence,
    RunEvidence, UpdateAttemptEvidence)
from primeqa.interpretation.attribution import (
    _effective_state, _eval_context, _mutation_step, _ordered_fold)
from primeqa.semantic.formula import EvalContext, evaluate, parse

pytestmark = pytest.mark.unit

_T = datetime(2026, 8, 11, tzinfo=timezone.utc)


def _create(values, ordinal=0, sobject="PLS_BM_Deal__c", record_id=None):
    return CreateAttemptEvidence(
        step_id=f"create{ordinal}", ordinal=ordinal, sobject=sobject,
        field_values=values, http_status=201, success=True, error_code=None,
        message=None, rejection_body=(), matched=None,
        cleanup=CleanupRecord(attempted=bool(record_id),
                              succeeded=True if record_id else None,
                              record_id=record_id),
        started_at=_T, finished_at=_T, duration_ms=1)


def _update(changes, ordinal=1, *, success=True, matched=None, error=None,
            sobject="PLS_BM_Deal__c", record_id="001A"):
    return UpdateAttemptEvidence(
        step_id=f"update{ordinal}", ordinal=ordinal, sobject=sobject,
        record_id=record_id, field_changes=changes,
        http_status=204 if success else 400, success=success,
        error_code=None, message=None, rejection_body=(), matched=matched,
        started_at=_T, finished_at=_T, duration_ms=1, error=error)


def _delete(ordinal, *, success, sobject="PLS_BM_Deal__c",
            record_id="001A"):
    return DeleteAttemptEvidence(
        step_id=f"delete{ordinal}", ordinal=ordinal, sobject=sobject,
        record_id=record_id, http_status=204 if success else 400,
        success=success, error_code=None, message=None, rejection_body=(),
        matched=None, started_at=_T, finished_at=_T, duration_ms=1)


def _run(steps):
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=59,
        api_choice="rest", outcome="passed", started_at=_T, finished_at=_T,
        steps=tuple(steps))


class _S1Bare:
    pass


# ---------------------------------------------------------------------------
# The specimen shape (run 495707b6): the fold repairs the D-441 wrong verdict
# ---------------------------------------------------------------------------

_VR05 = ('ISPICKVAL(PRIORVALUE(PLS_BM_Stage__c), "Approved") && '
         'ISCHANGED(PLS_BM_Deal_Value__c)')


def _specimen():
    create = _create({"PLS_BM_Stage__c": "Contract Review",
                      "PLS_BM_Deal_Value__c": 2000000.01})
    entry = _update({"PLS_BM_Stage__c": "Approved"}, ordinal=1)
    graded = _update({"PLS_BM_Deal_Value__c": 2000000.02}, ordinal=2,
                     success=False, matched=True)
    return create, entry, graded


def test_specimen_fold_yields_the_intermediate_state():
    create, entry, graded = _specimen()
    prior, refusal = _ordered_fold(_run([create, entry, graded]), graded)
    assert refusal is None
    assert prior["PLS_BM_Stage__c"] == "Approved"
    assert prior["PLS_BM_Deal_Value__c"] == 2000000.01


def test_specimen_vr05_evaluates_true_not_the_wrong_direction_false():
    create, entry, graded = _specimen()
    ev = _run([create, entry, graded])
    ctx = _eval_context(graded, ev, _S1Bare())
    post = _effective_state(graded, ev)
    verdict = evaluate(parse(_VR05), post, context=ctx)
    assert verdict is True
    # and the naive create-pairing really was the wrong direction:
    naive = EvalContext(prior_state=dict(create.field_values),
                        is_create=False)
    assert evaluate(parse(_VR05), post, context=naive) is False


def test_effective_state_includes_the_intermediate_write():
    create, entry, graded = _specimen()
    state = _effective_state(graded, _run([create, entry, graded]))
    assert state["PLS_BM_Stage__c"] == "Approved"
    assert state["PLS_BM_Deal_Value__c"] == 2000000.02


# ---------------------------------------------------------------------------
# Graded-step selection: the evidence's own marker
# ---------------------------------------------------------------------------

def test_mutation_step_picks_the_marked_step_not_the_first():
    create, entry, graded = _specimen()
    assert _mutation_step(_run([create, entry, graded])) is graded


def test_mutation_step_falls_back_to_first_when_none_marked():
    create = _create({"A__c": 1})
    u1 = _update({"A__c": 2}, ordinal=1)
    u2 = _update({"A__c": 3}, ordinal=2)
    assert _mutation_step(_run([create, u1, u2])) is u1


# ---------------------------------------------------------------------------
# What must not contribute / the guard's survivors
# ---------------------------------------------------------------------------

def test_rejected_intermediate_contributes_nothing():
    """A rejected update changed nothing in the org — SKIPPED, not folded."""
    create = _create({"Stage__c": "Draft", "V__c": 1})
    rejected = _update({"Stage__c": "Approved"}, ordinal=1, success=False)
    graded = _update({"V__c": 2}, ordinal=2, success=False, matched=True)
    prior, refusal = _ordered_fold(_run([create, rejected, graded]), graded)
    assert refusal is None
    assert prior["Stage__c"] == "Draft"          # the rejection left it


def test_unknown_intermediate_outcome_refuses():
    class _Err:
        pass
    create = _create({"A__c": 1})
    unknown = _update({"A__c": 2}, ordinal=1, success=False, error=_Err())
    graded = _update({"A__c": 3}, ordinal=2, success=False, matched=True)
    ev = _run([create, unknown, graded])
    prior, refusal = _ordered_fold(ev, graded)
    assert prior is None and "unknown" in refusal
    ctx = _eval_context(graded, ev, _S1Bare())
    assert ctx.prior_state is None and ctx.is_create is None


def test_successful_delete_of_the_subject_refuses():
    create = _create({"A__c": 1})
    gone = _delete(1, success=True)
    graded = _update({"A__c": 3}, ordinal=2, success=False, matched=True)
    prior, refusal = _ordered_fold(_run([create, gone, graded]), graded)
    assert prior is None and "delete" in refusal


def test_rejected_delete_is_skipped():
    create = _create({"A__c": 1})
    kept = _delete(1, success=False)
    graded = _update({"A__c": 3}, ordinal=2, success=False, matched=True)
    prior, refusal = _ordered_fold(_run([create, kept, graded]), graded)
    assert refusal is None and prior == {"A__c": 1}


def test_no_setup_create_refuses():
    graded = _update({"A__c": 3}, ordinal=0, success=False, matched=True)
    prior, refusal = _ordered_fold(_run([graded]), graded)
    assert prior is None and "no same-record" in refusal


def test_multiple_candidate_creates_refuse():
    c1 = _create({"A__c": 1}, ordinal=0)
    c2 = _create({"A__c": 9}, ordinal=1)
    graded = _update({"A__c": 3}, ordinal=2, success=False, matched=True)
    prior, refusal = _ordered_fold(_run([c1, c2, graded]), graded)
    assert prior is None and "ambiguous" in refusal


def test_other_object_steps_never_fold():
    parent = _create({"Name": "P"}, ordinal=0, sobject="Account")
    create = _create({"A__c": 1}, ordinal=1)
    other = _update({"X__c": 5}, ordinal=2, sobject="Account",
                    record_id="001B")
    graded = _update({"A__c": 3}, ordinal=3, success=False, matched=True)
    prior, refusal = _ordered_fold(
        _run([parent, create, other, graded]), graded)
    assert refusal is None
    assert prior == {"A__c": 1}


# ---------------------------------------------------------------------------
# The 2-step degenerate: byte-identical to the pre-D-448 shape
# ---------------------------------------------------------------------------

def test_two_step_negative_folds_to_exactly_the_setup_create():
    create = _create({"Stage__c": "Draft", "Amount__c": 5})
    graded = _update({"Stage__c": "Approved"}, ordinal=1, success=False,
                     matched=True)
    ev = _run([create, graded])
    prior, refusal = _ordered_fold(ev, graded)
    assert refusal is None and prior == dict(create.field_values)
    state = _effective_state(graded, ev)
    assert state == {"Stage__c": "Approved", "Amount__c": 5}

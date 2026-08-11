"""D-444 pins: ISPICKVAL case-only mismatch refuses (org-probed insensitive).

The env-59 two-create probe proved a staged case-variant fires an
exact-literal ISPICKVAL rule, so the old exact-match ``False`` on a
case-only difference was a live wrong-direction verdict. The guard refuses
it; exact matches and beyond-case differences keep their old truth values.
The D-337 gate's ``_text_eq`` was already tri-state — pinned here too so the
two layers cannot drift apart.
"""
import pytest

from primeqa.generation.vr_conflict import find_staged_vr_conflict
from primeqa.semantic.formula import EvalContext, NonEvaluable, evaluate, parse

pytestmark = pytest.mark.unit


def _ev(formula, payload, ctx=None):
    return evaluate(parse(formula), payload, context=ctx)


def test_exact_match_stays_true():
    assert _ev('ISPICKVAL(Status, "Critical")', {"Status": "Critical"}) is True


def test_case_only_mismatch_refuses():
    out = _ev('ISPICKVAL(Status, "Critical")', {"Status": "critical"})
    assert isinstance(out, NonEvaluable) and "case-variant" in out.reason


def test_beyond_case_difference_stays_false():
    assert _ev('ISPICKVAL(Status, "Critical")', {"Status": "Low"}) is False


def test_absent_field_stays_false():
    assert _ev('ISPICKVAL(Status, "Critical")', {}) is False


def test_priorvalue_composition_case_only_refuses():
    ctx = EvalContext(prior_state={"Status": "approved"}, is_create=False)
    out = _ev('ISPICKVAL(PRIORVALUE(Status), "Approved")',
              {"Status": "Rejected"}, ctx)
    assert isinstance(out, NonEvaluable) and "case-variant" in out.reason


def test_priorvalue_composition_exact_stays_true():
    ctx = EvalContext(prior_state={"Status": "Approved"}, is_create=False)
    assert _ev('ISPICKVAL(PRIORVALUE(Status), "Approved")',
               {"Status": "Rejected"}, ctx) is True


def test_case_only_refusal_kleene_composes():
    """The refusal stays Kleene: a True sibling still resolves an OR."""
    out = _ev('ISPICKVAL(Status, "Critical") || ISBLANK(Reason)',
              {"Status": "critical", "Reason": None})
    assert out is True


def test_gate_text_eq_already_tristate_on_case():
    """The emission gate admits (never refuses, never proves-not-fire) on a
    case-only staged variant — pre-existing D-337/D-443 soundness."""
    rules = [("R", 'ISPICKVAL(Status__c, "Critical") && ISBLANK(Reason__c)')]
    out = find_staged_vr_conflict(
        rules, {"Status__c": "critical", "Reason__c": None})
    assert out is None
    exact = find_staged_vr_conflict(
        rules, {"Status__c": "Critical", "Reason__c": None})
    assert exact is not None and "R" in exact

"""D-447 pins: the three cheap C arms — constant booleans, ISCHANGED-on-create
(resolved), and REGEX behind the four D-344 guards.

The REGEX pins are drawn from the five REAL env-59 rules, run as their STORED
formula text — including the lexer-unescape end-to-end proof (the sfFma
``"\\\\w+"`` shape) and the fullmatch-vs-search discriminator (PLS_FB_VR01's
unanchored pattern).
"""
import pytest

from primeqa.semantic.formula import EvalContext, NonEvaluable, evaluate, parse

pytestmark = pytest.mark.unit


def _ev(formula, payload, ctx=None):
    return evaluate(parse(formula), payload, context=ctx)


# ---------------------------------------------------------------------------
# (1) Constant-boolean predicates decide
# ---------------------------------------------------------------------------

def test_literal_false_provably_never_fires():
    assert _ev("false", {}) is False
    assert _ev("FALSE", {"anything": 1}) is False


def test_literal_true_provably_always_fires():
    assert _ev("TRUE", {}) is True


def test_non_boolean_bare_literal_still_refuses():
    out = _ev("0", {})
    assert isinstance(out, NonEvaluable) and "non-boolean" in out.reason


def test_dead_rule_inside_connectives_resolves():
    assert _ev("false && ISBLANK(X__c)", {}) is False
    assert _ev("false || ISBLANK(X__c)", {"X__c": None}) is True


# ---------------------------------------------------------------------------
# (2) ISCHANGED-on-create — resolved FALSE (official article, D-447)
# ---------------------------------------------------------------------------

def test_ischanged_on_create_false_makes_guarded_rules_decidable():
    """VR10's shape on a create: ISCHANGED conjunct is False -> the rule
    provably does not fire on any create."""
    ctx = EvalContext(is_create=True)
    out = _ev('ISPICKVAL(S__c, "Approved") && ISCHANGED(S__c)',
              {"S__c": "Approved"}, ctx)
    assert out is False


def test_ischanged_without_context_still_refuses():
    out = _ev("ISCHANGED(S__c)", {"S__c": "X"})
    assert isinstance(out, NonEvaluable)


# ---------------------------------------------------------------------------
# (3) REGEX — the four guards, pinned on the real rules
# ---------------------------------------------------------------------------

# PLS_BM_VR09_External_Reference (anchored)
_VR09 = ('NOT(ISBLANK(PLS_BM_External_Reference__c)) && '
         'NOT(REGEX(PLS_BM_External_Reference__c, "^EXT-[0-9]{8}$"))')
# PLS_FB_VR01_External_Ref_Format (UNANCHORED — the fullmatch discriminator)
_FBVR01 = ('NOT(ISBLANK(PLS_FB_External_Ref__c)) && '
           'NOT(REGEX(PLS_FB_External_Ref__c, "FB-[0-9]{6}"))')
# sfFma XSS_Prevention_FullName — STORED text carries the double backslash
_SFFMA = 'NOT(REGEX(sfFma__FullName__c , "\\\\w+__\\\\w+"))'


def test_vr09_valid_reference_does_not_fire():
    assert _ev(_VR09, {"PLS_BM_External_Reference__c": "EXT-12345678"}) is False


def test_vr09_malformed_reference_fires():
    assert _ev(_VR09, {"PLS_BM_External_Reference__c": "BAD-1"}) is True


def test_fullmatch_not_search_on_the_unanchored_corpus_pattern():
    """Guard (i): SF REGEX is whole-string (Java String.matches). The
    unanchored FB-[0-9]{6} must NOT match an embedded occurrence — search
    would, and would wrongly prove the rule silent."""
    assert _ev(_FBVR01, {"PLS_FB_External_Ref__c": "FB-123456"}) is False
    assert _ev(_FBVR01, {"PLS_FB_External_Ref__c": "xxFB-123456xx"}) is True


def test_lexer_unescape_end_to_end_no_double_unescape():
    """Guard (ii): the stored text ``"\\\\w+__\\\\w+"`` reaches the evaluator
    as the pattern ``\\w+__\\w+`` — compiled AS RECEIVED it matches a real
    namespaced name. A double-unescape or a raw double-backslash pattern
    would both fail this pin."""
    assert _ev(_SFFMA, {"sfFma__FullName__c": "ns__param"}) is False
    assert _ev(_SFFMA, {"sfFma__FullName__c": "plainname"}) is True


def test_blank_input_refuses_but_isblank_guard_resolves():
    """Guard (iv): blank -> NE on the bare REGEX; the corpus's own ISBLANK
    guard Kleene-resolves the full rule to False."""
    bare = _ev('REGEX(F__c, "^A$")', {"F__c": None})
    assert isinstance(bare, NonEvaluable) and "blank" in bare.reason
    assert _ev(_VR09, {"PLS_BM_External_Reference__c": None}) is False


def test_python_uncompilable_pattern_refuses_never_raises():
    out = _ev('REGEX(F__c, "(*")', {"F__c": "x"})
    assert isinstance(out, NonEvaluable) and "does not compile" in out.reason
    java_only = _ev('REGEX(F__c, "\\\\p{Lu}+")', {"F__c": "X"})
    assert isinstance(java_only, NonEvaluable)


def test_java_class_intersection_refuses_silent_divergence():
    out = _ev('REGEX(F__c, "[a-z&&[^bc]]")', {"F__c": "a"})
    assert isinstance(out, NonEvaluable) and "intersection" in out.reason


def test_non_literal_pattern_refuses():
    out = _ev("REGEX(F__c, G__c)", {"F__c": "x", "G__c": "y"})
    assert isinstance(out, NonEvaluable)

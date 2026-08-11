"""Formula evaluation — does a parsed VR formula fire on a single-object payload?

D-113 (S8 recipe-grounding leg). The evaluation counterpart of D-107 ``derive``:
``derive`` SOLVES (formula -> a violating assignment); ``evaluate`` COMPUTES
(formula + a concrete payload -> does it fire?). A validation rule's
error-condition formula "fires" — i.e. the create is rejected — when it
evaluates TRUE.

Pure, parser-shaped walk over the AST nodes, evaluable over the SAME
single-object create-time subset ``derive`` solves: comparisons, AND/OR/NOT, and
ISBLANK / ISNULL / ISPICKVAL. Anything outside that subset — the org-state
functions (PRIORVALUE / ISCHANGED / ISNEW), cross-object dotted refs, an
unparseable formula, a bare / type-uncertain predicate, or a non-numeric
ordering — yields :class:`NonEvaluable`, mirroring ``derive``'s
``_Undecidable -> NotDerivable`` boundary.

Three-valued (Kleene): the boolean connectives combine ``True`` / ``False`` /
``NonEvaluable`` so a *determinable* result still resolves even when a sibling
subtree is non-evaluable — ``True OR NonEvaluable`` is ``True``;
``False AND NonEvaluable`` is ``False``. This is strictly more precise than
bailing on the first non-evaluable node, and it is what lets the recipe-grounding
leg answer "still violates?" for a formula mixing evaluable and org-state
clauses. Pure computation — no satisfiability, no merge logic (that is
``derive``'s job).

A payload is ``{field_name: value}``; ``value`` of ``None`` means the field is
blank (the create leaves it unset) — the convention ``derive`` emits for
ISBLANK / ISNULL.
"""
from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Union

from primeqa.semantic.formula.nodes import (
    And,
    Comparison,
    FieldRef,
    FunctionCall,
    Literal,
    Not,
    NotParsed,
    Or,
)

_ORG_STATE_FUNCS = {"PRIORVALUE", "ISCHANGED", "ISNEW"}

# D-442 (B1): ISNULL is evaluable ONLY on field types where Salesforce null
# semantics match the blank check — the documented "text fields are never
# null" rule is honoured by REFUSAL on non-nullable/unknown types, never by
# emulating always-False from a secondary source.
_NULLABLE_TYPES = frozenset({
    "double", "currency", "percent", "int", "integer", "long",
    "date", "datetime", "time",
})

# Comparison op re-oriented to (field, literal) when the literal is on the LEFT.
_FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "=", "<>": "<>"}

# Concrete ordering / equality operators for a resolved (value, literal) pair.
_ORDER = {
    "=": operator.eq, "<>": operator.ne,
    "<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge,
}


@dataclass(frozen=True)
class NonEvaluable:
    """The formula left the single-object create-time evaluable subset, so
    whether it fires on the payload cannot be computed. ``reason`` is a short
    human tag (the recipe-grounding leg surfaces this as
    ``formula_non_evaluable``)."""

    reason: str


# True (fires — the payload violates) | False (does not) | NonEvaluable(reason)
EvalResult = Union[bool, NonEvaluable]


@dataclass(frozen=True)
class EvalContext:
    """The optional evaluation context arming the D-439 families. Absent
    (``context=None``) every armed construct stays :class:`NonEvaluable` —
    exactly the pre-D-439 behaviour, so context-less callers (the D-337
    vr_conflict gate, derivation) are untouched.

    * ``prior_state`` / ``is_create`` — the ORG-STATE pair (D-439): the graded
      mutation's pre-state and whether the graded step is a create. The pair
      is the smallest sound shape — a changed-flag loses PRIORVALUE; per-field
      second values in one dict make absence ambiguous.
      Create-context semantics, VERIFIED before building (sources in the
      D-439 tests): ``ISNEW`` → True (confirmed); ``PRIORVALUE`` → the
      CURRENT value (confirmed); ``ISCHANGED`` → **NonEvaluable — the
      published semantics could not be conclusively verified, and a guessed
      default flips verdicts** (D-399.1).
    * ``record_type_developer_name`` — resolver for the RecordType family:
      ``RecordTypeId -> DeveloperName`` or ``None`` (unresolvable →
      NonEvaluable, never a guess).
    """

    prior_state: Optional[Mapping] = None
    is_create: Optional[bool] = None
    record_type_developer_name: Optional[
        Callable[[str], Optional[str]]] = None
    # D-442: bare field name -> the field's S1 ``field_type`` (or None =
    # unknown). Feeds two guards and one conversion: ISNULL refuses unless
    # the type is known-nullable; percent-typed comparison operands convert
    # API value -> fraction (÷100 — Salesforce VR formulas evaluate percent
    # fields in FRACTION space, live-proven from the org's own firing
    # boundary: create Discount=20 succeeds against `> 0.20`, update 20.01
    # fires).
    field_type_of: Optional[Callable[[str], Optional[str]]] = None


def evaluate(ast, payload: dict, *, context: Optional[EvalContext] = None,
             ) -> EvalResult:
    """Evaluate a parsed VR formula against a single-object ``payload``.

    Returns ``True`` (the error condition fires — the payload violates the rule),
    ``False`` (it does not), or :class:`NonEvaluable` (outside the evaluable
    subset). Never raises on a recognized AST. ``context`` (D-439) arms the
    org-state, field-vs-field, and RecordType constructs; without it they are
    NonEvaluable exactly as before."""
    if isinstance(ast, NotParsed) or ast is None:
        return NonEvaluable("formula not parsed")
    return _eval(ast, payload, context)


def _eval(node, payload, ctx: Optional[EvalContext] = None) -> EvalResult:
    if isinstance(node, And):
        return _kleene_and(_eval(op, payload, ctx) for op in node.operands)
    if isinstance(node, Or):
        return _kleene_or(_eval(op, payload, ctx) for op in node.operands)
    if isinstance(node, Not):
        return _kleene_not(_eval(node.operand, payload, ctx))
    if isinstance(node, Comparison):
        return _eval_comparison(node, payload, ctx)
    if isinstance(node, FunctionCall):
        return _eval_function(node, payload, ctx)
    if isinstance(node, FieldRef):
        return NonEvaluable(f"bare field predicate {node.name} (type-uncertain)")
    if isinstance(node, Literal):
        # D-447: a constant BOOLEAN error condition decides — literal
        # `false` provably never fires (the census's 12 dead rules),
        # literal `true` always does. Non-boolean bare literals stay
        # refused (a bare number/string is not a boolean formula).
        if isinstance(node.value, bool):
            return node.value
        return NonEvaluable("constant non-boolean predicate")
    return NonEvaluable("unrecognized node")


# -- Kleene three-valued connectives -----------------------------------------

def _kleene_and(results) -> EvalResult:
    """AND: a single ``False`` dominates -> ``False``; else any
    ``NonEvaluable`` -> ``NonEvaluable``; else ``True``."""
    pending = None
    for r in results:
        if r is False:
            return False
        if isinstance(r, NonEvaluable):
            pending = r
    return pending if pending is not None else True


def _kleene_or(results) -> EvalResult:
    """OR: a single ``True`` dominates -> ``True``; else any ``NonEvaluable``
    -> ``NonEvaluable``; else ``False``."""
    pending = None
    for r in results:
        if r is True:
            return True
        if isinstance(r, NonEvaluable):
            pending = r
    return pending if pending is not None else False


def _kleene_not(r: EvalResult) -> EvalResult:
    if isinstance(r, NonEvaluable):
        return r
    return not r


# -- leaves ------------------------------------------------------------------

def _eval_comparison(node: Comparison, payload,
                     ctx: Optional[EvalContext] = None) -> EvalResult:
    left, right = node.left, node.right
    if isinstance(left, FieldRef) and isinstance(right, Literal):
        fref, lit, op = left, right, node.op
    elif isinstance(left, Literal) and isinstance(right, FieldRef):
        fref, lit, op = right, left, _FLIP[node.op]
    elif isinstance(left, FieldRef) and isinstance(right, FieldRef):
        return _eval_field_vs_field(left, right, node.op, payload, ctx)
    else:
        return NonEvaluable("comparison without a single field + literal")
    if fref.is_dotted:
        rt = _eval_record_type_comparison(fref, op, lit, payload, ctx)
        if rt is not None:
            return rt
        return NonEvaluable(f"cross-object ref {fref.name}")
    if fref.path[0] not in payload:
        return NonEvaluable(f"field {fref.path[0]} absent from payload")
    return _compare(_percent_adjusted(payload[fref.path[0]], fref.path[0],
                                      ctx), op, lit)


def _percent_adjusted(value, field: str, ctx: Optional[EvalContext]):
    """D-442: Salesforce VR formulas evaluate PERCENT fields in fraction
    space (API value ÷ 100) — live-proven by the org's own firing boundary
    (create `Discount=20` succeeds against `> 0.20`; update `20.01` fires).
    Convert only when the type is KNOWN percent; with no resolver or an
    unknown type the raw value stands as before (residual risk documented in
    the census)."""
    if (ctx is not None and ctx.field_type_of is not None
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and str(ctx.field_type_of(field)).lower() == "percent"):
        return value / 100.0
    return value


def _pickval_eq(value, literal):
    """D-444: ISPICKVAL matching, live-probed case-INSENSITIVE on env-59 —
    a staged case-variant (`Risk_Level='critical'`) fired the exact-literal
    rule (`ISPICKVAL(f, "Critical")`, VR07); whether by insensitive compare
    or pre-VR canonicalization is indistinguishable and verdict-equivalent.
    Exact → True; case-only difference → NonEvaluable (one sandbox probe is
    not platform law — the old exact-match False was the proven
    wrong-direction verdict, and emulating insensitivity would guess the
    other way); beyond-case difference → False (unchanged)."""
    if value == literal:
        return True
    if (isinstance(value, str) and isinstance(literal, str)
            and value.casefold() == literal.casefold()):
        return NonEvaluable(
            "ISPICKVAL case-variant match — org-probed case-insensitive "
            "(D-444); exact-match evaluation would be wrong-direction; "
            "refusing")
    return False


def _eval_field_vs_field(left: FieldRef, right: FieldRef, op: str,
                         payload,
                         ctx: Optional[EvalContext] = None) -> EvalResult:
    """D-439 field-vs-field: both bare, both present, both NUMERIC → compare.
    Anything else — dotted, absent, None (a blank), bool, non-numeric — is
    NonEvaluable: Salesforce's blank-vs-zero handling in numeric comparison
    is ambiguous, so the blank case must stay unknown (the corpus rule's own
    ISBLANK guards Kleene-short-circuit it to an evaluable False anyway)."""
    if left.is_dotted or right.is_dotted:
        return NonEvaluable("field-to-field comparison with a dotted ref")
    lf, rf = left.path[0], right.path[0]
    if lf not in payload or rf not in payload:
        return NonEvaluable("field-to-field comparison with a field absent "
                            "from the payload")
    lv, rv = payload[lf], payload[rf]
    if lv is None or rv is None:
        return NonEvaluable("null operand in field-to-field comparison")
    for v in (lv, rv):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return NonEvaluable("non-numeric operand in field-to-field "
                                "comparison")
    lv = _percent_adjusted(lv, lf, ctx)
    rv = _percent_adjusted(rv, rf, ctx)
    return _ORDER[op](lv, rv)


_RECORD_TYPE_PATH = ("RecordType", "DeveloperName")


def _eval_record_type_comparison(fref: FieldRef, op: str, lit: Literal,
                                 payload,
                                 ctx: Optional[EvalContext]) -> Optional[
                                     EvalResult]:
    """D-439 RecordType family: ``RecordType.DeveloperName <op> "lit"``
    resolves through the injected S1 resolver. Returns None when this is not
    the RecordType shape (caller falls through to the generic dotted-ref
    NonEvaluable). Unresolvable id / missing resolver / missing RecordTypeId
    → NonEvaluable, never a guess."""
    if tuple(fref.path) != _RECORD_TYPE_PATH:
        return None
    if op not in ("=", "<>") or lit.kind != "string":
        return NonEvaluable("RecordType.DeveloperName comparison outside "
                            "string equality")
    if ctx is None or ctx.record_type_developer_name is None:
        return NonEvaluable("RecordType.DeveloperName needs the S1 resolver "
                            "(no context)")
    rt_id = payload.get("RecordTypeId")
    if not rt_id:
        return NonEvaluable("payload carries no RecordTypeId")
    dev = ctx.record_type_developer_name(str(rt_id))
    if dev is None:
        return NonEvaluable(f"RecordTypeId {rt_id!r} did not resolve to a "
                            f"DeveloperName in S1")
    return _ORDER[op](dev, lit.value)


def _compare(value, op: str, literal: Literal) -> EvalResult:
    if value is None:
        return NonEvaluable("null operand in comparison")
    kind, lit = literal.kind, literal.value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return NonEvaluable("non-numeric payload value vs number literal")
        return _ORDER[op](value, lit)
    if kind in ("string", "boolean"):
        if op == "=":
            return value == lit
        if op == "<>":
            return value != lit
        return NonEvaluable(f"non-numeric ordering ({op}) on a {kind} literal")
    return NonEvaluable(f"unsupported literal kind {kind!r}")


# D-447 REGEX guard (iii-b): Java character-class intersection ([a&&[b]])
# is parsed LITERALLY by Python's re — a silent semantic divergence that
# raises nothing, so it must be refused by inspection.
_JAVA_CLASS_INTERSECTION = re.compile(r"\[[^\]]*&&")


def _eval_regex(node: FunctionCall, payload) -> EvalResult:
    """D-447: SF ``REGEX(text, pattern)`` behind the four D-344 guards.
    SF semantics are Java ``String.matches`` — the WHOLE string must match
    (guard i: ``re.fullmatch``, never ``search`` — the corpus's unanchored
    ``FB-[0-9]{6}`` would otherwise wrongly match embedded occurrences).
    The vr-dialect lexer already unescapes ``\\\\`` in string literals, so
    the pattern compiles AS RECEIVED (guard ii: no double-unescape).
    ``re.error`` — Java-only escapes, possessive quantifiers — refuses
    (guard iii), as does the never-raising Java class-intersection syntax.
    Blank/absent input refuses (guard iv: SF null-text semantics
    unverified; the corpus's ISBLANK guards Kleene-resolve around it)."""
    if (len(node.args) != 2 or not isinstance(node.args[1], Literal)
            or not isinstance(node.args[1].value, str)):
        return NonEvaluable("REGEX without a literal text pattern")
    field = _single_field(node)
    if field is None:
        return NonEvaluable("REGEX without a single same-object field")
    value = payload.get(field)
    if value is None or value == "":
        return NonEvaluable(
            "REGEX on a blank input — Salesforce null-text semantics "
            "not verified; refusing (D-447)")
    if not isinstance(value, str):
        return NonEvaluable("REGEX on a non-text payload value")
    pattern = node.args[1].value
    if _JAVA_CLASS_INTERSECTION.search(pattern):
        return NonEvaluable(
            "REGEX pattern uses Java character-class intersection — "
            "Python re parses it literally (silent divergence); refusing "
            "(D-447)")
    try:
        return re.fullmatch(pattern, value) is not None
    except re.error:
        return NonEvaluable(
            "REGEX pattern does not compile under Python re — "
            "Java-vs-Python syntax divergence; refusing (D-447)")


def _eval_function(node: FunctionCall, payload,
                   ctx: Optional[EvalContext] = None) -> EvalResult:
    name = node.name
    if name in _ORG_STATE_FUNCS:
        return _eval_org_state(node, payload, ctx)
    if name == "REGEX":
        return _eval_regex(node, payload)
    if name == "ISBLANK":
        field = _single_field(node)
        if field is None:
            return NonEvaluable("ISBLANK without a single same-object field")
        v = payload.get(field)
        return v is None or v == ""
    if name == "ISNULL":
        # D-442 (B1): evaluable ONLY when the field's type is known-nullable.
        # Text-like types refuse (SF: "text fields are never null" — we
        # refuse rather than emulate); UNKNOWN type refuses too — never a
        # guessed verdict.
        field = _single_field(node)
        if field is None:
            return NonEvaluable("ISNULL without a single same-object field")
        ftype = (ctx.field_type_of(field)
                 if ctx is not None and ctx.field_type_of is not None
                 else None)
        if ftype is None or str(ftype).lower() not in _NULLABLE_TYPES:
            return NonEvaluable(
                f"ISNULL({field}) on a non-nullable or unknown field type "
                f"({ftype!r}) — Salesforce text-family null semantics "
                f"diverge; refusing rather than guessing (D-442)")
        v = payload.get(field)
        return v is None or v == ""
    if name == "ISPICKVAL":
        if len(node.args) != 2 or not isinstance(node.args[1], Literal):
            return NonEvaluable("ISPICKVAL without a literal value")
        if node.args[1].value == "":
            # D-442 (B2): the empty-literal blank test — Salesforce's own
            # behaviour is disputed; refusing is the only sound answer.
            return NonEvaluable(
                "ISPICKVAL with an empty-string literal (the blank-test "
                "idiom) — Salesforce semantics disputed; refusing (D-442)")
        field = _single_field(node)
        if field is not None:
            return _pickval_eq(payload.get(field), node.args[1].value)
        # D-439: the corpus composition ISPICKVAL(PRIORVALUE(f), "lit").
        prior = _prior_value_of(node.args[0], payload, ctx)
        if prior is not None:
            if isinstance(prior, NonEvaluable):
                return prior
            return _pickval_eq(prior[0], node.args[1].value)
        return NonEvaluable("ISPICKVAL without a single same-object field")
    return NonEvaluable(f"function {name} not evaluable")


def _eval_org_state(node: FunctionCall, payload,
                    ctx: Optional[EvalContext]) -> EvalResult:
    """D-439 org-state arm. Without context: NonEvaluable exactly as before.
    Create-context semantics per the verify-first gate: ISNEW → True
    (confirmed); ISCHANGED → **NonEvaluable — not conclusively verified, and
    a guessed default flips verdicts** (D-399.1); PRIORVALUE is handled where
    it composes (``_prior_value_of``), not as a boolean."""
    name = node.name
    if ctx is None or ctx.is_create is None:
        return NonEvaluable(
            f"org-state function {name} (needs prior/changed/new record state)")
    if name == "ISNEW":
        return bool(ctx.is_create)
    if name == "ISCHANGED":
        field = _single_field(node)
        if field is None:
            return NonEvaluable("ISCHANGED without a single same-object field")
        if ctx.is_create:
            # D-447: resolved on the retried official source (the ISCHANGED
            # article, rendered 2026-08-11): "This function returns FALSE
            # when evaluating any field on a newly created record."
            return False
        if ctx.prior_state is None:
            return NonEvaluable("ISCHANGED without a prior state")
        if field not in ctx.prior_state or field not in payload:
            return NonEvaluable(
                f"ISCHANGED({field}) with the field absent from the prior "
                f"or new state")
        return ctx.prior_state[field] != payload[field]
    # PRIORVALUE standing alone is a value, not a boolean predicate.
    return NonEvaluable("PRIORVALUE is not a boolean predicate on its own")


def _prior_value_of(arg, payload, ctx: Optional[EvalContext]):
    """``PRIORVALUE(f)`` as a VALUE (for compositions): returns ``None`` when
    ``arg`` is not that shape; ``NonEvaluable`` when it is but cannot be
    resolved; else a 1-tuple ``(value,)`` (so a legitimate None prior value
    is distinguishable from "not the shape")."""
    if not (isinstance(arg, FunctionCall) and arg.name == "PRIORVALUE"):
        return None
    field = _single_field(arg)
    if field is None:
        return NonEvaluable("PRIORVALUE without a single same-object field")
    if ctx is None or ctx.is_create is None:
        return NonEvaluable(
            "org-state function PRIORVALUE (needs prior/changed/new record "
            "state)")
    if ctx.is_create:
        # CONFIRMED (D-439 verify-first): on creation PRIORVALUE returns the
        # CURRENT value. Absent from the payload → blank/None prior.
        return (payload.get(field),)
    if ctx.prior_state is None or field not in ctx.prior_state:
        return NonEvaluable(
            f"PRIORVALUE({field}) with the field absent from the prior state")
    return (ctx.prior_state[field],)


def _single_field(node: FunctionCall):
    """The single, same-object field name of a function's first arg, or None."""
    if node.args and isinstance(node.args[0], FieldRef) and not node.args[0].is_dotted:
        return node.args[0].path[0]
    return None

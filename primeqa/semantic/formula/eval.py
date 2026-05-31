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
from dataclasses import dataclass
from typing import Union

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


def evaluate(ast, payload: dict) -> EvalResult:
    """Evaluate a parsed VR formula against a single-object create ``payload``.

    Returns ``True`` (the error condition fires — the payload violates the rule),
    ``False`` (it does not), or :class:`NonEvaluable` (outside the evaluable
    subset). Never raises on a recognized AST."""
    if isinstance(ast, NotParsed) or ast is None:
        return NonEvaluable("formula not parsed")
    return _eval(ast, payload)


def _eval(node, payload) -> EvalResult:
    if isinstance(node, And):
        return _kleene_and(_eval(op, payload) for op in node.operands)
    if isinstance(node, Or):
        return _kleene_or(_eval(op, payload) for op in node.operands)
    if isinstance(node, Not):
        return _kleene_not(_eval(node.operand, payload))
    if isinstance(node, Comparison):
        return _eval_comparison(node, payload)
    if isinstance(node, FunctionCall):
        return _eval_function(node, payload)
    if isinstance(node, FieldRef):
        return NonEvaluable(f"bare field predicate {node.name} (type-uncertain)")
    if isinstance(node, Literal):
        return NonEvaluable("constant boolean predicate")
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

def _eval_comparison(node: Comparison, payload) -> EvalResult:
    left, right = node.left, node.right
    if isinstance(left, FieldRef) and isinstance(right, Literal):
        fref, lit, op = left, right, node.op
    elif isinstance(left, Literal) and isinstance(right, FieldRef):
        fref, lit, op = right, left, _FLIP[node.op]
    elif isinstance(left, FieldRef) and isinstance(right, FieldRef):
        return NonEvaluable("field-to-field comparison (no literal to compare)")
    else:
        return NonEvaluable("comparison without a single field + literal")
    if fref.is_dotted:
        return NonEvaluable(f"cross-object ref {fref.name}")
    if fref.path[0] not in payload:
        return NonEvaluable(f"field {fref.path[0]} absent from payload")
    return _compare(payload[fref.path[0]], op, lit)


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


def _eval_function(node: FunctionCall, payload) -> EvalResult:
    name = node.name
    if name in _ORG_STATE_FUNCS:
        return NonEvaluable(
            f"org-state function {name} (needs prior/changed/new record state)")
    if name in ("ISBLANK", "ISNULL"):
        field = _single_field(node)
        if field is None:
            return NonEvaluable(f"{name} without a single same-object field")
        v = payload.get(field)
        return v is None or v == ""
    if name == "ISPICKVAL":
        if len(node.args) != 2 or not isinstance(node.args[1], Literal):
            return NonEvaluable("ISPICKVAL without a literal value")
        field = _single_field(node)
        if field is None:
            return NonEvaluable("ISPICKVAL without a single same-object field")
        return payload.get(field) == node.args[1].value
    return NonEvaluable(f"function {name} not evaluable")


def _single_field(node: FunctionCall):
    """The single, same-object field name of a function's first arg, or None."""
    if node.args and isinstance(node.args[0], FieldRef) and not node.args[0].is_dotted:
        return node.args[0].path[0]
    return None

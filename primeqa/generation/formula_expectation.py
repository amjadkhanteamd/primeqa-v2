"""Formula-expectation verification — the req-302 robustness arc's R3 guard.

The D-304 formula primitive takes the LLM's ``expected_value`` on faith; the
honest run-time fail-loop was the only backstop. Live consequence (claim
7649c167): an update-then-observe claim asserted the PRE-update LTV (62.5)
while the org correctly recomputed 75.0 — a defective claim that could only be
discovered by running it.

This module VERIFIES the LLM's value against the bound calculated field's own
``calculatedFormula`` (parsed in the D-107 VALUE dialect) and the intent's own
staged inputs — the create-state ``trigger_fields`` and, when an update phase
exists (D-306), the post-update state. It never fabricates claim content
(k16): a refusal's plain detail may cite the derived value, but nothing is
ever written into a claim body.

Fail-open is load-bearing: governance cannot see padding-set fields, so ANY
formula field-ref absent from the staged inputs → ``not_evaluable`` → accept
unchanged (the run-time loop stays the backstop). Weakening this gate is the
one dangerous failure mode (a wrong refusal of a correct claim).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

from primeqa.semantic.formula import (
    And,
    Arithmetic,
    Comparison,
    FieldRef,
    FunctionCall,
    If,
    Literal,
    Not,
    NotParsed,
    Or,
    parse,
)

_NUMERIC_FIELD_TYPES = frozenset(
    {"int", "integer", "double", "currency", "percent"})


@dataclass(frozen=True)
class _NotEvaluable:
    reason: str


@dataclass(frozen=True)
class FormulaVerdict:
    """The verification outcome. ``computed_final`` / ``computed_create`` are
    canonical decimal STRINGS (post type-conversion: percent ×100, quantized
    to the field's scale) for refusal details — plain, never re-parsed."""

    status: str            # "match" | "pre_update_match" | "mismatch" | "not_evaluable"
    computed_final: Optional[str] = None
    computed_create: Optional[str] = None


def as_decimal(value: Any) -> Optional[Decimal]:
    """``value`` as a Decimal, or None when it isn't a number. Booleans are
    NOT numbers (the ``isinstance(True, int)`` trap)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, Decimal):
        return value
    return None


def verify_formula_expectation(
    *, formula_text: Optional[str], expected_value: Any,
    create_inputs: dict, update_inputs: dict,
    field_type: Optional[str], scale: Optional[int],
    treat_null_as_zero: bool,
) -> FormulaVerdict:
    """Verify ``expected_value`` against the formula evaluated on the intent's
    own staged inputs. Pure; never raises.

    Input keys may be object-qualified (``Opportunity.Loan_Amount__c``) — the
    formula speaks bare names, so keys are bare-ified case-insensitively."""
    try:
        expected = as_decimal(expected_value)
        if formula_text is None or expected is None:
            return FormulaVerdict("not_evaluable")
        ast = parse(formula_text, dialect="value")
        if isinstance(ast, NotParsed):
            return FormulaVerdict("not_evaluable")

        create_payload = _bare_payload(create_inputs)
        create_raw = _eval_value(ast, create_payload, treat_null_as_zero)
        if isinstance(create_raw, _NotEvaluable) or create_raw is None:
            return FormulaVerdict("not_evaluable")
        computed_create = _to_api_value(create_raw, field_type, scale)

        if update_inputs:
            final_payload = {**create_payload, **_bare_payload(update_inputs)}
            final_raw = _eval_value(ast, final_payload, treat_null_as_zero)
            if isinstance(final_raw, _NotEvaluable) or final_raw is None:
                return FormulaVerdict("not_evaluable")
            computed_final = _to_api_value(final_raw, field_type, scale)
        else:
            computed_final = computed_create

        expected_q = _quantize(expected, scale)
        if expected_q == computed_final:
            return FormulaVerdict("match", _plain(computed_final),
                                  _plain(computed_create))
        if update_inputs and expected_q == computed_create:
            return FormulaVerdict("pre_update_match", _plain(computed_final),
                                  _plain(computed_create))
        return FormulaVerdict("mismatch", _plain(computed_final),
                              _plain(computed_create))
    except Exception:
        # A guard must never break generation — any surprise is not_evaluable.
        return FormulaVerdict("not_evaluable")


def _bare_payload(inputs: dict) -> dict:
    """Object-qualified keys → bare lower-cased (formulas speak bare names)."""
    return {str(k).rsplit(".", 1)[-1].lower(): v
            for k, v in (inputs or {}).items()}


def _lookup(payload: dict, fref: FieldRef):
    """Case-insensitive bare-name lookup; ``_MISSING`` when unstaged."""
    if fref.is_dotted:
        return _MISSING
    return payload.get(fref.path[-1].lower(), _MISSING)


_MISSING = object()


def _to_api_value(raw: Decimal, field_type: Optional[str],
                  scale: Optional[int]) -> Decimal:
    """The API-visible value of a raw formula result — the D-304 semantics:
    a percent field's API value is the formula result ×100; quantize to the
    field's scale (ROUND_HALF_UP)."""
    v = raw * Decimal(100) if (field_type or "").lower() == "percent" else raw
    return _quantize(v, scale)


def _quantize(v: Decimal, scale: Optional[int]) -> Decimal:
    if scale is None:
        return v
    try:
        q = Decimal(1).scaleb(-int(scale))
        return v.quantize(q, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return v


def _plain(v: Decimal) -> str:
    """A plain decimal string for refusal details — never scientific notation
    (``110``, not ``1.1E+2``); trailing zeros trimmed."""
    s = format(v, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _eval_value(node, payload: dict, tnz: bool):
    """Numeric evaluation of a value-dialect AST. Returns Decimal | None (the
    formula's null) | _NotEvaluable. All-Decimal arithmetic — no float drift
    (``100 * 1.1`` is exactly ``110``)."""
    if isinstance(node, Literal):
        if node.kind == "number":
            return Decimal(str(node.value))
        if node.kind == "null":
            return None
        return _NotEvaluable(f"non-numeric literal ({node.kind})")
    if isinstance(node, FieldRef):
        v = _lookup(payload, node)
        if v is _MISSING:
            # Load-bearing fail-open: padding-set fields are invisible here.
            return _NotEvaluable(f"field {node.name} not staged")
        d = as_decimal(v)
        if d is None:
            return None if v is None else _NotEvaluable(
                f"non-numeric staged value for {node.name}")
        return d
    if isinstance(node, Arithmetic):
        left = _eval_value(node.left, payload, tnz)
        right = _eval_value(node.right, payload, tnz)
        for side in (left, right):
            if isinstance(side, _NotEvaluable):
                return side
        if left is None or right is None:
            if not tnz:
                return None                      # SF: null propagates
            left = left if left is not None else Decimal(0)
            right = right if right is not None else Decimal(0)
        if node.op == "+":
            return left + right
        if node.op == "-":
            return left - right
        if node.op == "*":
            return left * right
        if node.op == "/":
            if right == 0:
                return _NotEvaluable("division by zero")
            return left / right
        return _NotEvaluable(f"unknown arithmetic op {node.op!r}")
    if isinstance(node, If):
        cond = _eval_bool(node.cond, payload, tnz)
        if cond is True:
            return _eval_value(node.then, payload, tnz)
        if cond is False:
            return _eval_value(node.els, payload, tnz)
        return cond                              # _NotEvaluable
    return _NotEvaluable(f"node not value-evaluable: {type(node).__name__}")


def _eval_bool(node, payload: dict, tnz: bool):
    """Boolean evaluation for IF conditions — strict True/False or
    _NotEvaluable (no Kleene shortcuts needed: an undecidable condition makes
    the whole verification not_evaluable, which is the honest posture)."""
    if isinstance(node, And):
        out = True
        for op in node.operands:
            r = _eval_bool(op, payload, tnz)
            if isinstance(r, _NotEvaluable):
                return r
            out = out and r
        return out
    if isinstance(node, Or):
        out = False
        for op in node.operands:
            r = _eval_bool(op, payload, tnz)
            if isinstance(r, _NotEvaluable):
                return r
            out = out or r
        return out
    if isinstance(node, Not):
        r = _eval_bool(node.operand, payload, tnz)
        return r if isinstance(r, _NotEvaluable) else (not r)
    if isinstance(node, Comparison):
        left = _eval_value(node.left, payload, tnz)
        right = _eval_value(node.right, payload, tnz)
        for side in (left, right):
            if isinstance(side, _NotEvaluable):
                return side
        if left is None or right is None:
            if not tnz:
                return _NotEvaluable("null operand in comparison")
            left = left if left is not None else Decimal(0)
            right = right if right is not None else Decimal(0)
        ops = {"=": left == right, "<>": left != right, "<": left < right,
               ">": left > right, "<=": left <= right, ">=": left >= right}
        if node.op not in ops:
            return _NotEvaluable(f"unknown comparison op {node.op!r}")
        return ops[node.op]
    if isinstance(node, FunctionCall):
        if node.name in ("ISBLANK", "ISNULL"):
            if (len(node.args) == 1 and isinstance(node.args[0], FieldRef)):
                v = _lookup(payload, node.args[0])
                if v is _MISSING:
                    return _NotEvaluable(
                        f"field {node.args[0].name} not staged")
                return v is None or v == ""
        if node.name == "ISPICKVAL":
            if (len(node.args) == 2 and isinstance(node.args[0], FieldRef)
                    and isinstance(node.args[1], Literal)):
                v = _lookup(payload, node.args[0])
                if v is _MISSING:
                    return _NotEvaluable(
                        f"field {node.args[0].name} not staged")
                return v == node.args[1].value
        return _NotEvaluable(f"function {node.name} not evaluable")
    if isinstance(node, Literal) and node.kind == "boolean":
        return bool(node.value)
    if isinstance(node, FieldRef):
        v = _lookup(payload, node)
        if isinstance(v, bool):
            return v
        return _NotEvaluable(f"bare field predicate {node.name}")
    return _NotEvaluable(f"node not bool-evaluable: {type(node).__name__}")

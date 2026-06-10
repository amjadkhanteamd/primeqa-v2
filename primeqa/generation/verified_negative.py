"""Violating-value derivation for verified negatives (D-107, slice 3).

Given a parsed validation-rule formula AST (the slice-1 parser, imported from
``primeqa/semantic/formula``), derive — *with certainty* — a single-object,
create-time field assignment that makes the error-condition formula evaluate
TRUE (i.e. fires the rejection). That assignment is the violating payload of a
**verified** negative.

Contract::

    derive(ast) -> VerifiedNegative(violating_payload: {field: value})
                 | NotDerivable(reason)

Certainty is the bar (D-107 .1): derive only when the violating assignment is
certain; otherwise ``NotDerivable(reason)`` — never a guessed payload. The
derivable / not-derivable line *is* the verified-vs-caveated line (slice 4):
a ``NotDerivable`` (or NotParsed) formula falls back to the caveated negative
(D-101).

Not derivable at create-time / single-object certainty:
  - org-state functions (PRIORVALUE / ISCHANGED / ISNEW) anywhere;
  - cross-object dotted refs anywhere;
  - field-to-field or constant comparisons (no field+literal to assign);
  - non-numeric ordering (``<`` / ``>`` on strings/booleans);
  - "not blank" / "not this picklist value" (no certain valid alternative);
  - a bare field / bare boolean predicate (type-uncertain);
  - a compound that forces conflicting values on one field.

``value`` of ``None`` in the payload means "leave the field blank" (the
violating assignment for ISBLANK / ISNULL).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from primeqa.semantic.formula import (
    And, Comparison, FieldRef, FunctionCall, Literal, Not, NotParsed, Or, walk,
)

_ORG_STATE_FUNCS = {"PRIORVALUE", "ISCHANGED", "ISNEW"}
_FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "=", "<>": "<>"}
_NEG = {"<": ">=", ">": "<=", "<=": ">", ">=": "<", "=": "<>", "<>": "="}


@dataclass(frozen=True)
class VerifiedNegative:
    violating_payload: dict[str, Any]   # {field_name: value}; value None = blank


@dataclass(frozen=True)
class VerifiedUpdateNegative:
    """The 2-step update-rejected pair (D-203): a create-time state that does
    NOT violate the formula (the setup record) + the field changes that DO
    (the update the org must reject). Both certainty-derived — only formulas
    satisfiable in BOTH directions qualify (comparisons; NOT-ISPICKVAL /
    NOT-ISBLANK have no certain non-violating assignment and stay
    NotDerivable)."""

    setup_payload: dict[str, Any]       # non-violating create-time state
    violating_changes: dict[str, Any]   # the update that fires the rejection


@dataclass(frozen=True)
class NotDerivable:
    reason: str


class _Undecidable(Exception):
    """Internal — certainty failed for some subtree; caught at the boundary."""


def derive(ast) -> Union[VerifiedNegative, NotDerivable]:
    """Derive the violating payload, or NotDerivable (fail-loud)."""
    blocked = _pre_scan(ast)
    if blocked is not None:
        return blocked
    try:
        payload = _satisfy(ast, True)
    except _Undecidable as e:
        return NotDerivable(str(e))
    if not payload:
        return NotDerivable("no field assignment derivable (constant predicate)")
    return VerifiedNegative(payload)


def derive_update(ast) -> Union[VerifiedUpdateNegative, NotDerivable]:
    """Derive the 2-step update-rejected pair (D-203): setup =
    ``_satisfy(ast, False)`` (a create the rule does NOT reject), violating
    changes = ``_satisfy(ast, True)`` (the update it MUST). Same pre-scan +
    certainty bar as :func:`derive`; either direction underivable →
    NotDerivable (the caller's graded fallback)."""
    blocked = _pre_scan(ast)
    if blocked is not None:
        return blocked
    try:
        setup = _satisfy(ast, False)
        violating = _satisfy(ast, True)
    except _Undecidable as e:
        return NotDerivable(str(e))
    if not setup or not violating:
        return NotDerivable("no field assignment derivable (constant predicate)")
    return VerifiedUpdateNegative(
        setup_payload=setup, violating_changes=violating)


def _pre_scan(ast) -> Optional[NotDerivable]:
    """Anything that can't be a same-object, flat-state predicate."""
    if isinstance(ast, NotParsed) or ast is None:
        return NotDerivable("formula not parsed")
    for n in walk(ast):
        if isinstance(n, FunctionCall) and n.name in _ORG_STATE_FUNCS:
            return NotDerivable(f"org-state function {n.name} (needs prior/changed/new record state)")
        if isinstance(n, FieldRef) and n.is_dotted:
            return NotDerivable(f"cross-object ref {n.name} (needs related-record state)")
    return None


def _satisfy(node, want_true: bool) -> dict[str, Any]:
    """A field assignment that makes ``node`` evaluate to ``want_true``.
    Raises ``_Undecidable`` when no certain assignment exists."""
    if isinstance(node, And):
        if want_true:
            return _merge(_satisfy(op, True) for op in node.operands)
        return _first(node.operands, False)          # NOT(AND) = OR(NOT ops)
    if isinstance(node, Or):
        if want_true:
            return _first(node.operands, True)
        return _merge(_satisfy(op, False) for op in node.operands)  # NOT(OR)=AND(NOT)
    if isinstance(node, Not):
        return _satisfy(node.operand, not want_true)
    if isinstance(node, Comparison):
        return _satisfy_comparison(node, want_true)
    if isinstance(node, FunctionCall):
        return _satisfy_function(node, want_true)
    if isinstance(node, FieldRef):
        raise _Undecidable(f"bare field predicate {node.name} (type-uncertain)")
    if isinstance(node, Literal):
        raise _Undecidable("constant boolean predicate")
    raise _Undecidable("unrecognized node")


def _first(operands, want_true: bool) -> dict[str, Any]:
    """First operand with a derivable satisfying assignment (OR / NOT-AND)."""
    last = None
    for op in operands:
        try:
            return _satisfy(op, want_true)
        except _Undecidable as e:
            last = e
    raise _Undecidable(f"no derivable disjunct ({last})")


def _merge(dicts) -> dict[str, Any]:
    """Merge per-operand assignments; conflicting values on one field is not
    certainly satisfiable here (we do not solve multi-constraint-per-field)."""
    out: dict[str, Any] = {}
    for d in dicts:
        for field, value in d.items():
            if field in out and out[field] != value:
                raise _Undecidable(f"conflicting assignment on field {field}")
            out[field] = value
    return out


def _satisfy_comparison(node: Comparison, want_true: bool) -> dict[str, Any]:
    field, literal, op = _orient(node)
    if want_true is False:
        op = _NEG[op]
    return {field: _violating_value(op, literal)}


def _orient(node: Comparison):
    """Return (field_name, Literal, op) with op relative to the field. Both-field
    or both-literal comparisons are not derivable."""
    left, right = node.left, node.right
    if isinstance(left, FieldRef) and isinstance(right, Literal):
        return left.path[0], right, node.op
    if isinstance(left, Literal) and isinstance(right, FieldRef):
        return right.path[0], left, _FLIP[node.op]
    if isinstance(left, FieldRef) and isinstance(right, FieldRef):
        raise _Undecidable("field-to-field comparison (no literal to assign)")
    raise _Undecidable("comparison without a single field + literal")


def _violating_value(op: str, literal: Literal) -> Any:
    """A concrete value making ``field op literal`` TRUE, with certainty."""
    v = literal.value
    if literal.kind == "number":
        return {"=": v, "<=": v, ">=": v, "<": v - 1, ">": v + 1, "<>": v + 1}[op]
    if literal.kind == "string":
        if op == "=":
            return v
        if op == "<>":
            return f"{v}_x"                           # any value != v
        raise _Undecidable(f"non-numeric ordering ({op}) on a string literal")
    if literal.kind == "boolean":
        if op == "=":
            return v
        if op == "<>":
            return not v
        raise _Undecidable(f"non-numeric ordering ({op}) on a boolean literal")
    raise _Undecidable(f"unsupported literal kind {literal.kind!r}")


def _satisfy_function(node: FunctionCall, want_true: bool) -> dict[str, Any]:
    if node.name in ("ISBLANK", "ISNULL"):
        field = _single_field(node)
        if want_true:
            return {field: None}                     # blank fires the rejection
        raise _Undecidable(f"NOT {node.name} (no certain non-blank value)")
    if node.name == "ISPICKVAL":
        field = _single_field(node)
        if not (len(node.args) == 2 and isinstance(node.args[1], Literal)):
            raise _Undecidable("ISPICKVAL without a literal value")
        if want_true:
            return {field: node.args[1].value}
        raise _Undecidable("NOT ISPICKVAL (no certain alternative picklist value)")
    raise _Undecidable(f"function {node.name} not derivable")


def _single_field(node: FunctionCall) -> str:
    if node.args and isinstance(node.args[0], FieldRef):
        return node.args[0].path[0]                  # dotted already pre-scanned out
    raise _Undecidable(f"{node.name} without a single field argument")

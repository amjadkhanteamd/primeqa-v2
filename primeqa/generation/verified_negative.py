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
    And, Comparison, FieldRef, FunctionCall, Literal, Not, NotParsed, NonEvaluable,
    Or, evaluate, walk,
)

_ORG_STATE_FUNCS = {"PRIORVALUE", "ISCHANGED", "ISNEW"}
_FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "=", "<>": "<>"}
_NEG = {"<": ">=", ">": "<=", "<=": ">", ">=": "<", "=": "<>", "<>": "="}
# D-294: numeric SF DescribeField.type-s a cross-field comparison can order.
# Mirrors `execution_engine.world._NUMERIC` (kept local to avoid an S3->S4 import;
# unified with the shared value-synthesis helper in a later slice).
_NUMERIC = frozenset({"int", "integer", "double", "currency", "percent"})
# D-294: textual SF types a deterministic non-blank filler can safely fill.
# Mirrors `execution_engine.world._TEXTUAL` (kept local — see _nonblank_value).
_TEXTUAL = frozenset({"string", "text", "textarea", "combobox"})
_UNSYNTHESIZABLE = object()   # "no certain non-blank value for this field type"
# A concrete numeric (left, right) pair making ``left <op> right`` TRUE — the
# violating assignment for a cross-field comparison (D-294). Deterministic, so a
# ``>`` pair is strictly-greater by construction (no `evaluate` self-check exists
# for field-to-field; correctness is construction- + live-proof).
_CROSS_PAIR = {">": (1, 0), ">=": (1, 0), "<": (0, 1), "<=": (0, 1),
               "=": (0, 0), "<>": (1, 0)}


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


def derive(ast, field_metadata=None) -> Union[VerifiedNegative, NotDerivable]:
    """Derive the violating payload, or NotDerivable (fail-loud). ``field_metadata``
    (D-294, bare-field-keyed S1 type/picklist) widens derivation to metadata-backed
    shapes (cross-field armed here); absent/insufficient metadata refuses exactly
    as the metadata-free path (the certainty bar)."""
    blocked = _pre_scan(ast)
    if blocked is not None:
        return blocked
    try:
        payload = _satisfy(ast, True, field_metadata or {})
    except _Undecidable as e:
        return NotDerivable(str(e))
    if not payload:
        return NotDerivable("no field assignment derivable (constant predicate)")
    return VerifiedNegative(payload)


def derive_update(ast, field_metadata=None) -> Union[VerifiedUpdateNegative, NotDerivable]:
    """Derive the 2-step update-rejected pair (D-203): setup =
    ``_satisfy(ast, False)`` (a create the rule does NOT reject), violating
    changes = ``_satisfy(ast, True)`` (the update it MUST). Same pre-scan +
    certainty bar as :func:`derive`; either direction underivable →
    NotDerivable (the caller's graded fallback). ``field_metadata`` (D-294)
    widens what derives (cross-field: a non-violating pair + a violating pair)."""
    blocked = _pre_scan(ast)
    if blocked is not None:
        return blocked
    meta = field_metadata or {}
    try:
        setup = _satisfy(ast, False, meta)
        violating = _satisfy(ast, True, meta)
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


def _satisfy(node, want_true: bool, meta: dict) -> dict[str, Any]:
    """A field assignment that makes ``node`` evaluate to ``want_true``.
    Raises ``_Undecidable`` when no certain assignment exists. ``meta`` is the
    D-294 bare-field-keyed metadata rail (empty -> metadata-free behaviour)."""
    if isinstance(node, And):
        if want_true:
            return _merge(_satisfy(op, True, meta) for op in node.operands)
        return _first(node.operands, False, meta)     # NOT(AND) = OR(NOT ops)
    if isinstance(node, Or):
        if want_true:
            return _first(node.operands, True, meta)
        return _merge(_satisfy(op, False, meta) for op in node.operands)  # NOT(OR)=AND(NOT)
    if isinstance(node, Not):
        return _satisfy(node.operand, not want_true, meta)
    if isinstance(node, Comparison):
        return _satisfy_comparison(node, want_true, meta)
    if isinstance(node, FunctionCall):
        return _satisfy_function(node, want_true, meta)
    if isinstance(node, FieldRef):
        # D-294: a bare boolean field predicate is TRUE iff the field is TRUE, so
        # ``want_true`` maps directly onto the field's value (NOT-wrapping is
        # already handled by the Not case flipping want_true). Only when metadata
        # confirms the field is boolean — otherwise a bare field is type-uncertain.
        fm = (meta or {}).get(node.path[-1])
        if fm is not None and fm.get("field_type") == "boolean":
            return {node.path[-1]: want_true}
        raise _Undecidable(f"bare field predicate {node.name} (type-uncertain)")
    if isinstance(node, Literal):
        raise _Undecidable("constant boolean predicate")
    raise _Undecidable("unrecognized node")


def _first(operands, want_true: bool, meta: dict) -> dict[str, Any]:
    """First operand with a derivable satisfying assignment (OR / NOT-AND)."""
    last = None
    for op in operands:
        try:
            return _satisfy(op, want_true, meta)
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


def _satisfy_comparison(node: Comparison, want_true: bool, meta: dict) -> dict[str, Any]:
    # D-294: a field-to-field comparison (`Loan__c > Property__c`) is derivable
    # when both fields are numeric + writable — synthesize an ordered pair.
    if isinstance(node.left, FieldRef) and isinstance(node.right, FieldRef):
        return _satisfy_cross_field(node.left, node.right, node.op, want_true, meta)
    field, literal, op = _orient(node)
    if want_true is False:
        op = _NEG[op]
    return {field: _violating_value(op, literal)}


def _field_meta(node: FieldRef, meta: dict) -> Optional[dict]:
    """The rail entry for a FieldRef, by its bare field key (the LAST path
    segment — the field, for both bare ``Loan__c`` and self-qualified
    ``Opportunity.Loan__c`` refs, matching how the rail is keyed). None if absent."""
    return (meta or {}).get(node.path[-1])


def _satisfy_cross_field(left: FieldRef, right: FieldRef, op: str,
                         want_true: bool, meta: dict) -> dict[str, Any]:
    """Derive an ordered numeric pair for ``left <op> right`` (D-294). Certainty
    bar: refuse (as today) unless BOTH fields have numeric, writable metadata."""
    ml, mr = _field_meta(left, meta), _field_meta(right, meta)
    if ml is None or mr is None:
        raise _Undecidable(
            "field-to-field comparison without field metadata (needs D-294 rail)")
    if ml["field_type"] not in _NUMERIC or mr["field_type"] not in _NUMERIC:
        raise _Undecidable("field-to-field comparison on non-numeric field(s)")
    if ml.get("is_calculated") or mr.get("is_calculated"):
        raise _Undecidable("field-to-field comparison on a calculated field (not writable)")
    eff = op if want_true else _NEG[op]              # violate: make `left eff right` TRUE
    a, b = _CROSS_PAIR[eff]
    return {left.path[-1]: a, right.path[-1]: b}


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


def _nonblank_value(fm: Optional[dict]) -> Any:
    """A certain, DETERMINISTIC non-blank value for a field given its D-294
    metadata dict (or None) — else ``_UNSYNTHESIZABLE``. Deterministic (fixed)
    values keep S3 emission replay-stable; this is a DISTINCT concern from
    ``world._fill_value`` (S4 padding-validity, which uses live now()/today()),
    so it is intentionally NOT shared."""
    if not fm:
        return _UNSYNTHESIZABLE
    ft = (fm.get("field_type") or "")
    if ft in _NUMERIC:
        return 1
    if ft == "boolean":
        return True
    if ft in _TEXTUAL:
        length = fm.get("length") or 0
        v = "PQA"
        return v[:length] if 0 < length < len(v) else v
    if ft == "email":
        return "pqa@example.com"
    if ft == "url":
        return "https://example.com"
    if ft == "phone":
        return "5555550100"
    if ft == "date":
        return "2000-01-01"
    if ft == "datetime":
        return "2000-01-01T00:00:00Z"
    if ft == "picklist":
        vals = fm.get("picklist_values")
        return vals[0] if vals else _UNSYNTHESIZABLE
    return _UNSYNTHESIZABLE


def _self_check(node, payload: dict, want_true: bool) -> None:
    """D-294 polarity guard: a metadata-synthesized payload must make ``node``
    evaluate to ``want_true`` (via the D-113 ``evaluate`` primitive). ``evaluate``
    is Kleene — ``NonEvaluable`` (bare field, field-to-field) means no check is
    possible, so we trust the construction. A concrete disagreement is a bug ->
    refuse (never emit a payload that does not actually fire the rule)."""
    result = evaluate(node, payload)
    if isinstance(result, NonEvaluable):
        return
    if result != want_true:
        raise _Undecidable(
            f"synthesized value failed the evaluate self-check for {node!r}")


def _satisfy_function(node: FunctionCall, want_true: bool, meta: dict) -> dict[str, Any]:
    if node.name in ("ISBLANK", "ISNULL"):
        field = _single_field(node)
        if want_true:
            return {field: None}                     # blank fires the rejection
        # D-294: NOT(ISBLANK) fires when the field is NON-blank -> synthesize a
        # certain non-blank value from field metadata (else refuse — the bar).
        v = _nonblank_value((meta or {}).get(field))
        if v is _UNSYNTHESIZABLE:
            raise _Undecidable(f"NOT {node.name} (no synthesizable non-blank value)")
        payload = {field: v}
        _self_check(node, payload, want_true)        # evaluate confirms it fires
        return payload
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

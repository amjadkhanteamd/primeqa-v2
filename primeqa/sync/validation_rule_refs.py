"""ValidationRule REFERENCES extraction — bespoke field_refs writer (D-107,
slice 2, approach B). Closes corrections-log §17 for same-object refs.

Parses each VR's ``formula_text`` (the slice-1 parser), walks the AST for the
field references it implies, resolves the *same-object* ones to Field entities,
and writes ``validation_rule_field_refs`` rows directly — a single-purpose
writer that does NOT route through the property-less ``EdgeSpec`` junction
mirror (D-107 Fork B). ``derivation.py`` then builds the REFERENCES edges from
the junction, unchanged.

Scope (D-107 / 2a + 1a):
  - same-object bare ref -> a row, ``reference_type`` read | priorvalue |
    ischanged (+ matching flag). The same field can yield multiple rows
    (``Amount <> PRIORVALUE(Amount)`` -> a ``read`` row and a ``priorvalue`` row).
  - cross-object dotted ref -> no row (deferred; needs cross-object relationship
    resolution) -> the VR's ``references_status`` becomes ``partial``.
  - ISNEW() -> no field, no row, does not reduce completeness (1a).
  - formula does not fully parse -> no rows, ``references_status`` = ``unparsed``
    (fail-loud, D-107 .1).
"""
from __future__ import annotations

from typing import Callable, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from primeqa.semantic.formula import (
    And, Comparison, FieldRef, FunctionCall, Not, Or, is_parsed, parse,
)

# parent_resolver-style callable: (entity_type=..., external_id=...) -> entity_id|None
Resolver = Callable[..., Optional[str]]

# enclosing change-context function -> reference_type for the field it wraps.
_CHANGE_CTX = {"PRIORVALUE": "priorvalue", "ISCHANGED": "ischanged"}


def _collect(node, ctx: str, out: list[tuple[tuple[str, ...], str]]) -> None:
    """Walk the AST, appending (field_path, reference_type) for every field ref.
    ``ctx`` is the reference_type implied by the enclosing change-context
    function (``read`` at the top). ISNEW contributes no field."""
    if isinstance(node, FieldRef):
        out.append((node.path, ctx))
    elif isinstance(node, FunctionCall):
        if node.name == "ISNEW":
            return                                   # record-level; no field (1a)
        sub = _CHANGE_CTX.get(node.name, "read")
        for a in node.args:
            _collect(a, sub, out)
    elif isinstance(node, Comparison):
        _collect(node.left, ctx, out)
        _collect(node.right, ctx, out)
    elif isinstance(node, (And, Or)):
        for o in node.operands:
            _collect(o, ctx, out)
    elif isinstance(node, Not):
        _collect(node.operand, ctx, out)
    # Literal: leaf, no field


def extract_field_refs(formula_text: Optional[str]):
    """Pure: parse + walk. Returns ``(parsed, refs, has_cross_object)`` where
    ``refs`` is a de-duplicated list of ``(field_path, reference_type)`` for the
    *same-object* refs (single-segment paths), and ``has_cross_object`` flags
    that >= 1 dotted (cross-object) ref was seen and skipped."""
    result = parse(formula_text or "")
    if not is_parsed(result):
        return (False, [], False)
    raw: list[tuple[tuple[str, ...], str]] = []
    _collect(result, "read", raw)
    refs: list[tuple[tuple[str, ...], str]] = []
    seen = set()
    has_cross_object = False
    for path, ref_type in raw:
        if len(path) > 1:                            # dotted -> cross-object (2a)
            has_cross_object = True
            continue
        key = (path, ref_type)
        if key not in seen:
            seen.add(key)
            refs.append(key)
    return (True, refs, has_cross_object)


def write_field_refs(
    conn: Connection, *, vr_entity_id: UUID, formula_text: Optional[str],
    parent_object_api: Optional[str], resolve: Resolver,
) -> str:
    """Extract + resolve + write ``validation_rule_field_refs`` rows for one VR,
    then set + return its ``references_status`` (complete | partial | unparsed)."""
    parsed, refs, has_cross_object = extract_field_refs(formula_text)
    if not parsed:
        _set_status(conn, vr_entity_id, "unparsed")
        return "unparsed"

    unresolved = False
    rows: list[dict] = []
    for path, ref_type in refs:
        field_eid = None
        if parent_object_api:
            field_eid = resolve(
                entity_type="Field",
                external_id=f"{parent_object_api}.{path[0]}",
            )
        if field_eid is None:
            unresolved = True                        # same-object, but not synced
            continue
        rows.append({
            "vr": str(vr_entity_id),
            "field": str(field_eid),
            "reference_type": ref_type,
            "is_priorvalue": ref_type == "priorvalue",
            "is_ischanged": ref_type == "ischanged",
            "is_isnew": False,
        })

    for r in rows:
        conn.execute(text("""
            INSERT INTO validation_rule_field_refs
                (validation_rule_entity_id, field_entity_id, reference_type,
                 is_priorvalue, is_ischanged, is_isnew)
            VALUES (CAST(:vr AS uuid), CAST(:field AS uuid), :reference_type,
                    :is_priorvalue, :is_ischanged, :is_isnew)
            ON CONFLICT DO NOTHING
        """), r)

    status = "partial" if (has_cross_object or unresolved) else "complete"
    _set_status(conn, vr_entity_id, status)
    return status


def _set_status(conn: Connection, vr_entity_id: UUID, status: str) -> None:
    conn.execute(text(
        "UPDATE validation_rule_details SET references_status = :s "
        "WHERE entity_id = CAST(:eid AS uuid)"
    ), {"s": status, "eid": str(vr_entity_id)})


def write_field_refs_for_validation_rules(
    conn: Connection, vr_pairs: list[tuple[dict, UUID]], resolve: Resolver,
) -> None:
    """Sync hook: for each ``(normalized, entity_id)`` ValidationRule, write its
    field_refs + status. ``normalized`` carries the raw Tooling formula
    (``errorConditionFormula``) and the phase-injected ``_parent_object_api_name``
    marker (same source the detail mapper reads)."""
    for normalized, eid in vr_pairs:
        formula_text = (normalized.get("errorConditionFormula")
                        or normalized.get("formula_text"))
        parent_object_api = normalized.get("_parent_object_api_name")
        write_field_refs(
            conn, vr_entity_id=eid, formula_text=formula_text,
            parent_object_api=parent_object_api, resolve=resolve,
        )

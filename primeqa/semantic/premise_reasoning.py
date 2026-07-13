"""Cross-record premise REASONING (Wave 3 CP1) — pure, deterministic.

Extends the Wave-2 premise representation (``flow_cross_record_premises``:
bounded Get-Records as ``{object, filters, single, guard, element}`` with
``('$Record', <subject field>)`` correlation markers) into executable
reasoning — WITHOUT arbitrary SOQL semantics:

- :func:`classify_relation` — HOW the queried records relate to the
  triggering record (child lookup via ``$Record.Id``, parent lookup via a
  subject lookup field, sibling set via a shared parent reference, or
  ``uncorrelated`` — the explicit refusal class: evidence cannot isolate a
  global query's rows to the test's own records).
- :data:`CARDINALITY` predicates — exists / not_exists / count_equals /
  count_at_least / count_less_than / single_record.
- :func:`staging_plan` — the deterministic records the evidence layer must
  stage so a cardinality predicate PROVABLY holds: per-record field values
  from the premise's own literal filters (so the staged rows MATCH the
  query), the correlation binding, and the optional DISTRACTOR row (one
  literal filter flipped) that discriminates a real filter from a
  count-everything query — the differential discipline.

Everything here is pure over premise dicts — no DB, no S1, no LLM. The
evidence layer (CP3) turns plans into recipe steps; value flips reuse
``witnesses.picklist_alternative``. Unsupported constructs return named
refusal strings, never guesses.
"""
from __future__ import annotations

from typing import Optional

CARDINALITY = ("exists", "not_exists", "count_equals", "count_at_least",
               "count_less_than", "single_record")

_MAX_STAGED = 5   # bounded: no plan stages more than this many rows


def classify_relation(premise: dict) -> dict:
    """How the premise's records relate to the triggering record.

    Returns ``{"kind", "correlation_field", "subject_field", "literals"}``:

    - ``child_lookup`` — a filter ``(f, EqualTo, ($Record, 'Id'))``: the
      queried rows are children referencing the subject via ``f``.
    - ``sibling_set`` — correlation on a NON-Id subject field (the FL07
      shape: lines sharing the subject's own parent reference).
    - ``parent_lookup`` — ``('Id', EqualTo, ($Record, <lookup>))``: the
      single parent the subject points to.
    - ``uncorrelated`` — no ``$Record`` marker: a global query; evidence
      cannot isolate its rows (the refusal class, stated explicitly).

    ``literals`` are the remaining constant filters (the rows' required
    state). >1 correlation marker → ``unsupported`` (outside the bounded
    grammar; explicit)."""
    corr = [(f, v) for f, op, v in premise.get("filters", ())
            if isinstance(v, tuple) and len(v) == 2 and v[0] == "$Record"]
    literals = [(f, op, v) for f, op, v in premise.get("filters", ())
                if not (isinstance(v, tuple) and v and v[0] == "$Record")]
    if len(corr) > 1:
        return {"kind": "unsupported",
                "reason": "multiple_correlation_markers",
                "correlation_field": None, "subject_field": None,
                "literals": tuple(literals)}
    if not corr:
        return {"kind": "uncorrelated", "correlation_field": None,
                "subject_field": None, "literals": tuple(literals)}
    (fld, (_tag, subj_field)), = corr
    if fld == "Id":
        kind = "parent_lookup"
    elif subj_field == "Id":
        kind = "child_lookup"
    else:
        kind = "sibling_set"
    return {"kind": kind, "correlation_field": fld,
            "subject_field": subj_field, "literals": tuple(literals)}


def _record_template(relation: dict) -> tuple:
    """The per-row field state a staged record needs to MATCH the premise's
    query: every literal-EqualTo filter set to its literal; IsNull(True)
    filters by omission; IsNull(False) marked required-any. Returns
    ``(template_pairs, required_any, refusal)``."""
    tmpl, required_any = [], []
    for f, op, v in relation["literals"]:
        if op == "EqualTo":
            tmpl.append((f, v))
        elif op == "IsNull" and v is True:
            continue          # matched by omission
        elif op == "IsNull" and v is False:
            required_any.append(f)
        else:
            return ((), (), f"unstageable_filter:{f}:{op}")
    return (tuple(tmpl), tuple(required_any), None)


def staging_plan(premise: dict, predicate: str,
                 n: Optional[int] = None) -> dict:
    """The deterministic staging that makes ``predicate`` PROVABLY hold
    over the premise's query, or ``{"refusal": <named>}``.

    Shape: ``{"create_matching": k, "template": ((field, value)...),
    "required_any": (fields...), "correlate": {"kind", "field",
    "subject_field"}, "distractor": {"flip_field", "from_value"}|None,
    "assert": {"predicate", "n"}}``.

    - the DISTRACTOR row (present whenever a literal EqualTo filter exists
      and the predicate counts) flips ONE literal filter so a query that
      ignored its filters would over-count — the differential that makes a
      green mean the FILTER, not just the correlation;
    - ``parent_lookup`` premises never stage (the parent exists as the
      subject's own lookup target) — plans on them are count-free;
    - ``uncorrelated`` premises refuse (cannot isolate)."""
    rel = classify_relation(premise)
    if rel["kind"] == "uncorrelated":
        return {"refusal": "uncorrelated_premise_cannot_be_isolated"}
    if rel["kind"] == "unsupported":
        return {"refusal": rel["reason"]}
    if predicate not in CARDINALITY:
        return {"refusal": f"unknown_predicate:{predicate}"}
    if predicate in ("count_equals", "count_at_least", "count_less_than"):
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            return {"refusal": "count_predicate_needs_n"}
    if rel["kind"] == "parent_lookup" and predicate not in (
            "exists", "not_exists", "single_record"):
        return {"refusal": "parent_lookup_supports_existence_only"}

    tmpl, required_any, bad = _record_template(rel)
    if bad:
        return {"refusal": bad}

    k = {"exists": 1, "not_exists": 0, "single_record": 1,
         "count_equals": n, "count_at_least": (n or 0),
         "count_less_than": max((n or 1) - 1, 0)}[predicate]
    if k is None or k > _MAX_STAGED:
        return {"refusal": f"staging_bound_exceeded:{k}>{_MAX_STAGED}"}

    distractor = None
    eq_literals = [(f, v) for f, v in tmpl]
    if eq_literals and predicate != "not_exists":
        distractor = {"flip_field": eq_literals[0][0],
                      "from_value": eq_literals[0][1]}
    return {
        "create_matching": k,
        "template": tmpl,
        "required_any": required_any,
        "correlate": {"kind": rel["kind"],
                      "field": rel["correlation_field"],
                      "subject_field": rel["subject_field"]},
        "distractor": distractor,
        "assert": {"predicate": predicate, "n": n},
    }

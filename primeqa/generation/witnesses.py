"""Witness synthesis — the single entry point for deterministic test-value
generation (DEBT E2 closed at C3).

A *witness* is a value the substrate derives — never the model — so a test's
staged state is deterministic run-to-run (identity stability) and provably
consistent with the org's own rules (format patterns, guard intervals). Four
generator classes live behind this module today:

- **verified negatives** (``verified_negative`` — boundary-violating values
  for prohibition claims; D-346 discipline),
- **branch satisfaction** (``decision_branch`` — values satisfying a
  declarative Boolean rule arm),
- **transform witnesses** (here: :func:`synthesize_transform_witness`,
  moved from ``governance_core`` — the (canonical, raw) pair for a
  before-save rewrite, FL02 slice),
- **band-interval witnesses** (here: :func:`interval_witness` +
  :func:`guard_witness_values`, new at C3 — a value strictly interior to a
  first-match ladder band, derived from the arm's positive guard and the
  negation-context of every prior rule).

Every generator is pure, bounded, and honest: outside its grammar it returns
``None`` / a named refusal detail — never a guess.
"""
from __future__ import annotations

import re as _re
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Callable, Optional

from primeqa.semantic.entity_attributes import apply_transform_chain
from primeqa.generation.verified_negative import regex_matching_value

__all__ = [
    "boundary_witnesses",
    "picklist_alternative",
    "regex_matching_value",
    "synthesize_transform_witness",
    "interval_witness",
    "guard_witness_values",
]


def synthesize_transform_witness(chain, patterns, opaque_rules):
    """FL02 slice: derive the (canonical, raw) witness pair for a transform
    test — ``canonical`` satisfies every synthesizable format rule and
    ``raw`` is the deterministic de-transformation the create stages
    (post-save the org must produce ``canonical``). Returns the pair, or a
    refusal-detail STRING when honesty demands it (opaque governing rule, an
    unsynthesizable pattern, or a chain whose inversion cannot produce a
    distinguishing raw value)."""
    if opaque_rules:
        return (f"active validation rule(s) {opaque_rules} govern the "
                f"transformed field but their formulas are not readable — "
                f"cannot derive a witness the save is known to accept")
    if patterns:
        canonical = regex_matching_value(patterns[0])
        if canonical is None:
            return (f"the field's format pattern {patterns[0]!r} is outside "
                    f"the bounded synthesis grammar — cannot derive a "
                    f"format-valid witness")
        for pat in patterns[1:]:
            try:
                if not _re.fullmatch(pat, canonical):
                    return (f"no single witness satisfies every governing "
                            f"format pattern ({patterns})")
            except _re.error:
                return f"format pattern {pat!r} is not compilable"
        # the canonical must be a FIXED POINT of the chain — the org stores
        # the transformed value, so a format whose members the chain rewrites
        # out of the format is a genuinely inconsistent flow+rule pair.
        if apply_transform_chain(chain, canonical) != canonical:
            return (f"the format-valid witness {canonical!r} is not stable "
                    f"under the transform chain {list(chain)} — the flow "
                    f"would rewrite values out of their own format rule")
    else:
        # no format rule constrains the field: normalize the deterministic
        # seed through the chain so the canonical IS the post-save value.
        canonical = apply_transform_chain(chain, "Sample Value 1")
    raw = canonical
    for fn in reversed(chain):
        if fn == "UPPER":
            raw = raw.lower()
        elif fn == "LOWER":
            raw = raw.upper()
        elif fn == "TRIM":
            raw = " " + raw + " "
    if raw == canonical or apply_transform_chain(chain, raw) != canonical:
        return (f"cannot construct a raw witness the transform chain "
                f"{list(chain)} distinguishably normalizes to a valid value")
    return (canonical, raw)


# ── band-interval witnesses (C3) ─────────────────────────────────────

# the numeric comparison operators the interval grammar understands —
# the IR guard grammar's ladder subset (equality/null handled separately)
_NUMERIC_OPS = frozenset({
    "GreaterThanOrEqualTo", "GreaterThan",
    "LessThanOrEqualTo", "LessThan"})


def _holds(value: Decimal, op: str, threshold: Decimal) -> bool:
    if op == "GreaterThanOrEqualTo":
        return value >= threshold
    if op == "GreaterThan":
        return value > threshold
    if op == "LessThanOrEqualTo":
        return value <= threshold
    if op == "LessThan":
        return value < threshold
    return False


def _as_decimal(v) -> Optional[Decimal]:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def interval_witness(constraints, scale: int):
    """A value strictly interior to the band the constraints describe, or
    ``None`` when the band is empty or a constraint falls outside the
    numeric grammar. ``constraints`` is a list of ``(op, threshold,
    negated)`` triples on ONE field; ``scale`` is the field's decimal scale
    (the witness is quantized to it, and the interior step is one unit of
    it). Strictly-interior PREFERRED — boundary EDGES belong to the D-346
    verified-negative discipline, not the fire arm; a degenerate band whose
    only member IS a bound falls back to that bound (staging it still
    genuinely fires the arm). Candidate order: midpoint-by-scale when both
    bounds exist, ``lo + unit`` for a bottom-bounded band (the top band),
    ``hi - unit`` for a top-bounded band (the default band); every
    candidate is verified against the ORIGINAL constraints before it is
    returned."""
    if not constraints:
        return None
    unit = Decimal(1).scaleb(-int(scale or 0))
    checks = []            # (op, threshold, negated) with Decimal thresholds
    lo = hi = None         # tightest LEGAL bounds (inclusive after adjustment)
    lo_t = hi_t = None     # the ORIGINAL threshold that produced each bound —
    #                        the preferred candidate is threshold ± one unit,
    #                        uniformly, so an exclusive bound is never stepped
    #                        twice
    for op, threshold, negated in constraints:
        t = _as_decimal(threshold)
        if t is None or op not in _NUMERIC_OPS:
            return None
        checks.append((op, t, bool(negated)))
        # a negated constraint is its complement
        eff = {"GreaterThanOrEqualTo": "LessThan",
               "GreaterThan": "LessThanOrEqualTo",
               "LessThanOrEqualTo": "GreaterThan",
               "LessThan": "GreaterThanOrEqualTo"}[op] if negated else op
        if eff in ("GreaterThanOrEqualTo", "GreaterThan"):
            bound = t if eff == "GreaterThanOrEqualTo" else t + unit
            if lo is None or bound > lo:
                lo, lo_t = bound, t
        else:
            bound = t if eff == "LessThanOrEqualTo" else t - unit
            if hi is None or bound < hi:
                hi, hi_t = bound, t
    if lo is not None and hi is not None:
        candidates = [((lo + hi) / 2).quantize(unit, rounding=ROUND_HALF_EVEN),
                      lo_t + unit, hi_t - unit, lo, hi]
    elif lo is not None:
        candidates = [lo_t + unit, lo]     # the top band: threshold + unit
    elif hi is not None:
        candidates = [hi_t - unit, hi]     # the default band: below the lowest
    else:
        return None
    for cand in candidates:
        cand = cand.quantize(unit)
        ok = all(_holds(cand, op, t) is not neg for op, t, neg in checks)
        if ok:
            return int(cand) if cand == cand.to_integral_value() else float(cand)
    return None


def picklist_alternative(picklist_values, exclude) -> Optional[str]:
    """The FIRST active picklist value not in ``exclude`` — the deterministic
    "some other state" witness both transition shapes need (C4: a create
    that does NOT meet the entry filter; C5: an update that leaves the
    prior state). ``None`` when the value set is absent or exhausted —
    callers refuse with their own named detail."""
    for v in picklist_values or ():
        if v not in exclude:
            return v
    return None


def boundary_witnesses(constraints, scale: int) -> tuple:
    """Wave 2 (CP3): the EDGE values of the band the constraints describe —
    for each ORIGINAL threshold, the two adjacent quantized values with
    their side relative to the band: ``(value, "inside"|"outside",
    threshold)``. Composes the D-346 boundary discipline for first-match
    ladders: the inside edge must fire THIS arm, the outside edge must
    fire the neighbour — together they pin the threshold exactly.
    Deterministic; same constraint grammar as :func:`interval_witness`
    (``(op, threshold, negated)`` triples on ONE field); ``()`` outside
    the grammar or when a candidate fails re-verification."""
    if not constraints:
        return ()
    unit = Decimal(1).scaleb(-int(scale or 0))
    checks = []
    for op, threshold, negated in constraints:
        t = _as_decimal(threshold)
        if t is None or op not in _NUMERIC_OPS:
            return ()
        checks.append((op, t, bool(negated)))

    def _inside(v: Decimal) -> bool:
        return all(_holds(v, op, t) is not neg for op, t, neg in checks)

    out = []
    for _op, t, _neg in checks:
        for cand in (t, t - unit, t + unit):
            cand = cand.quantize(unit)
            side = "inside" if _inside(cand) else "outside"
            val = int(cand) if cand == cand.to_integral_value() \
                else float(cand)
            entry = (val, side, int(t) if t == t.to_integral_value()
                     else float(t))
            if entry not in out:
                out.append(entry)
    # keep only the tightest straddle per threshold: the edge value ON or
    # nearest the threshold per side (drop duplicates two units away)
    per: dict = {}
    for val, side, t in out:
        key = (t, side)
        best = per.get(key)
        if best is None or abs(Decimal(str(val)) - Decimal(str(t))) \
                < abs(Decimal(str(best)) - Decimal(str(t))):
            per[key] = val
    return tuple(sorted(
        (v, side, t) for (t, side), v in per.items()))


def guard_witness_values(guard, negated_guards, exclude_field: str,
                         scale_of: Callable[[str], Optional[int]]):
    """Derive the create-state witnesses that make ONE grounded arm fire:
    ``("ok", {bare_field: value})`` or ``("refuse", detail)``.

    Condition disposition, per field:

    - conditions on ``exclude_field`` (the observed effect field) are
      SKIPPED — k16 forbids staging it, and the null-guarded self-default
      (FL01's shape) is satisfied by that very omission;
    - ``IsNull`` conditions are SKIPPED — satisfied by omission (true) or
      by whatever value another rail stages (false); never a value here;
    - all-numeric conditions → :func:`interval_witness` (empty band or
      unknown scale → refuse: the arm is unfireable / unverifiable);
    - a single positive ``EqualTo`` → that literal;
    - anything else (``NotEqualTo``, negated equality, mixed shapes) →
      refuse with the named limit — mixed operators inside one band are
      outside the honest grammar (roadmap family A limits).

    An empty result ``("ok", {})`` is legitimate: every condition was
    omission-satisfied (FL01 stages nothing, byte-unchanged)."""
    by_field: dict = {}
    for conds, negated in ((guard, False), (negated_guards, True)):
        for cond in conds or ():
            try:
                field, op, value = cond
            except (TypeError, ValueError):
                return ("refuse", f"unreadable guard condition {cond!r}")
            if field == exclude_field or op == "IsNull":
                continue
            by_field.setdefault(field, []).append((op, value, negated))
    out: dict = {}
    for field in sorted(by_field):
        conds = by_field[field]
        if all(op in _NUMERIC_OPS for op, _, _ in conds):
            scale = scale_of(field)
            if scale is None:
                return ("refuse",
                        f"cannot derive an in-band witness for {field!r} — "
                        f"the field's numeric scale is not readable")
            wit = interval_witness(conds, scale)
            if wit is None:
                return ("refuse",
                        f"the arm's guard interval on {field!r} is empty or "
                        f"outside the bounded synthesis grammar — no create "
                        f"state can fire this band")
            out[field] = wit
        elif (len(conds) == 1 and conds[0][0] == "EqualTo"
                and not conds[0][2]):
            out[field] = conds[0][1]
        else:
            return ("refuse",
                    f"the arm's guard on {field!r} mixes operators outside "
                    f"the witness grammar "
                    f"({sorted({op for op, _, _ in conds})}) — cannot "
                    f"derive the create state that fires it")
    return ("ok", out)

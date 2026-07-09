"""Constraint-aware fixture solver — the SATISFACTION operation of the Constraint
IR (req-315 Phase-3 spike, D-347).

The Constraint IR (``CONSTRAINT_IR_SPIKE.md``) defines TWO operations over one
representation. D-343 built **entailment** — "which VR does the claim NECESSARILY
fire?" (selection). This is the second: **satisfaction** — find a field assignment
that FIRES a target rule while every SIBLING active rule stays FALSE (a negative
that trips ONLY the rule under test), or report **UNSAT**.

Today that isolation is handled OPERATIONALLY: ``vr_conflict`` (D-337) REFUSES at
authoring when the staged state provably fires a DIFFERENT rule, and R1 padding
(D-119, ``execution_engine/world.py``) fills sibling-satisfying values at RUN time.
This spike lifts it into an authoring-time SOLVE over the IR. Its unique additions
over refuse+pad:
  - **UNSAT detection** — a target whose firing provably CONFLICTS with a sibling's
    non-firing (on a value-under-test field) is refused at authoring, rather than
    emitting a test R1 can never isolate;
  - **determinism** — the isolating assignment is COMPUTED, not runtime-padded.

It reuses the certainty-bounded :func:`verified_negative._satisfy`, so it inherits
the same "refuse rather than guess" floor (an underivable sibling is left to R1 —
best-effort, never a guessed value). **NOT wired into emission** — a demonstrated
spike; production wiring waits for a triggering gap (an org where refuse+pad is
insufficient), per ``FIXTURE_SOLVER_SPIKE.md``. Generalizes beyond VRs (Flow entry
criteria, approval criteria, required fields, record types) — same IR, same solve.
"""
from __future__ import annotations

from typing import Optional

from primeqa.generation.verified_negative import (
    _Undecidable, _satisfy, _unwrap_soft,
)
from primeqa.semantic.formula import And, is_parsed, parse
from primeqa.semantic.formula.eval import evaluate


def _falsify_off_target(sibling_ast, avoid: set, meta: dict) -> Optional[dict]:
    """A certain assignment that makes ``sibling_ast`` FALSE while touching NO field
    in ``avoid`` (the fields under test) — the k16 discipline (D-119): isolate a
    sibling by padding a value it owns, never by disturbing the value under test.
    Falsifying ANY conjunct falsifies an ``And``; try each and take the first whose
    fields avoid the target's. ``None`` when the sibling can ONLY be falsified by
    changing a target field (un-isolatable → the caller's UNSAT)."""
    conjuncts = list(sibling_ast.operands) if isinstance(sibling_ast, And) else [sibling_ast]
    for c in conjuncts:
        try:
            asg = _unwrap_soft(_satisfy(c, False, meta))   # make this conjunct false
        except _Undecidable:
            continue
        if asg and not (set(asg) & avoid):
            return asg
    return None


def solve_fixture(target_formula: str, sibling_formulas,
                  field_metadata: Optional[dict] = None) -> Optional[dict]:
    """A field assignment that FIRES ``target_formula`` and keeps every parseable
    sibling FALSE — the isolating fixture — or ``None`` (UNSAT: the target is not
    certainly derivable, or a firing sibling can only be silenced by changing a
    value under test).

    Reuses the certainty-bounded :func:`verified_negative._satisfy`; a sibling that
    does not fire under the fixture is left alone, an unparseable / underivable one
    is left to run-time R1 padding (never guessed). Single-pass and bounded — a
    production solver would iterate to a fixpoint and backtrack across siblings;
    this demonstrates the IR's SATISFACTION operation + UNSAT detection."""
    meta = field_metadata or {}
    tast = parse(target_formula)
    if not is_parsed(tast):
        return None
    try:
        result = _unwrap_soft(_satisfy(tast, True, meta))     # the target FIRES
    except _Undecidable:
        return None                                           # not certainly derivable
    target_fields = set(result)
    for sib in sibling_formulas:
        sast = parse(sib)
        if not is_parsed(sast):
            continue                                          # unparseable -> run-time R1
        # ISOLATION uses run-time semantics: an ABSENT field defaults to BLANK at
        # run time, so a sibling that fires under blank-defaults WILL fire — use
        # `evaluate` (absent=blank), NOT `_fires` (absent=unknown, the SELECTION
        # semantic). This is the key satisfaction-vs-entailment distinction.
        if evaluate(sast, result) is not True:
            continue                                          # provably won't fire
        off = _falsify_off_target(sast, target_fields, meta)
        if off is None:
            return None                                       # UNSAT: un-isolatable
        result.update(off)                                    # avoids target fields
    return result if evaluate(tast, result) is True else None


"""DecisionBranchCoverage — logical branch coverage over ``A AND (B OR C) AND D``
(the VR03 arc; AK 2026-07-10).

Not generic compound-rule support: the bounded shape is a top-level AND carrying
EXACTLY ONE disjunction (the decision) plus one or more gate conjuncts. The
experiment decomposes the Boolean structure into:

  - one ISOLATED FIRING witness per branch GROUP of the disjunction (branches
    on the same field are one group — ``Risk = High OR Risk = Critical`` is the
    Risk branch), every NON-TARGET branch held provably FALSE, both firing arms
    requiring target attribution;
  - one NECESSITY CONTROL per gate (that gate falsified — minimally, at its
    boundary — everything else held in the firing configuration) plus the
    OR-gate control (every branch false): each must be ACCEPTED, proving the
    gate is necessary and not incidental.

Witness values are minimally violating / boundary-honest (D-352 composes
through ``_satisfy``): ``Discount > 0.15`` fires at 15.01% — which naturally
stays below VR02's 20% gate — and the ``Deal_Value`` gate control sits at
exactly 1,000,000 ("greater than" is false AT the boundary). Sibling isolation
reuses the existing D-354 completion (off-target fills, typed values) — no
rule-specific padding. Every arm is verified over the IR in its direction
(run-time blank semantics) and no sibling may provably fire in any arm.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from primeqa.generation.fixture import (
    ROLE_SIBLING_ISOLATION, ROLE_TARGET_ACTIVATION, ROLE_TARGET_WITNESS,
    _falsify_off_target,
)
from primeqa.generation.transition import (
    TransitionState, _bare, _flatten_and, evaluate_transition,
)
from primeqa.generation.verified_negative import (
    _Undecidable, _merge, _satisfy, _unwrap_soft,
)
from primeqa.semantic.formula import (
    And, Comparison, FieldRef, FunctionCall, Not, Or, is_parsed, parse,
)


@dataclass(frozen=True)
class DecisionArm:
    label: str
    payload: dict
    expect_reject: bool
    varied_field: str          # the arm's discriminating dimension (read-back / diagnostics)
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionBranchExperiment:
    arms: tuple                # (firing arms first, then the gate controls)


def decision_branch_shape(formula_text: str) -> Optional[tuple]:
    """``(gates, disjunction)`` when the formula is the bounded
    ``AND(gates..., OR(branches...))`` decision shape (>=1 gate, one OR with
    >=2 branches, no org-state functions), else ``None``."""
    ast = parse(formula_text)
    if not is_parsed(ast) or not isinstance(ast, And):
        return None
    conjuncts = _flatten_and(ast)
    disj = [c for c in conjuncts if isinstance(c, Or)]
    gates = [c for c in conjuncts if not isinstance(c, Or)]
    if len(disj) != 1 or not gates or len(disj[0].operands) < 2:
        return None
    return tuple(gates), disj[0]


def _branch_field(node) -> Optional[str]:
    """The bare field a disjunct is ABOUT (grouping key), else None."""
    if isinstance(node, Comparison) and isinstance(node.left, FieldRef):
        return node.left.path[-1]
    if (isinstance(node, FunctionCall)
            and node.args and isinstance(node.args[0], FieldRef)):
        return node.args[0].path[-1]
    if isinstance(node, Not):
        return _branch_field(node.operand)
    if isinstance(node, FieldRef):
        return node.path[-1]
    return None


def _gate_field(node) -> Optional[str]:
    return _branch_field(node)


def satisfy_decision_branches(formula_text: str, meta: Optional[dict] = None,
                              sibling_items=None
                              ) -> Optional[DecisionBranchExperiment]:
    """Derive the DecisionBranchCoverage experiment, or ``None`` (out of
    grammar / underivable / an unverifiable or un-isolatable arm)."""
    meta = meta or {}
    shape = decision_branch_shape(formula_text)
    if shape is None:
        return None
    gates, disj = shape
    ast = parse(formula_text)
    if sibling_items:
        sibling_items = [(n, t) for n, t in sibling_items if t != formula_text]
    parsed_sibs = []
    for name, text in sorted(set((str(n), str(t))
                                 for n, t in (sibling_items or ()))):
        sast = parse(text)
        if is_parsed(sast):
            parsed_sibs.append((name, sast))

    try:
        # The gate configuration (all gates TRUE — minimal witnesses via _satisfy).
        gates_true: dict = {}
        gate_prov: dict = {}
        for g in gates:
            asg = _unwrap_soft(_merge([_satisfy(g, True, meta)]))
            for f, v in asg.items():
                if f in gates_true and gates_true[f] != v:
                    return None
                gates_true[f] = v
                gate_prov[f] = (ROLE_TARGET_ACTIVATION,
                                f"gate satisfied: {_src(g)}")

        # Branch groups (by field, formula order): firing value + all-false values.
        groups: list = []                    # (field, representative_branch)
        seen: set = set()
        for b in disj.operands:
            f = _branch_field(b)
            if f is None:
                return None
            if f not in seen:
                seen.add(f)
                groups.append((f, b))
        branch_false: dict = {}              # every branch group held FALSE
        false_prov: dict = {}
        for b in disj.operands:
            asg = _unwrap_soft(_merge([_satisfy(b, False, meta)]))
            for f, v in asg.items():
                if f in branch_false and branch_false[f] != v:
                    return None               # same-field branches must reconcile
                branch_false[f] = v
                false_prov[f] = (ROLE_TARGET_ACTIVATION,
                                 f"branch held false: {_src(b)}")
    except _Undecidable:
        return None

    def _verified_arm(label, payload, expect_reject, varied, prov):
        """Sibling-complete + verify the arm in its direction, or None."""
        fills = dict(payload)
        arm_prov = dict(prov)
        protected = set(payload)
        for name, sast in parsed_sibs:
            ts = TransitionState(prior=_bare(fills), next=_bare(fills))
            if evaluate_transition(sast, ts, absent="blank") is not True:
                continue
            off = _falsify_off_target(sast, protected, meta)
            if off is None:
                return None
            for f, v in off.items():
                fills[f] = v
                protected.add(f)
                arm_prov[f] = (ROLE_SIBLING_ISOLATION, name)
        ts = TransitionState(prior=_bare(fills), next=_bare(fills))
        want = True if expect_reject else False
        if evaluate_transition(ast, ts, absent="blank") is not want:
            return None
        for name, sast in parsed_sibs:
            if evaluate_transition(sast, ts, absent="blank") is True:
                return None
        return DecisionArm(label=label, payload=fills,
                           expect_reject=expect_reject, varied_field=varied,
                           provenance=arm_prov)

    arms: list = []
    # 1..N: one ISOLATED firing arm per branch group.
    for f_target, branch in groups:
        try:
            hit = _unwrap_soft(_merge([_satisfy(branch, True, meta)]))
        except _Undecidable:
            return None
        payload = {**gates_true,
                   **{f: v for f, v in branch_false.items()
                      if f != f_target},              # non-targets held false
                   **hit}
        prov = {**gate_prov,
                **{f: p for f, p in false_prov.items() if f != f_target},
                **{f: (ROLE_TARGET_WITNESS, f"branch fires: {_src(branch)}")
                   for f in hit}}
        arm = _verified_arm(f"branch:{f_target}", payload, True, f_target, prov)
        if arm is None:
            return None
        arms.append(arm)

    # The OR-gate control: every branch false, gates true → must be ACCEPTED.
    payload = {**gates_true, **branch_false}
    prov = {**gate_prov, **false_prov}
    arm = _verified_arm("control:or-gate", payload, False,
                        next(iter(branch_false), ""), prov)
    if arm is None:
        return None
    arms.append(arm)

    # One NECESSITY control per gate: that gate falsified (minimally — its
    # boundary), everything else in the FIRST firing configuration.
    firing0 = arms[0].payload
    for g in gates:
        gf = _gate_field(g)
        try:
            off = _unwrap_soft(_merge([_satisfy(g, False, meta)]))
        except _Undecidable:
            return None
        payload = {**{k: v for k, v in firing0.items()
                      if k not in off}, **off}
        prov = {**{k: arms[0].provenance.get(
                       k, (ROLE_TARGET_ACTIVATION, "held from the firing arm"))
                   for k in payload},
                **{f: (ROLE_TARGET_ACTIVATION,
                       f"gate falsified: {_src(g)} (the necessity control)")
                   for f in off}}
        arm = _verified_arm(f"control:{gf}", payload, False, gf or "", prov)
        if arm is None:
            return None
        arms.append(arm)

    return DecisionBranchExperiment(arms=tuple(arms))


def _src(node) -> str:
    from primeqa.generation.transition import _plain_src
    return _plain_src(node)

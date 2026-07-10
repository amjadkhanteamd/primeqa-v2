"""Transition state — the TRANSITION context dimension of the shared IR (VR10 arc).

AK's instruction (2026-07-10): make transition state EXPLICIT in the shared
Constraint/Context IR rather than adding an ``ISCHANGED`` special case to
derivation — one representation consumed identically by VR10 (the approval
transition gate) and VR05 (the ``PRIORVALUE`` lock), and later by the
TRANSITION-dimension ContextDifferential.

The representation is a two-phase world:

    TransitionState(prior = the record before the update,
                    next  = the record after it)

Salesforce's org-state functions become ordinary, decidable predicates over it:
``ISCHANGED(f)`` ≡ ``prior[f] != next[f]``; ``PRIORVALUE(f)`` reads ``prior``;
``ISNEW()`` is False on an update (a transition has a prior record); every
ordinary field ref reads ``next``. The single-phase evaluators (``evaluate``,
``vr_conflict._fires``) return unknown for these functions — correctly, since a
flat payload carries no transition — so nothing existing changes behaviour.

Two operations, mirroring the IR's settled split (D-343 / D-347):

- :func:`evaluate_transition` — tri-state Kleene evaluation over a
  ``TransitionState``, with the SAME two absence modes the IR already
  distinguishes: ``absent='unknown'`` (authoring proof — never manufacture a
  fire from a field nothing pinned) and ``absent='blank'`` (run-time
  satisfaction — an unstaged field defaults to blank, D-347's finding).
- :func:`satisfy_transition` — the satisfaction operation for a
  TRANSITION-shaped prohibition: a :class:`TransitionWitness` ``(setup,
  changes)`` — the setup create holds the non-violating prior state, the update
  IS the transition — violating **exactly one** approval branch (the first
  derivable-and-isolatable disjunct in formula order, its value minimally
  violating per D-352) while every other branch and every sibling control is
  satisfied (the D-354 completion discipline, transition-aware: VR05's
  ``PRIORVALUE`` gate is provably silent because the witness's prior state says
  so). UNSAT / out-of-grammar → ``None`` (refuse-not-guess).

Bounded grammar (deliberately narrow, the certainty bar): a top-level AND of
transition atoms (``ISCHANGED(f)``, to-state / ``PRIORVALUE`` constraints),
plain single-phase conjuncts, and AT MOST ONE disjunction (the approval-branch
OR). Anything else refuses. One date pragmatic: falsifying a
``field < TODAY()`` branch stages the deterministic far-future constant
``2099-12-31`` (replay-stable; S3 never reads now()) — the honest bridge until
the VR06 temporal-boundary stage lands real relative-date semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from primeqa.generation.fixture import (
    ROLE_CONTEXT, ROLE_SIBLING_ISOLATION, ROLE_TARGET_ACTIVATION,
    ROLE_TARGET_WITNESS, _falsify_off_target,
)
from primeqa.generation.formula_expectation import as_decimal
from primeqa.generation.verified_negative import (
    _Undecidable, _merge, _satisfy, _unwrap_soft,
)
from primeqa.generation.vr_conflict import _ORG_STATE_FUNCS, _text_eq
from primeqa.semantic.formula import (
    And,
    Comparison,
    FieldRef,
    FunctionCall,
    Literal,
    Not,
    Or,
    is_parsed,
    parse,
    walk,
)

# The deterministic far-future date used to falsify a `field < TODAY()` branch
# (and satisfy its NOT-ISBLANK sibling). Replay-stable by construction; sound
# for any plausible run date. Replaced by real relative-date semantics at the
# VR06 temporal-boundary stage.
FAR_FUTURE_DATE = "2099-12-31"

_MISSING = object()

_ORDER = {
    "=": lambda a, b: a == b, "<>": lambda a, b: a != b,
    "<": lambda a, b: a < b, ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
}
_FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "=", "<>": "<>"}


@dataclass(frozen=True)
class TransitionState:
    """The two-phase world: ``prior`` (the record before the update) and
    ``next`` (after it). Bare-keyed, formula-domain values."""
    prior: dict
    next: dict


@dataclass(frozen=True)
class TransitionWitness:
    """The satisfaction result for a transition-shaped prohibition: the
    ``setup`` create (the prior state, plus every gate value, falsified branch
    and sibling-isolation fill — everything held through the update) and the
    ``changes`` update that IS the transition the org must reject. Provenance
    classifies every staged value (AK Option-1 roles).

    ``entry_changes`` (the VR05 arc): when the witness's PRIOR state is itself
    GATED by a sibling transition control (Stage=Approved is entered only past
    VR10's gate), a direct create into that state would BYPASS the org's own
    controls — so the prior state is established through the LEGITIMATE path:
    the create stages the entry control's acceptance fixture and
    ``entry_changes`` is the first, expected-to-SUCCEED update (the org's own
    transition), after which ``changes`` is the mutation under test. Empty →
    the ordinary 2-step pair."""
    setup: dict
    changes: dict
    provenance: dict = field(default_factory=dict)   # {field: (role, source)}
    violated_branch: str = ""                        # human: the ONE violated condition
    entry_changes: dict = field(default_factory=dict)


def has_transition_semantics(formula_text: str) -> bool:
    """Does the formula carry org-state (transition) functions at all?"""
    ast = parse(formula_text)
    if not is_parsed(ast):
        return False
    return any(isinstance(n, FunctionCall) and n.name in _ORG_STATE_FUNCS
               for n in walk(ast))


# ---------------------------------------------------------------------------
# evaluate_transition — Kleene over the two-phase world
# ---------------------------------------------------------------------------

def evaluate_transition(ast, ts: TransitionState, *,
                        absent: str = "unknown") -> Optional[bool]:
    """Tri-state truth of ``ast`` over ``ts``. ``absent='unknown'`` is the
    authoring-proof mode (an unpinned field decides nothing); ``absent='blank'``
    is the run-time satisfaction mode (an unstaged field is blank at run time,
    D-347). ``TODAY()`` comparisons are unknown in both modes (no clock at
    authoring); every other org-state function is decidable over the pair."""
    blank = absent == "blank"

    def val(node) -> Any:
        """The VALUE of an expression node, or ``_MISSING`` (undetermined)."""
        if isinstance(node, Literal):
            return node.value
        if isinstance(node, FieldRef):
            if node.is_dotted:
                return _MISSING
            v = ts.next.get(node.path[-1].lower(), _MISSING)
            return None if (v is _MISSING and blank) else v
        if (isinstance(node, FunctionCall) and node.name == "PRIORVALUE"
                and len(node.args) == 1 and isinstance(node.args[0], FieldRef)):
            v = ts.prior.get(node.args[0].path[-1].lower(), _MISSING)
            return None if (v is _MISSING and blank) else v
        return _MISSING

    def walk_bool(node) -> Optional[bool]:
        if isinstance(node, And):
            return _k_and(walk_bool(op) for op in node.operands)
        if isinstance(node, Or):
            return _k_or(walk_bool(op) for op in node.operands)
        if isinstance(node, Not):
            r = walk_bool(node.operand)
            return None if r is None else (not r)
        if isinstance(node, Comparison):
            return _compare(node, val)
        if isinstance(node, FunctionCall):
            return _bool_function(node, val, ts, blank)
        if isinstance(node, FieldRef):                 # bare boolean predicate
            v = val(node)
            return v if isinstance(v, bool) else None
        if isinstance(node, Literal):
            return bool(node.value) if node.kind == "boolean" else None
        return None

    return walk_bool(ast)


def _k_and(results) -> Optional[bool]:
    unknown = False
    for r in results:
        if r is False:
            return False
        if r is None:
            unknown = True
    return None if unknown else True


def _k_or(results) -> Optional[bool]:
    unknown = False
    for r in results:
        if r is True:
            return True
        if r is None:
            unknown = True
    return None if unknown else False


def _bool_function(node: FunctionCall, val, ts: TransitionState,
                   blank: bool) -> Optional[bool]:
    if node.name == "ISCHANGED":
        if len(node.args) == 1 and isinstance(node.args[0], FieldRef):
            f = node.args[0].path[-1].lower()
            p, n = ts.prior.get(f, _MISSING), ts.next.get(f, _MISSING)
            if blank:
                p = None if p is _MISSING else p
                n = None if n is _MISSING else n
                return p != n
            if p is _MISSING or n is _MISSING:
                return None
            return p != n
        return None
    if node.name == "ISNEW":
        return False                                   # a transition has a prior
    if node.name in ("ISBLANK", "ISNULL"):
        if len(node.args) == 1:
            v = val(node.args[0])
            if v is _MISSING:
                return None
            return v is None or v == ""
        return None
    if node.name == "ISPICKVAL":
        if len(node.args) == 2 and isinstance(node.args[1], Literal):
            v = val(node.args[0])                      # FieldRef OR PRIORVALUE(f)
            if v is _MISSING:
                return None
            if v is None:
                # blank picklist never equals a non-empty literal
                lit = node.args[1].value
                return False if isinstance(lit, str) and lit != "" else None
            if isinstance(v, str) and isinstance(node.args[1].value, str):
                return _text_eq(v, node.args[1].value)
        return None
    if node.name in ("TODAY", "PRIORVALUE"):
        return None                                    # value position / no clock
    return None


def _compare(node: Comparison, val) -> Optional[bool]:
    # TODAY() comparisons: unknown (no clock at authoring) — with ONE bounded,
    # documented axiom: the far-future bridge constant is not before today
    # (`FAR_FUTURE_DATE < TODAY()` is False for any plausible run date). This
    # is the same assumption the bridge itself rests on, made explicit so the
    # acceptance verification ("the rule provably does NOT fire") is not
    # poisoned by a date branch the fixture has already neutralized.
    for a_side, b_side, op in ((node.left, node.right, node.op),
                               (node.right, node.left, _FLIP[node.op])):
        if isinstance(b_side, FunctionCall) and b_side.name == "TODAY":
            v = val(a_side)
            if v == FAR_FUTURE_DATE and op == "<":
                return False
            return None
    a, b, op = val(node.left), val(node.right), node.op
    if a is _MISSING or b is _MISSING or a is None or b is None:
        return None
    da, db = as_decimal(a), as_decimal(b)
    if da is not None and db is not None:
        return _ORDER[op](da, db)
    if isinstance(a, str) and isinstance(b, str):
        if op not in ("=", "<>"):
            return None
        eq = _text_eq(a, b)
        if eq is None:
            return None
        return eq if op == "=" else (not eq)
    if isinstance(a, bool) and isinstance(b, bool) and op in ("=", "<>"):
        return (a == b) if op == "=" else (a != b)
    return None


# ---------------------------------------------------------------------------
# satisfy_transition — the satisfaction operation over the two-phase world
# ---------------------------------------------------------------------------

def satisfy_transition(formula_text: str, meta: Optional[dict] = None,
                       sibling_items=None) -> Optional[TransitionWitness]:
    """A :class:`TransitionWitness` realizing ``formula_text``'s firing as a
    setup-create + transition-update, or ``None`` (out of grammar / UNSAT).

    Grammar: a top-level AND whose conjuncts are (a) transition atoms —
    ``ISCHANGED(f)``, a to-state constraint on an ISCHANGED field
    (``ISPICKVAL(f, X)`` / ``f = X``), a prior-state constraint
    (``ISPICKVAL(PRIORVALUE(f), X)``); (b) plain single-phase conjuncts
    (delegated to the certainty-bounded ``_satisfy``); (c) AT MOST ONE
    disjunction — the approval-branch OR, of which **exactly one** branch is
    made true (the first derivable-and-isolatable in formula order, minimally
    violating) and every other is falsified.

    ``sibling_items`` (``[(name_or_msg, formula_text), ...]``) arms the D-354
    completion over the POST-update state, transition-aware (``VR05``'s
    ``PRIORVALUE`` gate evaluates against the witness's prior — provably silent
    for a non-Approved prior). A provably-firing sibling silenceable only on a
    staged (protected) dimension → UNSAT → ``None``."""
    meta = meta or {}
    ast = parse(formula_text)
    if not is_parsed(ast) or not isinstance(ast, And):
        return None
    # Defensive: the target is never its own sibling (the witness FIRES it —
    # completing against it would UNSAT on the protected witness fields).
    if sibling_items:
        sibling_items = [(n, t) for n, t in sibling_items if t != formula_text]
    conjuncts = _flatten_and(ast)

    changed_fields = _ischanged_fields(conjuncts)
    if not changed_fields:
        return None                                    # not transition-shaped

    setup: dict = {}
    changes: dict = {}
    provenance: dict = {}
    disjunction = None
    plain: list = []

    try:
        for c in conjuncts:
            if isinstance(c, Or):
                if disjunction is not None:
                    return None                        # >1 OR — out of grammar
                disjunction = c
                continue
            if _is_ischanged(c):
                continue                               # realized via to/prior state
            handled = _transition_atom(c, changed_fields, setup, changes,
                                       provenance, meta)
            if handled:
                continue
            plain.append(c)
        # ISCHANGED fields with no to-state constraint: synthesize a changed pair.
        for f in changed_fields:
            if f not in changes:
                pair = _changed_pair(f, meta)
                if pair is None:
                    return None
                setup.setdefault(f, pair[0])
                changes[f] = pair[1]
                provenance.setdefault(
                    f, (ROLE_TARGET_ACTIVATION, "transition: the field changes"))
        # Plain conjuncts → the setup create (held through the update).
        for c in plain:
            asg = _unwrap_soft(_merge([_satisfy(c, True, meta)]))
            for f, v in asg.items():
                if f in changes or (f in setup and setup[f] != v):
                    return None                        # conflicts the transition
                setup.setdefault(f, v)
                provenance.setdefault(f, (ROLE_TARGET_ACTIVATION, _plain_src(c)))
    except _Undecidable:
        return None

    prior_pins = [(f, setup[f]) for f in
                  {p[0] for p in map(_prior_constraint, conjuncts) if p}
                  if f in setup]

    if disjunction is None:
        witness = TransitionWitness(setup=dict(setup), changes=dict(changes),
                                    provenance=dict(provenance))
        witness = _compose_entry(witness, ast, prior_pins, meta, sibling_items)
        if witness is None:
            return None
        return _complete(witness, meta, sibling_items)

    # Exactly-one-branch violation: first derivable-and-isolatable in formula order.
    for branch in disjunction.operands:
        result = _try_branch(branch, disjunction, setup, changes, provenance,
                             meta, sibling_items)
        if result is not None:
            return result
    return None


def satisfy_transition_acceptance(formula_text: str, meta: Optional[dict] = None,
                                  sibling_items=None) -> Optional[TransitionWitness]:
    """The INVERSE VR10 experiment (the T3 positive): a witness under which the
    transition-shaped rule provably does NOT fire — every violation branch
    falsified, every gate and transition atom still realized — so the update
    (the transition into the gated state) must SUCCEED. Same grammar, same
    :class:`TransitionState`, same sibling completion as
    :func:`satisfy_transition`; the verification flips (the target must
    evaluate False over the witness, run-time mode). ``None`` on
    out-of-grammar / underivable / UNSAT — an acceptance is never emitted on a
    fixture the rule might still reject."""
    meta = meta or {}
    ast = parse(formula_text)
    if not is_parsed(ast) or not isinstance(ast, And):
        return None
    if sibling_items:
        sibling_items = [(n, t) for n, t in sibling_items if t != formula_text]
    conjuncts = _flatten_and(ast)
    changed_fields = _ischanged_fields(conjuncts)
    if not changed_fields:
        return None

    setup: dict = {}
    changes: dict = {}
    provenance: dict = {}
    disjunction = None
    plain: list = []
    try:
        for c in conjuncts:
            if isinstance(c, Or):
                if disjunction is not None:
                    return None
                disjunction = c
                continue
            if _is_ischanged(c):
                continue
            if _transition_atom(c, changed_fields, setup, changes,
                                provenance, meta):
                continue
            plain.append(c)
        for f in changed_fields:
            if f not in changes:
                pair = _changed_pair(f, meta)
                if pair is None:
                    return None
                setup.setdefault(f, pair[0])
                changes[f] = pair[1]
                provenance.setdefault(
                    f, (ROLE_TARGET_ACTIVATION, "transition: the field changes"))
        for c in plain:
            asg = _unwrap_soft(_merge([_satisfy(c, True, meta)]))
            for f, v in asg.items():
                if f in changes or (f in setup and setup[f] != v):
                    return None
                setup.setdefault(f, v)
                provenance.setdefault(f, (ROLE_TARGET_ACTIVATION, _plain_src(c)))
        # The inverse: EVERY violation branch falsified (none violated).
        if disjunction is not None:
            for branch in disjunction.operands:
                off = _falsify_branch(branch, meta)
                if off is None:
                    return None
                for f, v in off.items():
                    if f in changes:
                        return None
                    if f in setup and setup[f] != v:
                        return None
                    setup.setdefault(f, v)
                    provenance.setdefault(
                        f, (ROLE_TARGET_ACTIVATION,
                            f"branch falsified: {_plain_src(branch)}"))
    except _Undecidable:
        return None

    witness = TransitionWitness(setup=dict(setup), changes=dict(changes),
                                provenance=dict(provenance),
                                violated_branch="")
    # The target must provably NOT fire over the witness (run-time mode).
    ts = TransitionState(prior=_bare(witness.setup),
                         next=_bare({**witness.setup, **witness.changes}))
    if evaluate_transition(ast, ts, absent="blank") is not False:
        return None
    return _complete(witness, meta, sibling_items)


def _try_branch(branch, disjunction, setup, changes, provenance, meta,
                sibling_items) -> Optional[TransitionWitness]:
    """Attempt the witness with THIS branch violated and every other falsified;
    ``None`` when this branch is underivable or un-isolatable (the caller tries
    the next — 'first derivable-and-isolatable in formula order')."""
    try:
        s, p = dict(setup), dict(provenance)
        hit = _unwrap_soft(_merge([_satisfy(branch, True, meta)]))
        for f, v in hit.items():
            if f in changes or (f in s and s[f] != v):
                return None
            s[f] = v
            p[f] = (ROLE_TARGET_WITNESS, _plain_src(branch))
        for other in disjunction.operands:
            if other is branch:
                continue
            off = _falsify_branch(other, meta)
            if off is None:
                return None
            for f, v in off.items():
                if f in changes:
                    return None
                if f in s and s[f] != v:
                    return None                        # conflicts a staged value
                s.setdefault(f, v)
                p.setdefault(f, (ROLE_TARGET_ACTIVATION,
                                 f"branch falsified: {_plain_src(other)}"))
        witness = TransitionWitness(
            setup=s, changes=dict(changes), provenance=p,
            violated_branch=_plain_src(branch))
        # Exactly-one-branch verification over the assembled witness (blank
        # mode): the violated branch must be TRUE, every other branch must not
        # be provably true — catching falsification interactions (e.g. a date
        # value that falsifies one branch while arming another).
        ts = TransitionState(prior=_bare(witness.setup),
                             next=_bare({**witness.setup, **witness.changes}))
        if evaluate_transition(branch, ts, absent="blank") is not True:
            return None
        for other in disjunction.operands:
            if other is not branch and \
                    evaluate_transition(other, ts, absent="blank") is True:
                return None
        return _complete(witness, meta, sibling_items)
    except _Undecidable:
        return None


def _falsify_branch(node, meta) -> Optional[dict]:
    """A certain assignment making this disjunct FALSE. Two date pragmatics are
    handled locally with the SAME far-future constant so sibling date branches
    reconcile instead of conflicting: ``f < TODAY()`` is falsified by
    ``FAR_FUTURE_DATE``, and ``ISBLANK(f)`` on a DATE field is falsified by the
    same constant (the generic non-blank date is a PAST one, which would arm a
    ``< TODAY()`` sibling branch at run time — the live D6/D7 interaction).
    Everything else delegates to the certainty-bounded ``_satisfy(node, False)``."""
    if (isinstance(node, Comparison) and node.op == "<"
            and isinstance(node.left, FieldRef)
            and isinstance(node.right, FunctionCall)
            and node.right.name == "TODAY"):
        return {node.left.path[-1]: FAR_FUTURE_DATE}
    if (isinstance(node, FunctionCall) and node.name in ("ISBLANK", "ISNULL")
            and len(node.args) == 1 and isinstance(node.args[0], FieldRef)):
        f = node.args[0].path[-1]
        if ((meta or {}).get(f) or {}).get("field_type", "").lower() == "date":
            return {f: FAR_FUTURE_DATE}
    try:
        return _unwrap_soft(_merge([_satisfy(node, False, meta)]))
    except _Undecidable:
        return None


def _compose_entry(witness: TransitionWitness, target_ast, prior_pins,
                   meta, sibling_items) -> Optional[TransitionWitness]:
    """The LEGITIMATE-PATH composition (AK, VR05 arc): when the witness's prior
    state pins a field value whose ENTRY is gated by exactly one sibling
    transition control (Stage=Approved is entered only past VR10), a direct
    create into that state would bypass the org's own controls — so the prior
    state is established through the gate itself: the create stages the entry
    control's ACCEPTANCE fixture, ``entry_changes`` is the org's own transition
    (expected to succeed), and the witness's mutation is recomputed against the
    post-entry state. Unchanged when nothing pins a gated state (a direct
    create bypasses nothing); ``None`` (refuse) when the entry path is
    ambiguous (>1 gate), underivable, or the recomputed mutation cannot be
    verified — never a bypassing fixture."""
    if not prior_pins or not sibling_items:
        return witness
    for f, v in prior_pins:
        gates = []
        for name, text in sibling_items:
            sast = parse(text)
            if not is_parsed(sast) or not isinstance(sast, And):
                continue
            conj = _flatten_and(sast)
            if (f in _ischanged_fields(conj)
                    and any(_to_state_constraint(c) == (f, v) for c in conj)):
                gates.append((name, text))
        if not gates:
            continue                       # ungated state — direct create is honest
        if len(gates) > 1:
            return None                    # ambiguous entry path — refuse
        entry = satisfy_transition_acceptance(gates[0][1], meta, sibling_items)
        if entry is None:
            return None                    # cannot legitimately reach the prior state
        post_entry = {**entry.setup, **entry.changes}
        new_changes: dict = {}
        for g, nv in witness.changes.items():
            cur = post_entry.get(g)
            if cur is None:
                new_changes[g] = nv
                continue
            alt = _different_value(g, cur, meta)
            if alt is None:
                return None
            new_changes[g] = alt
        setup = dict(entry.setup)
        for sf, sv in witness.setup.items():
            if sf == f or sf in witness.changes:
                continue                   # the pin is realized by the entry; the
            if sf in setup and setup[sf] != sv:   # ISCHANGED initial: entry wins
                return None
            setup.setdefault(sf, sv)
        prov = dict(entry.provenance)
        prov.update({pf: pr for pf, pr in witness.provenance.items()
                     if pf not in prov})
        prov[f] = (ROLE_CONTEXT,
                   "prior state (established via the org's own transition control)")
        for g in new_changes:
            prov[g] = (ROLE_TARGET_WITNESS, f"the mutation under test ({g})")
        composed = TransitionWitness(
            setup=setup, changes=new_changes, provenance=prov,
            violated_branch=witness.violated_branch,
            entry_changes=dict(entry.changes))
        # The target must fire over the MUTATION phase (post-entry prior).
        ts = TransitionState(
            prior=_bare({**setup, **entry.changes}),
            next=_bare({**setup, **entry.changes, **new_changes}))
        if evaluate_transition(target_ast, ts, absent="blank") is not True:
            return None
        return composed
    return witness


def derive_prior_state_control(formula_text: str, setup: dict, changes: dict,
                               meta: Optional[dict] = None,
                               sibling_items=None) -> Optional[TransitionWitness]:
    """The PRIOR_STATE ContextDifferential control arm (AK, VR05 arc): the SAME
    setup and the SAME mutation, with the entry transition OMITTED — the one
    varied dimension is the prior-state context, so the accept↔reject delta is
    attributable to the transition history alone. Returns the control witness
    only when the target provably does NOT fire over the un-entered mutation
    (and no parseable sibling provably fires); ``None`` otherwise — a control
    is never emitted on a fixture some control might still reject."""
    meta = meta or {}
    ast = parse(formula_text)
    if not is_parsed(ast):
        return None
    ts = TransitionState(prior=_bare(setup), next=_bare({**setup, **changes}))
    if evaluate_transition(ast, ts, absent="blank") is not False:
        return None
    for name, text in sorted(set((str(n), str(t)) for n, t in (sibling_items or ())
                                 if t != formula_text)):
        sast = parse(text)
        if is_parsed(sast) and evaluate_transition(sast, ts, absent="blank") is True:
            return None
    prov = {f: (ROLE_TARGET_ACTIVATION, "held constant (the differential's base)")
            for f in setup}
    for g in changes:
        prov[g] = (ROLE_TARGET_WITNESS, f"the mutation under test ({g})")
    return TransitionWitness(
        setup=dict(setup), changes=dict(changes), provenance=prov,
        violated_branch="")


def _different_value(f: str, current: Any, meta: dict) -> Optional[Any]:
    """A deterministic value ≠ ``current`` for field ``f`` — numeric steps by
    the field's minimal increment (D-352), picklists take the sorted-first
    alternative; refuse otherwise."""
    from primeqa.generation.typed_value import minimal_increment
    fm = (meta or {}).get(f) or {}
    ft = (fm.get("field_type") or "").lower()
    d = as_decimal(current)
    if d is not None and ft in ("int", "integer", "double", "currency", "percent"):
        from decimal import Decimal
        inc = minimal_increment(ft, fm.get("scale")) or Decimal(1)
        nd = d + inc
        return int(nd) if nd == nd.to_integral_value() else float(nd)
    vals = sorted(fm.get("picklist_values") or ())
    for v in vals:
        if v != current:
            return v
    return None


def _complete(witness: TransitionWitness, meta,
              sibling_items) -> Optional[TransitionWitness]:
    """The D-354 completion, transition-aware: every sibling control that
    PROVABLY FIRES over the post-update state (``prior=setup``,
    ``next=setup⊕changes``, absent=blank — the satisfaction semantic) is
    silenced on a FREE dimension (fills land in the setup create and hold
    through the update); a sibling silenceable only on a staged dimension →
    UNSAT → ``None``. Unparseable / unprovable (TODAY) siblings are skipped —
    the run-time taxonomy stays their backstop."""
    if not sibling_items:
        return witness
    setup = dict(witness.setup)
    provenance = dict(witness.provenance)
    protected = set(setup) | set(witness.changes) | set(witness.entry_changes)
    parsed = []
    for name, text in sorted(set((str(n), str(t)) for n, t in sibling_items)):
        sast = parse(text)
        if is_parsed(sast):
            parsed.append((name, sast))

    def _mutation_ts(s: dict) -> TransitionState:
        # The MUTATION phase: for an entry-composed witness the prior is the
        # post-entry state (the record after the org's own transition).
        prior = {**s, **witness.entry_changes}
        return TransitionState(prior=_bare(prior),
                               next=_bare({**prior, **witness.changes}))

    for name, sast in parsed:
        if evaluate_transition(sast, _mutation_ts(setup),
                               absent="blank") is not True:
            continue
        off = _falsify_off_target(sast, protected, meta)
        if off is None:
            return None                                # UNSAT: un-isolatable
        for f, v in off.items():
            setup[f] = v
            protected.add(f)
            provenance[f] = (ROLE_SIBLING_ISOLATION, name)
    # Whole-set verification over the completed witness.
    for name, sast in parsed:
        if evaluate_transition(sast, _mutation_ts(setup),
                               absent="blank") is True:
            return None
    return TransitionWitness(setup=setup, changes=dict(witness.changes),
                             provenance=provenance,
                             violated_branch=witness.violated_branch,
                             entry_changes=dict(witness.entry_changes))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _flatten_and(ast) -> list:
    out: list = []

    def rec(node):
        if isinstance(node, And):
            for op in node.operands:
                rec(op)
        else:
            out.append(node)

    rec(ast)
    return out


def _is_ischanged(node) -> bool:
    return (isinstance(node, FunctionCall) and node.name == "ISCHANGED"
            and len(node.args) == 1 and isinstance(node.args[0], FieldRef))


def _ischanged_fields(conjuncts) -> set:
    return {c.args[0].path[-1] for c in conjuncts if _is_ischanged(c)}


def _transition_atom(node, changed_fields: set, setup: dict, changes: dict,
                     provenance: dict, meta: dict) -> bool:
    """Handle a transition atom in place; ``True`` when consumed. Raises
    ``_Undecidable`` on an unrealizable atom (no alternative prior value)."""
    # ISPICKVAL(PRIORVALUE(f), X) / PRIORVALUE(f) = X  → the PRIOR state.
    prior = _prior_constraint(node)
    if prior is not None:
        f, v = prior
        if f in setup and setup[f] != v:
            raise _Undecidable(f"conflicting prior state on {f}")
        setup[f] = v
        provenance[f] = (ROLE_TARGET_ACTIVATION, "transition: the prior state")
        return True
    # A to-state constraint on an ISCHANGED field → the CHANGES update; the
    # setup gets a real alternative prior value (≠ the to-state).
    to = _to_state_constraint(node)
    if to is not None and to[0] in {c for c in changed_fields}:
        f, v = to
        changes[f] = v
        alt = _alternative_value(f, v, meta)
        if alt is None:
            raise _Undecidable(f"no alternative prior value for {f}")
        setup.setdefault(f, alt)
        provenance[f] = (ROLE_TARGET_ACTIVATION,
                         f"transition: {alt!r} → {v!r}")
        return True
    return False


def _prior_constraint(node):
    """``(field, value)`` when the node constrains PRIORVALUE(f), else None."""
    if (isinstance(node, FunctionCall) and node.name == "ISPICKVAL"
            and len(node.args) == 2
            and isinstance(node.args[0], FunctionCall)
            and node.args[0].name == "PRIORVALUE"
            and len(node.args[0].args) == 1
            and isinstance(node.args[0].args[0], FieldRef)
            and isinstance(node.args[1], Literal)):
        return node.args[0].args[0].path[-1], node.args[1].value
    if (isinstance(node, Comparison) and node.op == "="
            and isinstance(node.left, FunctionCall)
            and node.left.name == "PRIORVALUE"
            and len(node.left.args) == 1
            and isinstance(node.left.args[0], FieldRef)
            and isinstance(node.right, Literal)):
        return node.left.args[0].path[-1], node.right.value
    return None


def _to_state_constraint(node):
    """``(field, value)`` when the node pins a field to a literal value —
    ``ISPICKVAL(f, X)`` or ``f = X``."""
    if (isinstance(node, FunctionCall) and node.name == "ISPICKVAL"
            and len(node.args) == 2 and isinstance(node.args[0], FieldRef)
            and isinstance(node.args[1], Literal)):
        return node.args[0].path[-1], node.args[1].value
    if (isinstance(node, Comparison) and node.op == "="
            and isinstance(node.left, FieldRef)
            and isinstance(node.right, Literal)):
        return node.left.path[-1], node.right.value
    return None


def _alternative_value(f: str, avoid: Any, meta: dict) -> Optional[Any]:
    """A real, deterministic prior value ≠ ``avoid`` for field ``f`` — from the
    field's active picklist values (sorted-first ≠ avoid), never invented."""
    fm = (meta or {}).get(f) or {}
    values = fm.get("picklist_values") or ()
    for v in sorted(values):
        if v != avoid:
            return v
    return None


def _changed_pair(f: str, meta: dict) -> Optional[tuple]:
    """A deterministic (prior, next) pair with prior ≠ next for an ISCHANGED
    field with NO to-state constraint (VR05's ``ISCHANGED(Deal_Value__c)``).
    Numeric → (1, 2); picklist → the first two active values; else refuse."""
    fm = (meta or {}).get(f) or {}
    ft = (fm.get("field_type") or "").lower()
    if ft in ("int", "integer", "double", "currency", "percent"):
        return 1, 2
    values = sorted(fm.get("picklist_values") or ())
    if len(values) >= 2:
        return values[0], values[1]
    return None


def _plain_src(node) -> str:
    """A compact human rendering of a conjunct/branch for provenance."""
    try:
        if isinstance(node, FunctionCall):
            args = ",".join(
                a.path[-1] if isinstance(a, FieldRef)
                else (f"{a.name}(…)" if isinstance(a, FunctionCall)
                      else repr(getattr(a, "value", "?")))
                for a in node.args)
            return f"{node.name}({args})"
        if isinstance(node, Comparison):
            left = (node.left.path[-1] if isinstance(node.left, FieldRef)
                    else "expr")
            right = (repr(node.right.value) if isinstance(node.right, Literal)
                     else ("TODAY()" if isinstance(node.right, FunctionCall)
                           and node.right.name == "TODAY" else "expr"))
            return f"{left} {node.op} {right}"
        if isinstance(node, Not):
            return f"NOT({_plain_src(node.operand)})"
    except Exception:
        pass
    return "condition"


def _bare(d: dict) -> dict:
    """Bare-lowercased keys (the formula's vocabulary)."""
    return {str(k).rsplit(".", 1)[-1].lower(): v for k, v in (d or {}).items()}

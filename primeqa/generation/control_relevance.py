"""Control-relevance nomination (Amendment B, AK 2026-07-09).

Nominating the relevant *control* for a requirement is a DIFFERENT operation
from proving a rule *fires* (entailment, ``vr_conflict.entails_firing``). A
requirement that under-specifies the threshold — req-315's *"Enterprise deals
are subject to stricter discount controls than standard deals"* names no number
— cannot ENTAIL a VR formula, so entailment can never NOMINATE the control from
it. Relevance nominates by semantic subject/context alignment; formula analysis
then reads the concrete boundary (25% off VR08); entailment stays for the proof
role, only when the requirement's own predicates are specific enough.

    RelevantControl = ContextGateMatch ∧ SubjectGovernanceMatch ∧ BehaviouralRoleMatch

- **ContextGateMatch** — the VR's error-condition contains the context conjunct
  ``RecordType.DeveloperName = "<devname>"`` (the rule is scoped to that record
  classification). For req-315 exactly one VR (VR08) carries it.
- **SubjectGovernanceMatch** — the VR constrains the requirement's subject field
  (Discount).
- **BehaviouralRoleMatch** — the *role* the VR plays on that field matches the
  requirement's asserted role — not merely that the field appears somewhere. This
  is the term that stops "field is mentioned in the formula" from counting as
  relevance (VR10's ``Discount > 0.20`` is an incidental branch inside an
  approval-transition gate, not a cap).

Bounded on purpose (AK): a **RECORD-TYPE** context only, and a small role grammar
— ``CAP`` / ``FLOOR`` (a direct upper/lower bound), ``REQUIREDNESS`` (conditional
requiredness), ``TRANSITION`` (a prior-state / approval-transition gate),
``COMPOUND`` (the field is one branch of a multi-part eligibility gate). A role
outside the grammar, or an unparseable formula, is ``UNKNOWN`` and matches
nothing — refuse-rather-than-guess. This is not a universal ontology; widening it
is deferred work.

Pure module: formula-AST + primitives only, no S1 / DB / governance imports, so
the nomination is unit-verifiable in isolation before it is wired into the
prohibition resolver (protecting the existing field-overlap controls).
"""
from __future__ import annotations

from typing import Optional

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

# -- the bounded behavioural-role grammar ------------------------------------
CAP = "cap"                    # a direct upper bound: fires when field > / >= a literal
FLOOR = "floor"               # a direct lower bound: fires when field < / <= a literal
REQUIREDNESS = "requiredness"  # conditional requiredness: fires when ISBLANK(field)
TRANSITION = "transition"     # a prior-state / approval-transition gate (PRIORVALUE / ISCHANGED)
COMPOUND = "compound"         # the field is one branch of a multi-part eligibility gate
UNKNOWN = "unknown"           # outside the grammar / unparseable — matches nothing

# The dotted ref that names a record's own RecordType — the ONE context leaf
# (mirrors ``verified_negative._RECORDTYPE_PATH`` / ``vr_conflict`` discipline).
_RECORDTYPE_PATH = ("recordtype", "developername")

# Functions that make a rule a transition gate — never a direct cap/floor on a field.
_TRANSITION_FUNCS = {"PRIORVALUE", "ISCHANGED", "ISNEW"}

_CAP_OPS = {">", ">="}
_FLOOR_OPS = {"<", "<="}


def _ast(vr_formula_text: str):
    try:
        ast = parse(vr_formula_text)
        return ast if is_parsed(ast) else None
    except Exception:
        return None


def _is_recordtype_ref(node) -> bool:
    return (isinstance(node, FieldRef) and getattr(node, "is_dotted", False)
            and tuple(p.lower() for p in node.path) == _RECORDTYPE_PATH)


def _bare(node: FieldRef) -> str:
    return node.path[-1].lower()


def _top_conjuncts(ast) -> list:
    """The flattened top-level AND conjuncts (the clauses a Salesforce error-condition
    ANDs together), or ``[ast]`` when the root is not an AND. Nested ANDs are
    flattened (the parser may build ``a && b && c`` as nested ``And`` nodes); an OR /
    NOT / comparison is a single conjunct and is NOT descended into. A conjunct is
    'top-level' when its truth is required for the rule to fire — the discriminator
    between a direct cap (top-level ``Discount > x``) and an incidental branch (a
    ``Discount > x`` inside an OR — a COMPOUND-gate branch)."""
    out: list = []

    def _rec(node):
        if isinstance(node, And):
            for op in node.operands:
                _rec(op)
        else:
            out.append(node)

    _rec(ast)
    return out


def context_gate_match(vr_formula_text: str, developer_name: str) -> bool:
    """True iff the VR's error-condition contains ``RecordType.DeveloperName =
    "<developer_name>"`` as a **top-level** conjunct — i.e. the rule is scoped to
    that record classification. Only equality (``=``) counts; the match is exact on
    the DeveloperName string. Every non-RecordType ref is ignored here."""
    ast = _ast(vr_formula_text)
    if ast is None:
        return False
    for conj in _top_conjuncts(ast):
        if not isinstance(conj, Comparison) or conj.op != "=":
            continue
        left, right = conj.left, conj.right
        if isinstance(right, FieldRef) and isinstance(left, Literal):
            left, right = right, left
        if (_is_recordtype_ref(left) and isinstance(right, Literal)
                and isinstance(right.value, str)
                and right.value == developer_name):
            return True
    return False


def governs_field(vr_formula_text: str, field: str) -> bool:
    """True iff the VR's formula references the bare ``field`` anywhere."""
    ast = _ast(vr_formula_text)
    if ast is None:
        return False
    f = field.lower()
    return any(isinstance(n, FieldRef) and not getattr(n, "is_dotted", False)
               and _bare(n) == f for n in walk(ast))


def vr_role_on_field(vr_formula_text: str, field: str) -> str:
    """Classify the behavioural role the VR plays *on* ``field`` into the bounded
    grammar. A rule carrying a prior-state / changed function anywhere is a
    TRANSITION gate (its role on any field is transition — the effect is the
    transition, not a static bound). Otherwise the role is read from where the field
    sits: a top-level threshold conjunct is a direct CAP/FLOOR; a top-level
    ``ISBLANK(field)`` is REQUIREDNESS; a field that appears only nested (inside an
    OR / a deeper subtree) is a COMPOUND-gate branch; anything else UNKNOWN."""
    ast = _ast(vr_formula_text)
    if ast is None:
        return UNKNOWN
    f = field.lower()
    for n in walk(ast):
        if isinstance(n, FunctionCall) and n.name in _TRANSITION_FUNCS:
            return TRANSITION
    for conj in _top_conjuncts(ast):
        role = _leaf_role(conj, f)
        if role is not UNKNOWN:
            return role
    # The field is referenced but only nested (not a top-level conjunct on it).
    if governs_field(vr_formula_text, field):
        return COMPOUND
    return UNKNOWN


def _leaf_role(conj, f: str) -> str:
    """The role of a single TOP-LEVEL conjunct on bare field ``f`` (UNKNOWN when the
    conjunct is not directly about ``f``)."""
    if isinstance(conj, Comparison):
        left, right, op = conj.left, conj.right, conj.op
        if isinstance(left, Literal) and isinstance(right, FieldRef):
            left, right, op = right, left, _flip(op)
        if (isinstance(left, FieldRef) and not getattr(left, "is_dotted", False)
                and _bare(left) == f and isinstance(right, Literal)):
            if op in _CAP_OPS:
                return CAP
            if op in _FLOOR_OPS:
                return FLOOR
    if (isinstance(conj, FunctionCall) and conj.name in ("ISBLANK", "ISNULL")
            and len(conj.args) == 1 and isinstance(conj.args[0], FieldRef)
            and _bare(conj.args[0]) == f):
        return REQUIREDNESS
    return UNKNOWN


_FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "=", "<>": "<>"}


def _flip(op: str) -> str:
    return _FLIP.get(op, op)


def role_from_condition_predicate(predicate: str) -> str:
    """The behavioural role a grounded rejection-condition asserts, from its S2
    predicate — the requirement's own role (via what the LLM proposed on the subject
    field). ``exceeds`` / ``>`` / ``>=`` assert a CAP; ``<`` / ``<=`` a FLOOR;
    ``is_null`` REQUIREDNESS. Anything else is UNKNOWN (no role to match on)."""
    p = (predicate or "").lower()
    if p in ("exceeds", "greater_than", "gt", ">", ">="):
        return CAP
    if p in ("less_than", "lt", "<", "<="):
        return FLOOR
    if p == "is_null":
        return REQUIREDNESS
    return UNKNOWN


# Bounded requirement-role keyword frames (AK: "R is inferred from its verb/frame").
# Deliberately small — a lexical map, not NLP; an unmatched excerpt is UNKNOWN.
_ROLE_FRAMES = (
    (CAP, ("stricter", "cannot exceed", "must not exceed", "no more than",
           "not more than", "maximum", "at most", "up to", "capped", " cap ",
           "cannot be greater", "no greater than", "limit")),
    (FLOOR, ("at least", "no less than", "minimum", "must be at least",
             "cannot be less", "no lower than")),
    (REQUIREDNESS, ("must have", "must be provided", "must be captured",
                    "is required", "are required", "is mandatory", "mandatory",
                    "must be present", "must be specified")),
)


def role_from_excerpt(excerpt: str) -> str:
    """The requirement's behavioural role from its verb/frame — a bounded lexical
    map (CAP: 'stricter … controls' / 'cannot exceed' / 'maximum'; FLOOR: 'at least'
    / 'minimum'; REQUIREDNESS: 'must have' / 'required'). UNKNOWN when no frame
    matches — never a guess. Used only as a fallback when the proposed condition
    predicate does not itself pin a role."""
    if not excerpt:
        return UNKNOWN
    low = f" {excerpt.lower()} "
    for role, frames in _ROLE_FRAMES:
        if any(f in low for f in frames):
            return role
    return UNKNOWN


def nominate(vr_items, developer_name: str, subject_field: str,
             requirement_role: str) -> Optional[str]:
    """The SINGLE VR formula that is a relevant control for a record-type context
    hypothesis, or ``None`` when zero or >=2 qualify (refuse-on-non-unique).

    ``vr_items`` = ``[(vr_name, vr_formula_text), ...]``. A VR qualifies iff:
    ``context_gate_match(devname)`` ∧ ``governs_field(subject_field)`` ∧
    ``vr_role_on_field(subject_field) == requirement_role`` (with ``requirement_role``
    in the bounded grammar; ``UNKNOWN`` requirement role matches nothing — refuse).

    Returns the qualifying VR's formula text (what the resolver binds as the
    grounding rule). Deterministic and order-independent — a >=2 qualifier set is a
    refuse, never a pick-by-order."""
    if requirement_role is UNKNOWN or not developer_name or not subject_field:
        return None
    qualifiers = [
        text for _name, text in vr_items
        if context_gate_match(text, developer_name)
        and governs_field(text, subject_field)
        and vr_role_on_field(text, subject_field) == requirement_role
    ]
    # De-duplicate identical formula texts before the uniqueness test.
    uniq = list(dict.fromkeys(qualifiers))
    return uniq[0] if len(uniq) == 1 else None

"""Authoring-time staged-state VR-conflict guard (D-337).

A claim's STAGED values — automation-effect ``trigger_fields`` /
``update_trigger_fields``, acceptance conditions (+ the D-306 update
clauses), approval-arc conditions — are record state the recipe ITSELF
writes. When that staged state already violates one of the org's ACTIVE
validation rules, the run can end only one way: the org rejects the
create/update for a reason that is NOT the behavior under test, and the
claim is perma-red by construction. Two live req-302 catches on env-59
(2026-07-07, both deprecated) define the class — an LTV automation-effect
whose staged update put ``Loan_Amount__c > Property_Value__c``, rejected at
run time by the org's own Loan_Exceeds_Property_Value rule; and an
approval-arc whose staged state failed to ARM the org machinery it needed
(NO_APPLICABLE_PROCESS). This guard closes the first class. The second —
requiring the bound approval process's entry criteria to be satisfiable
from the staged conditions — needs S1 to capture approval entry criteria,
which it does not today (no ``entryCriteria`` anywhere in the model); it
is a named deferred arm, not a silent scope cut.

This module answers "does a staged state PROVABLY fire a VR formula?" at
authoring time — the authoring-time sibling of S4's run-time
``_vr_satisfaction_filler`` (R1, ``execution_engine/world.py``), sharing
its epistemics but inverting the remedy: R1 pads around a deficiency that
PADDING owns; this guard refuses a defect the STAGED VALUES own. k16 holds
on both sides — padding never touches staged fields, and this guard never
rewrites them (refuse-at-authoring, the detail names the rule; the
human/LLM re-proposes).

Kleene discipline (the D-113 posture): a field the staged state does not
set is UNKNOWN — padding, org automation, or a default may supply anything
— so only a formula whose truth is determined by the staged values ALONE
refuses. ``True OR unknown`` is True (refuse); ``True AND unknown`` is
unknown (pass — run-time R1 may well pad the armed rule into satisfaction,
exactly its job). Unparseable formulas, org-state functions (PRIORVALUE /
ISCHANGED / ISNEW), cross-object refs, and type surprises are all unknown;
the honest run-time loop stays the backstop (the R3
``formula_expectation`` fail-open posture — a wrong refusal of a runnable
claim is the one dangerous failure mode).

Why not the D-113 ``semantic.formula.evaluate`` primitive directly: D-113
evaluates a COMPLETE create payload, where an absent field IS blank
(``ISBLANK(absent) -> True``) — over a PARTIAL staged payload that reading
manufactures provable-fires out of fields padding will fill. Here absence
must mean unknown. The staged surfaces also need field-to-field
comparisons (the live catch above) and string-typed staged numbers
coerced (``"600000"`` — the D-304 ``_identity_safe`` float-to-string
boundary), which D-113's whole-payload convention never carries.

The update-phase check evaluates ``create ⊕ update`` — the same staged
overlay epistemics R3 uses (``verify_formula_expectation``); fields the
org may have rewritten between the phases are exactly the ones the overlay
does not contain, so they stay unknown.

Text equality is tri-state on case: exact match -> True; equal only
case-insensitively -> unknown; different beyond case -> False. Salesforce
formula text ``=`` is case-insensitive (EXACT() exists for the sensitive
compare) but the picklist/ISPICKVAL corners are not uniformly documented —
both verdicts above are provable under EITHER reading, so no dialect bet
is ever load-bearing.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from primeqa.generation.formula_expectation import as_decimal
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
)

# Comparison op re-oriented to (field, literal) when the literal is on the
# LEFT (the D-113 idiom).
_FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "=", "<>": "<>"}

_ORDER = {
    "=": lambda a, b: a == b, "<>": lambda a, b: a != b,
    "<": lambda a, b: a < b, ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
}

# Functions that need prior/changed/new record state — never provable from
# staged values alone.
_ORG_STATE_FUNCS = {"PRIORVALUE", "ISCHANGED", "ISNEW"}

_MISSING = object()


def entails_firing(vr_formula_text: str, states: list) -> Optional[bool]:
    """D-350 entailment: does the claim's asserted rejection state NECESSARILY fire
    this VR? ``states`` is the enumerated set of concrete ``{field: value}`` worlds
    the claim's grounded conditions pin (``equals`` -> the value, ``is_null`` -> None,
    ``in_set`` -> one world per member; unpinned fields stay absent = unknown).

    Returns:
      - ``True``  — the VR fires in EVERY pinned world (necessarily fires);
      - ``False`` — it fires in NONE (contradicted);
      - ``None``  — otherwise: it only POSSIBLY fires, or an org-state / unparseable
        subtree leaves it undetermined — the refuse-rather-than-guess floor.

    Reuses the Kleene :func:`_fires` (absent field = unknown), so a rule is only
    'necessarily' selected when the claim's OWN asserted values determine it — never
    the whole-payload `evaluate` convention (absent = blank), which would manufacture
    spurious fires. Exposed for D-350 predicate-aware VR disambiguation."""
    if not states:
        return None
    try:
        ast = parse(vr_formula_text)
        if not is_parsed(ast):
            return None
        results = [_fires(ast, _bare_payload(s)) for s in states]
    except Exception:
        return None
    if all(r is True for r in results):
        return True
    if all(r is False for r in results):
        return False
    return None


def find_staged_vr_conflict(
    vr_rules, staged_create: dict, staged_update: Optional[dict] = None,
    *, field_types: Optional[dict] = None,
    record_type_developer_name=None,
    context_enabled: bool = True,
) -> Optional[str]:
    """The refusal detail when a staged state provably fires an active VR,
    else ``None``.

    ``vr_rules`` is ``[(vr_name, formula_text), ...]`` — the subject's ACTIVE
    ValidationRules (the caller reads them off the grounding neighborhood).
    ``staged_create`` / ``staged_update`` are ``{field: value}`` with
    object-qualified or bare keys (bare-ified case-insensitively here — the
    R3 convention; formulas speak bare names). The create state is checked
    first across every rule, then the post-update overlay ``create ⊕
    update``; the first provable fire (rules in sorted order — deterministic
    like R1's sorted formula walk) names the refusal. Pure and total: any
    surprise inside a rule contributes unknown, never an exception."""
    try:
        create = _bare_payload(staged_create)
        ftypes = ({str(k).rsplit(".", 1)[-1].lower(): v
                   for k, v in (field_types or {}).items()}
                  if context_enabled and field_types else None)
        # D-443: per-phase gate context. Emission time KNOWS the staged
        # update is the COMPLETE intended mutation, so on the update phase
        # ISCHANGED is provably False outside its key set; the prior state
        # IS the staged create. context_enabled=False pins the byte-old
        # (context-less) behaviour for the sizing comparison.
        phases = []
        if create:
            gctx = ({"is_create": True, "prior": None, "update_keys": None,
                     "rt": record_type_developer_name}
                    if context_enabled else None)
            phases.append(("create", create, gctx))
        if staged_update:
            upd = _bare_payload(staged_update)
            gctx = ({"is_create": False, "prior": create,
                     "update_keys": frozenset(upd),
                     "rt": record_type_developer_name}
                    if context_enabled else None)
            phases.append(("update", {**create, **upd}, gctx))
        if not phases:
            return None
        parsed = []
        for name, text in sorted(set(tuple(r) for r in vr_rules or ())):
            ast = parse(text)
            if is_parsed(ast):
                parsed.append((name, text, ast))
        for phase, payload, gctx in phases:
            for name, text, ast in parsed:
                try:
                    fires = _fires(ast, payload, gctx, ftypes)
                except Exception:
                    fires = None                 # unknown, never a crash
                if fires is True:
                    return _detail(name, text, phase)
        return None
    except Exception:
        # A guard must never break generation — any surprise is no-conflict.
        return None


def _detail(name: str, text: str, phase: str) -> str:
    step = "create" if phase == "create" else "update"
    return (
        f"the staged {step} state provably fires the active validation rule "
        f"{name!r} ({' '.join(text.split())}) — the org would reject the "
        f"{step} for a reason that is not the behavior under test, so the "
        f"claim could never be exercised; restate the staged values to "
        f"satisfy that rule, or assert the rejection itself as a "
        f"prohibition case")


def _bare_payload(staged: Optional[dict]) -> dict:
    """Object-qualified keys -> bare lower-cased (the R3 convention)."""
    return {str(k).rsplit(".", 1)[-1].lower(): v
            for k, v in (staged or {}).items()}


def _lookup(payload: dict, fref: FieldRef):
    """The staged value for a BARE field ref; ``_MISSING`` (unknown) for a
    dotted (cross-object) ref or an unstaged field."""
    if fref.is_dotted:
        return _MISSING
    return payload.get(fref.path[0].lower(), _MISSING)


# -- the Kleene walk: True (provably fires) / False (provably not) / None ----

def _fires(node, payload: dict, gctx: Optional[dict] = None,
           ftypes: Optional[dict] = None) -> Optional[bool]:
    if isinstance(node, And):
        return _k_and(_fires(op, payload, gctx, ftypes)
                      for op in node.operands)
    if isinstance(node, Or):
        return _k_or(_fires(op, payload, gctx, ftypes)
                     for op in node.operands)
    if isinstance(node, Not):
        r = _fires(node.operand, payload, gctx, ftypes)
        return None if r is None else (not r)
    if isinstance(node, Comparison):
        return _comparison(node, payload, gctx, ftypes)
    if isinstance(node, FunctionCall):
        return _function(node, payload, gctx, ftypes)
    if isinstance(node, FieldRef):
        return _as_bool(_lookup(payload, node))  # bare boolean predicate
    if isinstance(node, Literal):
        return bool(node.value) if node.kind == "boolean" else None
    return None


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


# -- leaves -------------------------------------------------------------------

def _function(node: FunctionCall, payload: dict,
              gctx: Optional[dict] = None,
              ftypes: Optional[dict] = None) -> Optional[bool]:
    if node.name in _ORG_STATE_FUNCS:
        return _org_state(node, payload, gctx)
    if node.name in ("ISBLANK", "ISNULL"):
        if len(node.args) == 1 and isinstance(node.args[0], FieldRef):
            v = _lookup(payload, node.args[0])
            if v is _MISSING:
                return None                      # unstaged -> padding may fill
            return v is None or v == ""
        return None
    if node.name == "ISPICKVAL":
        if (len(node.args) == 2 and isinstance(node.args[0], FieldRef)
                and isinstance(node.args[1], Literal)):
            v = _lookup(payload, node.args[0])
            if v is _MISSING or not isinstance(v, str) \
                    or not isinstance(node.args[1].value, str):
                return None
            return _text_eq(v, node.args[1].value)
        # D-443: the corpus composition ISPICKVAL(PRIORVALUE(f), "lit").
        if (len(node.args) == 2 and isinstance(node.args[0], FunctionCall)
                and node.args[0].name == "PRIORVALUE"
                and isinstance(node.args[1], Literal)
                and isinstance(node.args[1].value, str)):
            prior = _prior_value(node.args[0], payload, gctx)
            if prior is _MISSING or not isinstance(prior, str):
                return None
            return _text_eq(prior, node.args[1].value)
        return None
    return None


def _org_state(node: FunctionCall, payload: dict,
               gctx: Optional[dict]) -> Optional[bool]:
    """D-443: emission-time org-state. The staged update is the COMPLETE
    intended mutation, so on the update phase ISCHANGED(f) is provably
    False for f outside the update's key set — stronger than attribution
    can ever be. Create-phase semantics are D-439's verified set (ISNEW →
    True; ISCHANGED → unknown, the unverified-create refusal)."""
    if gctx is None:
        return None
    if node.name == "ISNEW":
        return bool(gctx.get("is_create"))
    if node.name == "ISCHANGED":
        if not (len(node.args) == 1 and isinstance(node.args[0], FieldRef)
                and not node.args[0].is_dotted):
            return None
        if gctx.get("is_create"):
            return None          # create-context semantics not verified (D-439)
        keys = gctx.get("update_keys")
        if keys is None:
            return None
        field = node.args[0].path[0].lower()
        if field not in keys:
            return False         # the staged update provably does not touch it
        prior = (gctx.get("prior") or {})
        if field not in prior:
            return None          # pre-update value unknown (unstaged at create)
        return prior[field] != payload.get(field)
    return None                  # PRIORVALUE alone is a value, not a predicate


def _prior_value(node: FunctionCall, payload: dict, gctx: Optional[dict]):
    """``PRIORVALUE(f)`` as a staged value; ``_MISSING`` when unknowable."""
    if gctx is None:
        return _MISSING
    if not (len(node.args) == 1 and isinstance(node.args[0], FieldRef)
            and not node.args[0].is_dotted):
        return _MISSING
    field = node.args[0].path[0].lower()
    if gctx.get("is_create"):
        # D-439 verified: on creation PRIORVALUE returns the CURRENT value.
        return payload.get(field, _MISSING)
    prior = gctx.get("prior")
    if prior is None or field not in prior:
        return _MISSING
    return prior[field]


_RT_PATH = ("RecordType", "DeveloperName")


def _comparison(node: Comparison, payload: dict,
                gctx: Optional[dict] = None,
                ftypes: Optional[dict] = None) -> Optional[bool]:
    left, right, op = node.left, node.right, node.op
    if isinstance(left, Literal) and isinstance(right, FieldRef):
        left, right, op = right, left, _FLIP[op]
    if isinstance(left, FieldRef) and isinstance(right, Literal):
        # D-443: RecordType.DeveloperName resolves via the injected S1
        # resolver when the staged payload carries RecordTypeId.
        if (tuple(left.path) == _RT_PATH and gctx is not None
                and gctx.get("rt") is not None
                and op in ("=", "<>")
                and isinstance(right.value, str)):
            rt_id = payload.get("recordtypeid")
            if not rt_id:
                return None
            dev = gctx["rt"](str(rt_id))
            if dev is None:
                return None
            eq = _text_eq(dev, right.value)
            if eq is None:
                return None
            return eq if op == "=" else (not eq)
        v = _lookup(payload, left)
        if v is _MISSING:
            return None
        return _cmp_value_literal(
            v, op, right, _ftype(ftypes, left))
    if isinstance(left, FieldRef) and isinstance(right, FieldRef):
        a, b = _lookup(payload, left), _lookup(payload, right)
        if a is _MISSING or b is _MISSING:
            return None
        return _cmp_value_value(a, op, b, _ftype(ftypes, left),
                                _ftype(ftypes, right))
    return None                                  # arithmetic / nested — unknown


def _ftype(ftypes: Optional[dict], fref: FieldRef) -> Optional[str]:
    if ftypes is None or fref.is_dotted:
        return None
    return ftypes.get(fref.path[0].lower())


def _pct(value, ftype: Optional[str]):
    """D-442/D-443: percent fields evaluate in FRACTION space (÷100,
    live-proven). Convert only on a KNOWN percent type."""
    if ftype is not None and str(ftype).lower() == "percent":
        d = as_decimal(value)
        if d is not None:
            return d / Decimal(100)
    return value


def _cmp_value_literal(value, op: str, literal: Literal,
                       ftype: Optional[str] = None) -> Optional[bool]:
    if value is None:
        return None                              # null comparison — unknown
    kind, lit = literal.kind, literal.value
    if kind == "number":
        d = as_decimal(_pct(value, ftype))
        if d is None:
            return None                          # non-numeric staged value
        return _ORDER[op](d, Decimal(str(lit)))
    if kind == "string":
        if op not in ("=", "<>") or not isinstance(value, str):
            return None                          # text ordering — unknown
        eq = _text_eq(value, lit)
        if eq is None:
            return None
        return eq if op == "=" else (not eq)
    if kind == "boolean":
        vb = _as_bool(value)
        if vb is None or op not in ("=", "<>"):
            return None
        return (vb == lit) if op == "=" else (vb != lit)
    return None


def _cmp_value_value(a, op: str, b, ftype_a: Optional[str] = None,
                     ftype_b: Optional[str] = None) -> Optional[bool]:
    """Field-to-field — the live-catch shape (``Loan_Amount__c >
    Property_Value__c`` with both sides staged)."""
    a, b = _pct(a, ftype_a), _pct(b, ftype_b)
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
    ba, bb = _as_bool(a), _as_bool(b)
    if ba is not None and bb is not None and op in ("=", "<>"):
        return (ba == bb) if op == "=" else (ba != bb)
    return None


def _text_eq(a: str, b: str) -> Optional[bool]:
    """Tri-state text equality — provable under both the case-insensitive
    (SF formula ``=``) and case-sensitive (EXACT/ISPICKVAL corners) readings:
    exact -> True, equal-only-up-to-case -> unknown, different beyond case
    -> False."""
    if a == b:
        return True
    if a.casefold() == b.casefold():
        return None
    return False


def _as_bool(v) -> Optional[bool]:
    """A staged checkbox value as bool — real bools plus the "true"/"false"
    string shapes the hint boundary produces; anything else unknown."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.casefold() in ("true", "false"):
        return v.casefold() == "true"
    return None

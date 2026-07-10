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

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Union

from primeqa.generation.typed_value import minimal_increment

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
    satisfiable in BOTH directions qualify. Comparisons qualify; D-294 NOT-ISBLANK
    derives too (setup = the blank state, violating = a non-blank value); D-296
    derives a compound ``AND(NOT-ISBLANK, ..., a>b)`` via the soft/hard merge. A
    direction with no certain assignment stays NotDerivable (the graded fallback)."""

    setup_payload: dict[str, Any]       # non-violating create-time state
    violating_changes: dict[str, Any]   # the update that fires the rejection
    # Transition-witness provenance (VR10 arc): {field: (role, source)} for
    # every staged value when the pair came from `transition.satisfy_transition`
    # (target witness / target activation / sibling isolation). ``None`` on the
    # ordinary single-formula derivation (pre-transition pairs, byte-identical).
    provenance: Optional[dict] = None
    # The legitimate-path entry update (VR05 arc): when the prior state is
    # gated by a sibling transition control, this expected-to-SUCCEED update
    # (the org's own transition) precedes the violating one. None/empty → the
    # ordinary 2-step pair.
    entry_changes: Optional[dict] = None
    # The VR06-arc temporal experiment (a transition.TemporalExperiment): the
    # primary IS its blank arm; emission authors the remaining arms as probes
    # (past-reject + the adjacent today/tomorrow accepts). None → no experiment.
    temporal: Optional[Any] = None


@dataclass(frozen=True)
class NotDerivable:
    reason: str


@dataclass(frozen=True)
class BoundaryMember:
    """One member of the D-300 boundary set for a single-threshold-numeric
    prohibition: the payload to create, whether the VR must REJECT it (the
    firing probe) or ACCEPT it (the just-inside probe), and the human edge
    label (rides the recipe body for diagnostics — the verdict never consumes
    it; ``ProbeResult.boundary`` labelling stays deferred, D-300)."""

    payload: dict[str, Any]     # {field_name: value} (+ D-328 gate fields)
    expect_reject: bool         # True = firing; False = just-inside (accept)
    edge: str                   # "firing" | "just-inside"
    # The threshold field's bare name — WHICH payload key carries the boundary
    # value (a D-328 gated member also stages gate fields, e.g. VR08's
    # RecordTypeId, so the payload is no longer single-key). ``None`` only on
    # hand-built members; ``derive_boundary_set`` always fills it.
    boundary_field: Optional[str] = None
    # Fixture-completion provenance (AK Option-1 scope): {field: (role, source)}
    # for every payload value — target witness / target activation / context /
    # sibling isolation. Stamped by emission's accept-fixture completion;
    # rendered into the probe's env detail (the durable record of WHY each
    # field is staged). ``None`` = no completion ran (pre-completion members).
    fixture_provenance: Optional[dict] = None


@dataclass(frozen=True)
class _SoftFill:
    """D-296 — a "soft" fill: an *any non-blank* value synthesized for a
    ``NOT(ISBLANK(f))`` conjunct, which a HARD assignment on the same field (e.g. a
    cross-field ordered value like ``Property_Value__c: 0``) may override in
    :func:`_merge`, since any non-blank value already satisfies the NOT-ISBLANK
    constraint. Stripped to its raw ``value`` at the derive / derive_update boundary
    (:func:`_unwrap_soft`); it never reaches an emitted payload."""
    value: Any


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
    return VerifiedNegative(_unwrap_soft(payload))


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
        setup_payload=_unwrap_soft(setup),
        violating_changes=_unwrap_soft(violating))


_BOUNDARY_OPS = frozenset({">", ">=", "<", "<="})  # threshold orderings only


def derive_boundary_set(ast, field_metadata=None) -> tuple:
    """D-300 (+D-328 compound-AND): the ordered boundary set for a
    single-threshold-numeric prohibition — the firing probe (the existing
    :func:`derive` payload; the VR must reject it) + the just-inside probe
    (the boundary-adjacent non-firing value; the create must SUCCEED). Both
    values come from the SHIPPED derivation machinery — firing =
    ``_violating_value(op, lit)``, just-inside = the ``_NEG[op]`` direction
    (exactly :func:`derive_update`'s setup direction) — so no new value
    arithmetic exists here.

    Shapes (refuse → ``()``, never a guessed set):

      - A bare top-level ``Comparison`` of one FieldRef against one INTEGER
        literal with a threshold ordering (``>`` / ``>=`` / ``<`` / ``<=``) —
        the D-300 shape, unchanged.
      - D-328: a top-level ``And`` whose operands contain EXACTLY ONE such
        threshold comparison; every other operand is a GATE condition staged
        TRUE (via :func:`_satisfy`) in BOTH probes. Gates make the just-inside
        accept MEANINGFUL: with the gates true, the only reason the create
        succeeds is the value being inside the threshold (e.g.
        ``AND(ISPICKVAL(StageName,"Approved"), Loan__c > 5000000,
        NOT(ISPICKVAL(Approval_Status__c,"Approved")))`` probes exactly the
        5000000 boundary). Any underivable gate, zero or multiple threshold
        conjuncts, or a gate/threshold assignment conflict refuses the set.

    Equality ops are point constraints (no threshold adjacency); ``Or`` /
    ``Not`` compounds, cross-field thresholds, string/boolean literals,
    org-state functions, and dotted refs all refuse — the same
    :func:`_pre_scan` gates as :func:`derive`. ``field_metadata`` feeds gate
    derivation (D-294 rail: NOT-ISBLANK / NOT-ISPICKVAL gates need it).

    Invariant (drift self-check, unit-pinned): ``member[0].payload`` equals
    ``derive(ast).violating_payload`` — the firing probe IS the primary's
    payload (same constraint set through the same ``_merge``), never a
    divergent re-derivation.

    Integer ±1 adjacency is SOUND (genuinely firing / non-firing) but not
    scale-tight on decimal fields — tightening via field scale metadata is a
    named D-300 deferral."""
    if _pre_scan(ast) is not None:
        return ()
    meta = field_metadata or {}
    if isinstance(ast, Comparison):
        return _boundary_members(ast, gates=(), meta=meta)
    if isinstance(ast, And):
        # D-328: exactly one threshold conjunct; the rest are gates.
        thresholds = [op for op in ast.operands if _is_threshold(op, meta)]
        if len(thresholds) != 1:
            return ()
        thr = thresholds[0]
        gates = tuple(op for op in ast.operands if op is not thr)
        return _boundary_members(thr, gates=gates, meta=meta)
    return ()                           # Or / Not / function shapes: no single threshold


def derive_context_control(firing: "BoundaryMember",
                           meta: Optional[dict] = None) -> Optional["BoundaryMember"]:
    """The RECORD-dimension **ContextDifferential control arm** (Amendment B §4
    redesign): the SAME above-boundary scenario under the ALTERNATIVE record
    classification, expected to be ACCEPTED — proving the rule is context-scoped
    (the identical value rejected in-context is accepted out-of-context).

    Derived **by reference** from the BoundaryPair's firing member — the
    single-dimension guarantee is structural: exactly ONE mutation (the
    ``RecordTypeId`` key, flipped to a REAL alternative record type off the
    D-348 rail, sorted-deterministic); every other field is copied
    byte-identically, so no second axis can co-vary and the reject↔accept delta
    is attributable to the record-context flip alone. This also realizes the
    composition dedup by construction: the differential's TREATMENT arm *is*
    the firing member (already the claim's primary reject proof) — nothing is
    reconstructed, so nothing can drift.

    ``None`` (the honest 2-member fallback) when the firing member stages no
    ``RecordTypeId`` (the claim is not record-context-gated) or the org exposes
    no alternative record type — a control arm is only meaningful against a
    real alternative classification, never fabricated."""
    rid = (firing.payload or {}).get("RecordTypeId")
    if rid is None:
        return None
    rt_map = (meta or {}).get(_RECORD_TYPES_KEY) or {}
    alternatives = sorted(
        (name, alt) for name, alt in rt_map.items() if alt and alt != rid)
    if not alternatives:
        return None
    _alt_name, alt_rid = alternatives[0]
    return BoundaryMember(
        payload={**firing.payload, "RecordTypeId": alt_rid},
        expect_reject=False, edge="context-control",
        boundary_field=firing.boundary_field)


def _is_threshold(node, meta: Optional[dict] = None) -> bool:
    """A single-field-vs-numeric-literal comparison with a threshold ordering —
    the boundary-eligible conjunct shape. D-300's review-fix held integers only
    (fractional ±1 adjacency is unsound under field-scale rounding); P1 lifts
    that EXACTLY where the unsoundness is cured: a fractional literal qualifies
    iff the field's metadata carries a numeric type + scale, so adjacency steps
    by the field's own representable increment (``minimal_increment``). An
    integer literal stays eligible with or without metadata (the pre-P1
    behaviour); a fractional literal without scale metadata still refuses."""
    if not isinstance(node, Comparison):
        return False
    if isinstance(node.left, FieldRef) and isinstance(node.right, FieldRef):
        return False
    try:
        _field, literal, op = _orient(node)
    except _Undecidable:
        return False
    if literal.kind != "number" or op not in _BOUNDARY_OPS:
        return False
    if isinstance(literal.value, int):
        return True
    fref = node.left if isinstance(node.left, FieldRef) else node.right
    fm = _field_meta(fref, meta or {})
    return minimal_increment((fm or {}).get("field_type"),
                             (fm or {}).get("scale")) is not None


def _boundary_members(thr: Comparison, *, gates: tuple, meta: dict) -> tuple:
    """The (firing, just-inside) pair for threshold ``thr`` with ``gates``
    staged TRUE in both probes. Refuses (``()``) when the threshold shape is
    ineligible, a gate is underivable, or the merge conflicts."""
    if not _is_threshold(thr, meta):
        return ()
    field, literal, op = _orient(thr)
    thr_fref = thr.left if isinstance(thr.left, FieldRef) else thr.right
    thr_fm = _field_meta(thr_fref, meta)
    try:
        gate_payloads = [_satisfy(g, True, meta) for g in gates]
        firing = _unwrap_soft(_merge(
            gate_payloads + [{field: _violating_value(op, literal, thr_fm)}]))
        just_inside = _unwrap_soft(_merge(
            gate_payloads + [{field: _violating_value(_NEG[op], literal, thr_fm)}]))
    except _Undecidable:
        return ()
    return (
        BoundaryMember(payload=firing, expect_reject=True,
                       edge="firing", boundary_field=field),
        BoundaryMember(payload=just_inside, expect_reject=False,
                       edge="just-inside", boundary_field=field),
    )


def _unwrap_soft(payload: dict[str, Any]) -> dict[str, Any]:
    """D-296 boundary strip: a ``_SoftFill`` that survived :func:`_merge` (no hard
    override) becomes its raw non-blank value HERE, at the derive / derive_update
    boundary — NOT inside ``_merge`` (a lone ``NOT(ISBLANK)`` never passes through a
    merge, so a merge-only strip would leak the sentinel into an emitted single-field
    payload and corrupt it). Asserts no sentinel escapes into the emitted payload."""
    out = {k: (v.value if isinstance(v, _SoftFill) else v) for k, v in payload.items()}
    assert not any(isinstance(v, _SoftFill) for v in out.values()), \
        "D-296: _SoftFill escaped the derivation boundary"
    return out


# D-348 (VR08): the ONE cross-object ref the recipe CAN realize on the record
# itself — a record's RecordType. ``RecordType.DeveloperName = "X"`` is satisfied
# by SETTING RecordTypeId to the Id of the record type named X (resolved from the
# ``__record_types__`` rail). Every OTHER dotted ref stays a non-derivable
# related-record read.
_RECORDTYPE_PATH = ("recordtype", "developername")
_RECORD_TYPES_KEY = "__record_types__"


def _is_recordtype_ref(node) -> bool:
    return (isinstance(node, FieldRef) and node.is_dotted
            and tuple(p.lower() for p in node.path) == _RECORDTYPE_PATH)


def _pre_scan(ast) -> Optional[NotDerivable]:
    """Anything that can't be a same-object, flat-state predicate."""
    if isinstance(ast, NotParsed) or ast is None:
        return NotDerivable("formula not parsed")
    for n in walk(ast):
        if isinstance(n, FunctionCall) and n.name in _ORG_STATE_FUNCS:
            return NotDerivable(f"org-state function {n.name} (needs prior/changed/new record state)")
        if isinstance(n, FieldRef) and n.is_dotted and not _is_recordtype_ref(n):
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
        if fm is not None and fm.get("field_type") == "boolean" and _writable(fm):
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


def _is_blank(v) -> bool:
    return v is None or v == ""


def _merge(dicts) -> dict[str, Any]:
    """Merge per-operand assignments (we do not solve multi-constraint-per-field).
    D-296 soft-vs-hard reconciliation: a ``_SoftFill`` (any-non-blank, from
    NOT-ISBLANK) YIELDS to a hard assignment on the same field PROVIDED that hard
    value is itself non-blank — it then subsumes the NOT-ISBLANK constraint (safe
    because a hard cross-field value is a provably-non-blank numeric, as
    :func:`_satisfy_cross_field` refused non-numeric/non-writable fields). A hard
    *blank* survivor, or two unequal hard values, remain a genuine conflict ->
    NotDerivable (the refuse floor)."""
    out: dict[str, Any] = {}
    for d in dicts:
        for field, value in d.items():
            if field not in out:
                out[field] = value
                continue
            existing = out[field]
            e_soft = isinstance(existing, _SoftFill)
            v_soft = isinstance(value, _SoftFill)
            if e_soft and v_soft:
                continue                        # both any-non-blank -> keep existing
            if e_soft or v_soft:
                hard = value if e_soft else existing
                if _is_blank(hard):             # non-blank constraint vs a blank hard
                    raise _Undecidable(f"conflicting assignment on field {field}")
                out[field] = hard               # hard non-blank subsumes the soft fill
                continue
            if existing != value:               # two hard, unequal
                raise _Undecidable(f"conflicting assignment on field {field}")
            # two hard, equal -> keep existing
    return out


def _satisfy_recordtype(node: Comparison, want_true: bool,
                        meta: dict) -> Optional[dict[str, Any]]:
    """D-348 (VR08): satisfy ``RecordType.DeveloperName <op> "X"`` by SETTING the
    record's ``RecordTypeId`` — the typed value boundary's DeveloperName→Id (D-346),
    resolved from the ``__record_types__`` rail. ``None`` when the comparison is not
    a RecordType ref (fall through to the ordinary path); raises ``_Undecidable``
    when it is but can't be certainly realized (unknown record type, unsupported op)."""
    left, right = node.left, node.right
    if isinstance(right, FieldRef) and isinstance(left, Literal):
        left, right = right, left                      # normalize literal-on-left
    if not (_is_recordtype_ref(left) and isinstance(right, Literal)):
        return None
    if node.op not in ("=", "<>"):
        raise _Undecidable(f"RecordType.DeveloperName {node.op} (only =/<> derivable)")
    rt_map = (meta or {}).get(_RECORD_TYPES_KEY) or {}
    devname = right.value
    must_be = (node.op == "=") == want_true            # the record must BE devname
    if must_be:
        rid = rt_map.get(devname)
        if not rid:
            raise _Undecidable(f"RecordType {devname!r} not in the org (record-types rail)")
        return {"RecordTypeId": rid}
    alt = next((rid for name, rid in rt_map.items() if name != devname), None)
    if not alt:
        raise _Undecidable(f"no alternative RecordType to {devname!r}")
    return {"RecordTypeId": alt}


def _satisfy_comparison(node: Comparison, want_true: bool, meta: dict) -> dict[str, Any]:
    rt = _satisfy_recordtype(node, want_true, meta)     # D-348: the RecordType gate
    if rt is not None:
        return rt
    # D-294: a field-to-field comparison (`Loan__c > Property__c`) is derivable
    # when both fields are numeric + writable — synthesize an ordered pair.
    if isinstance(node.left, FieldRef) and isinstance(node.right, FieldRef):
        return _satisfy_cross_field(node.left, node.right, node.op, want_true, meta)
    field, literal, op = _orient(node)
    if want_true is False:
        op = _NEG[op]
    fref = node.left if isinstance(node.left, FieldRef) else node.right
    return {field: _violating_value(op, literal, _field_meta(fref, meta))}


def _field_meta(node: FieldRef, meta: dict) -> Optional[dict]:
    """The rail entry for a FieldRef, by its bare field key (the LAST path
    segment — the field, for both bare ``Loan__c`` and self-qualified
    ``Opportunity.Loan__c`` refs, matching how the rail is keyed). None if absent."""
    return (meta or {}).get(node.path[-1])


def _writable(fm: Optional[dict]) -> bool:
    """D-294 certainty bar — a violating payload can only target a WRITABLE field.
    A calculated (formula / rollup-summary) field, or one that is neither
    createable NOR updateable (system / audit fields), cannot be set: a create or
    update of it would be rejected for the WRONG reason (masking the VR, mis-graded
    passed), so every derive branch that SETS a field refuses when it is not
    writable. Defaults match ``field_details`` (D-160 server_default TRUE), so an
    absent flag reads writable; an absent rail entry (``None``) is not writable."""
    return bool(fm) and not fm.get("is_calculated") and (
        fm.get("is_createable", True) or fm.get("is_updateable", True))


def _satisfy_cross_field(left: FieldRef, right: FieldRef, op: str,
                         want_true: bool, meta: dict) -> dict[str, Any]:
    """Derive an ordered numeric pair for ``left <op> right`` (D-294). Certainty
    bar: refuse (as today) unless BOTH fields have numeric, writable metadata."""
    ml, mr = _field_meta(left, meta), _field_meta(right, meta)
    if ml is None or mr is None:
        raise _Undecidable(
            "field-to-field comparison without field metadata (needs D-294 rail)")
    if ml.get("field_type") not in _NUMERIC or mr.get("field_type") not in _NUMERIC:
        raise _Undecidable("field-to-field comparison on non-numeric field(s)")
    if not _writable(ml) or not _writable(mr):
        raise _Undecidable("field-to-field comparison on a non-writable field")
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


def _violating_value(op: str, literal: Literal,
                     fm: Optional[dict] = None) -> Any:
    """A concrete value making ``field op literal`` TRUE, with certainty.

    P1 (Amendment B): when the field's metadata carries a numeric type + scale,
    the ordered ops step by the field's smallest representable increment in the
    formula domain (``typed_value.minimal_increment``) — the **minimally
    violating** witness (``Discount > 0.25`` on a scale-2 percent → ``0.2501``,
    transport ``25.01``), never an arbitrary ±1 that could collide with field
    precision, another control, or domain plausibility at run time. Without
    scale metadata the ±1 fallback is byte-identical to pre-P1 (the certainty
    bar: sound-but-loose beats a guessed step)."""
    v = literal.value
    if literal.kind == "number":
        inc = minimal_increment((fm or {}).get("field_type"),
                                (fm or {}).get("scale"))
        if inc is not None and op in ("<", ">", "<>"):
            d = Decimal(str(v)) + (inc if op in (">", "<>") else -inc)
            return int(d) if d == d.to_integral_value() else float(d)
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


# D-344: deterministic non-match probes for NOT(REGEX(...)) derivation — varied
# shapes so at least one is a CERTAIN non-match for the simple anchored format
# patterns real VRs use (each verified per-pattern with re.fullmatch). Replay-
# stable (fixed order, no randomness).
_REGEX_NONMATCH_CANDIDATES = (
    "!nv@l!d-format#", "zzzzzzzz", "0", "not a match", "XxYyZz42",
)


def _satisfy_function(node: FunctionCall, want_true: bool, meta: dict) -> dict[str, Any]:
    if node.name == "REGEX":
        # D-344: NOT(REGEX(field, "pat")) fires when `field` does NOT match `pat`.
        # Derive a CERTAIN non-matching, non-blank value — pick the first
        # deterministic candidate that Python re.fullmatch rejects (Salesforce
        # REGEX is a FULL match). The POSITIVE side (a matching value) needs true
        # regex synthesis and stays NotDerivable (bounded on purpose).
        if not (len(node.args) == 2 and isinstance(node.args[0], FieldRef)
                and isinstance(node.args[1], Literal)
                and isinstance(node.args[1].value, str)):
            raise _Undecidable("REGEX without a field + literal pattern")
        if want_true:
            raise _Undecidable("REGEX true (matching-value synthesis not derivable)")
        field = _single_field(node)
        try:
            rx = re.compile(node.args[1].value)
        except re.error:
            raise _Undecidable("REGEX pattern not compilable")
        cand = next((c for c in _REGEX_NONMATCH_CANDIDATES
                     if rx.fullmatch(c) is None), None)
        if cand is None:
            raise _Undecidable("REGEX (no certain non-matching candidate)")
        fm = (meta or {}).get(field)
        if not _writable(fm):
            raise _Undecidable("REGEX (field not writable)")
        payload = {field: cand}
        _self_check(node, payload, want_true)        # NonEvaluable -> trusts re-verified value
        return payload
    if node.name in ("ISBLANK", "ISNULL"):
        field = _single_field(node)
        if want_true:
            return {field: None}                     # blank fires the rejection
        # D-294: NOT(ISBLANK) fires when the field is NON-blank -> synthesize a
        # certain non-blank value from field metadata (else refuse — the bar).
        fm = (meta or {}).get(field)
        v = _nonblank_value(fm)                       # None/absent -> UNSYNTHESIZABLE
        if v is _UNSYNTHESIZABLE:
            raise _Undecidable(f"NOT {node.name} (no synthesizable non-blank value)")
        if not _writable(fm):                         # value exists but field is read-only
            raise _Undecidable(f"NOT {node.name} (field not writable)")
        payload = {field: v}
        _self_check(node, payload, want_true)        # evaluate confirms it fires (raw)
        # D-296: return a SOFT fill — any non-blank value satisfies NOT-ISBLANK, so a
        # hard cross-field assignment on the same field may override it in _merge.
        return {field: _SoftFill(v)}
    if node.name == "ISPICKVAL":
        field = _single_field(node)
        if not (len(node.args) == 2 and isinstance(node.args[1], Literal)):
            raise _Undecidable("ISPICKVAL without a literal value")
        forbidden = node.args[1].value
        if want_true:
            return {field: forbidden}
        # D-294: NOT(ISPICKVAL(field,"X")) fires when field != "X" -> set a
        # certain alternative from the field's active picklist values (rail 2-hop).
        # Refuse (as today) when the field is not writable, the set is absent
        # (inline picklist S1 did not capture), or has no value other than "X".
        fm = (meta or {}).get(field)
        vals = (fm or {}).get("picklist_values")
        alt = next((v for v in (vals or ()) if v != forbidden), None)
        if alt is None:
            raise _Undecidable("NOT ISPICKVAL (no certain alternative picklist value)")
        if not _writable(fm):
            raise _Undecidable("NOT ISPICKVAL (field not writable)")
        payload = {field: alt}
        _self_check(node, payload, want_true)        # evaluate confirms field != "X"
        return payload
    raise _Undecidable(f"function {node.name} not derivable")


def _single_field(node: FunctionCall) -> str:
    if node.args and isinstance(node.args[0], FieldRef):
        return node.args[0].path[0]                  # dotted already pre-scanned out
    raise _Undecidable(f"{node.name} without a single field argument")

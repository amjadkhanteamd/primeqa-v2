"""D-454: the partial-coverage flag — visibility for claims that cover only
part of their mechanism rule's condition.

FLAG-FOR-REVIEW ONLY, by measurement: D-453 sized the refuse ceiling at
159/215 approved claims declining (112 of them live-green), so this check
DECIDES NOTHING. It records. Three verdicts, never two (the D-412
discipline):

  * ``COVERED``       — every conjunct field of some firing path of the
                        mechanism rule is pinned by the claim's
                        staged+asserted set.
  * ``PARTIAL``       — the rule parses, at least one field is pinned, and
                        no firing path is fully pinned; the flag names the
                        mechanism VR, the covered fields, and the missing
                        conjunct fields VERBATIM (the visibility D-452
                        found structurally absent from the persisted claim).
  * ``CANNOT_ASSESS`` — the honest refusal to guess: the rule does not
                        parse (TEXT()/IF/$-globals — the founding specimen
                        4f52b937's TA7 lands here, with the parse reason),
                        or the claim's persisted staging is EMPTY (the
                        R1-padding-dependent shape — 37d9dac4; run-time
                        padding is invisible to authoring-time analysis).

Coverage is DISJUNCT-AWARE over the parsed AST: ``Or`` is satisfied by ANY
covered disjunct (a claim staging one disjunct is fully covered — conjunct
flattening would false-flag it); ``And`` requires all children; ``Not``
passes through to its operand's fields (pinning them is what makes the
negation knowable); field-less leaves (ISNEW(), constant booleans) are
trivially covered.

EQUIVALENCE-AWARE: any pin the evaluator can prove counts as coverage —
staged ``RecordTypeId`` covers a ``RecordType.DeveloperName`` ref (the
D-439 resolver equivalence). Other dotted refs are never pinnable from a
single-object payload.

An INACTIVE mechanism rule is its own named state (``mechanism_inactive``)
— a claim whose mechanism is switched off is vacuous by a different route
than partial staging.

Pure module: no DB, no S1, no I/O. Callers resolve the mechanism rules and
the pinned field set; this module only assesses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from primeqa.semantic.formula import parse
from primeqa.semantic.formula.nodes import (
    And,
    FieldRef,
    Not,
    NotParsed,
    Or,
)

COVERED = "COVERED"
PARTIAL = "PARTIAL"
CANNOT_ASSESS = "CANNOT_ASSESS"

# D-439-proven equivalence: a staged RecordTypeId pins the record's
# RecordType, so a `RecordType.DeveloperName` ref is covered by it.
_RT_REF = "recordtype.developername"
_RT_PIN = "recordtypeid"


@dataclass(frozen=True)
class CoverageFlag:
    """One mechanism rule's coverage verdict for one claim bundle."""

    verdict: str                       # COVERED | PARTIAL | CANNOT_ASSESS
    vr_name: Optional[str]             # best-effort (formula-text match)
    vr_formula: str                    # verbatim
    mechanism_kind: str                # 'grounding' | 'boundary-literal-proxy'
    mechanism_inactive: bool = False   # the switched-off-mechanism sub-flag
    covered_fields: tuple = ()
    missing_fields: tuple = ()
    reason: Optional[str] = None       # CANNOT_ASSESS only

    def to_payload(self) -> dict:
        d = {
            "verdict": self.verdict,
            "vr_name": self.vr_name,
            "vr_formula": self.vr_formula,
            "mechanism_kind": self.mechanism_kind,
        }
        if self.mechanism_inactive:
            d["mechanism_inactive"] = True
        if self.verdict == PARTIAL:
            d["covered_fields"] = list(self.covered_fields)
            d["missing_fields"] = list(self.missing_fields)
        if self.reason is not None:
            d["reason"] = self.reason
        return d


def _leaf_fields(node, out: set) -> None:
    if isinstance(node, FieldRef):
        if node.is_dotted:
            out.add("<dotted>" + node.name.lower())
        else:
            out.add(node.path[0].lower())
        return
    for attr in ("operand", "operands", "args", "left", "right"):
        v = getattr(node, attr, None)
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            for x in v:
                _leaf_fields(x, out)
        else:
            _leaf_fields(v, out)


def _pinned(f: str, pins: frozenset) -> bool:
    if f.startswith("<dotted>"):
        # The one provable equivalence (D-439): RecordTypeId pins the
        # RecordType, so its DeveloperName ref is covered.
        return f == "<dotted>" + _RT_REF and _RT_PIN in pins
    return f in pins


def _covered(node, pins: frozenset) -> bool:
    """DISJUNCT-AWARE coverage: is some firing path of ``node`` fully
    pinned? Or → any child; And → all children; Not → pass-through."""
    if isinstance(node, And):
        return all(_covered(c, pins) for c in node.operands)
    if isinstance(node, Or):
        return any(_covered(c, pins) for c in node.operands)
    if isinstance(node, Not):
        return _covered(node.operand, pins)
    leaves: set = set()
    _leaf_fields(node, leaves)
    return all(_pinned(f, pins) for f in leaves)


def assess_rule_coverage(
    *, vr_name: Optional[str], vr_formula: str, vr_active: Optional[bool],
    pinned_fields, mechanism_kind: str,
) -> CoverageFlag:
    """Assess ONE mechanism rule against the claim's pinned (staged +
    asserted) bare field names. Pure; never raises on recognized input."""
    pins = frozenset(str(f).lower() for f in (pinned_fields or ()))
    inactive = vr_active is False
    ast = parse(vr_formula)
    if isinstance(ast, NotParsed) or ast is None:
        return CoverageFlag(
            verdict=CANNOT_ASSESS, vr_name=vr_name, vr_formula=vr_formula,
            mechanism_kind=mechanism_kind, mechanism_inactive=inactive,
            reason=(f"mechanism formula does not parse "
                    f"({getattr(ast, 'reason', 'unparseable')}) — coverage "
                    f"is unknowable, refusing to guess (D-454)"))
    if not pins:
        return CoverageFlag(
            verdict=CANNOT_ASSESS, vr_name=vr_name, vr_formula=vr_formula,
            mechanism_kind=mechanism_kind, mechanism_inactive=inactive,
            reason=("empty persisted staging — the executed state depends "
                    "on run-time R1 padding, which authoring-time analysis "
                    "cannot see (D-454)"))
    if _covered(ast, pins):
        return CoverageFlag(
            verdict=COVERED, vr_name=vr_name, vr_formula=vr_formula,
            mechanism_kind=mechanism_kind, mechanism_inactive=inactive)
    leaves: set = set()
    _leaf_fields(ast, leaves)
    covered_f = tuple(sorted(f for f in leaves if _pinned(f, pins)))
    missing_f = tuple(sorted(f for f in leaves if not _pinned(f, pins)))
    return CoverageFlag(
        verdict=PARTIAL, vr_name=vr_name, vr_formula=vr_formula,
        mechanism_kind=mechanism_kind, mechanism_inactive=inactive,
        covered_fields=covered_f, missing_fields=missing_f)

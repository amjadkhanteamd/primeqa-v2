"""Representation checks for staged/asserted literals (D-426).

Deterministic, LLM-free, read-only post-emission checks beside
``value_membership.py`` — a RATCHET against the D-425.1
representation-mismatch class re-entering the approved corpus (generation
emitting a plausible HUMAN-READABLE string where the field holds a MACHINE
value), not remediation of an active problem: the class is already
self-cleaning via deprecate-then-regen (5 run-corpus specimens, 2 survivors
in 187 approved when this was built).

**Three verdicts, never two (D-399.1 — the binding constraint).** The
``Verdict`` enum is value_membership's own; a two-verdict design converts
every knowledge gap into a silent refusal, inverting fail-loud.

**Check 1 — placeholder leak.** Full-match on the stripped literal, two
tiers; prose is never parsed (the D-412 structured extraction walker is
reused verbatim):

  structural   ``^<[^<>]{1,120}>$`` — a bare angle-bracketed token as the
               COMPLETE value is a template artifact with zero legitimate
               variants (``<computed>``, ``<derived from submission date>``).
               INVALID on any non-enumerated field, known or unknown type.
  filler       the CLOSED word list {provided, tbd, todo, placeholder},
               case-insensitive. INVALID only where S1 KNOWS the field's
               type and it is non-enumerated; unknown type →
               CANNOT_VALIDATE. The list is deliberately tiny — N/A /
               Unknown / Sample are excluded as too plausible.

Jurisdiction boundary: literals on ENUMERATED fields (picklist /
multipicklist) get NO checks here — D-412 membership owns them, so an
org-real enumerated ``"provided"`` can never be falsely refused by this
module, and a hallucinated one is already declined by membership.

Declared false-positive surface (D-426): a genuine business value that IS
one of the four filler words (or an angle-bracketed token) as the complete
value of a known free-text field. Zero instances in the current corpus;
the cost is a LOUD declination at generation time, recoverable by regen —
never a silent hole. If the surface materialises on a real org, the
word/pattern moves behind an org-real corroboration read, not a wider
heuristic.

**Check 2 — label-vs-Id.** For a string literal on a field whose S1 type
is KNOWN to be ``reference`` or ``id``: symbolic step-refs (``$…``, the
bridge grammar) are VALID (pinned); Id-shaped values (15/18 alphanumeric)
are VALID — representation only, existence is another validator's job;
anything else is INVALID (``label_on_id_field`` — the 0d81c6f9 shape:
``OwnerId = "Credit Manager"``). Unknown/uncaptured type →
CANNOT_VALIDATE, NEVER INVALID; a field S1 cannot resolve at all is
field-grounding's jurisdiction (no check), mirroring membership's
``None → []``.

Non-string literals — numbers, booleans, None, and structured envelopes
like the relative-date dict — are outside both checks by construction.

**Fail loud on its own errors** (D-399.1 corollary): the index builder
raises :class:`RepresentationCheckError` rather than degrade to a
permissive index — a pass this module did not compute is a silent
wrong-green of its own.

SCALE/FORMAT IS DELIBERATELY ABSENT (D-426): flag-only semantics,
legitimate variants exist, zero current instances.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Optional

from primeqa.generation.value_membership import (
    Verdict,
    extract_field_literals,
)


class RepresentationCheckError(RuntimeError):
    """The checker itself failed (S1 unreadable). Never swallow into a pass."""


# Tier 1 — structural: a bare angle-bracketed token as the COMPLETE value.
_STRUCTURAL = re.compile(r"^<[^<>]{1,120}>$")
# Tier 2 — the CLOSED filler list (case-insensitive full match).
FILLER_WORDS = frozenset({"provided", "tbd", "todo", "placeholder"})
# A Salesforce record Id: 15 or 18 alphanumeric characters.
_SF_ID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
# A symbolic step-ref (the bridge grammar: ``$<step>.id`` and kin).
_SYMBOLIC = re.compile(r"^\$[A-Za-z0-9_.\-]+$")

# S1 field types under each check's jurisdiction.
ENUMERATED_TYPES = frozenset({"picklist", "multipicklist"})
ID_TYPES = frozenset({"reference", "id"})


@dataclass(frozen=True)
class RepresentationCheck:
    """One (field, literal) representation check."""
    field: str                      # Object.Field
    value: str
    check: str                      # 'placeholder' | 'label_vs_id'
    verdict: Verdict
    # INVALID  -> 'placeholder_structural' | 'placeholder_filler'
    #             | 'label_on_id_field'
    # VALID    -> 'symbolic_ref' | 'id_shaped' | 'plain_value'
    # CANNOT_VALIDATE -> 'unknown_field_type'
    detail: str
    field_type: Optional[str]       # the S1 type consulted (None = unknown)


@dataclass
class RepresentationValidation:
    """All representation checks for one claim/bundle body set."""
    checks: list = dc_field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        """Roll-up mirroring value_membership: INVALID dominates, then
        CANNOT_VALIDATE; a body set with nothing in jurisdiction is
        vacuously VALID."""
        vs = {c.verdict for c in self.checks}
        if Verdict.INVALID in vs:
            return Verdict.INVALID
        if Verdict.CANNOT_VALIDATE in vs:
            return Verdict.CANNOT_VALIDATE
        return Verdict.VALID

    @property
    def invalid_checks(self) -> list:
        return [c for c in self.checks if c.verdict == Verdict.INVALID]


class FieldTypeIndex:
    """The S1 field types for the fields a bundle references, loaded once.

    Construct directly for tests; :meth:`from_s1` is the emission-path
    builder (the same seam ``FieldCaptureIndex.from_s1`` uses). A field
    that does not resolve in S1 is simply NOT in the index — out of this
    module's jurisdiction for the filler/Id checks (structural placeholder
    still judges, per D-426). A resolved field whose detail row carries no
    ``field_type`` maps to ``None`` — known-field-unknown-type →
    CANNOT_VALIDATE downstream.
    """

    def __init__(self, types: dict[str, Optional[str]]):
        self._types = types

    @classmethod
    def from_s1(cls, s1, at_seq: int,
                field_names: Iterable[str]) -> "FieldTypeIndex":
        types: dict[str, Optional[str]] = {}
        for name in field_names:
            try:
                ents = s1.get_entities("Field", at_seq=at_seq,
                                       filters={"sf_api_name": name})
                if not ents:
                    continue
                details = s1.get_entity_details(ents[0].id, at_seq=at_seq) or {}
            except Exception as e:  # noqa: BLE001 — wrap, never pass-as-pass
                raise RepresentationCheckError(
                    f"cannot read S1 field type for {name!r}: {e}") from e
            ftype = details.get("field_type")
            types[name] = ftype.lower() if isinstance(ftype, str) else None
        return cls(types)

    def field_type(self, field: str) -> Optional[str]:
        return self._types.get(field)

    def knows(self, field: str) -> bool:
        return field in self._types

    # -- the checks --------------------------------------------------------

    def check_literal(self, field: str, value: Any) -> list[RepresentationCheck]:
        """Representation checks for one (field, literal). Non-string
        literals are out of scope; enumerated fields are membership's
        jurisdiction (no checks here)."""
        if not isinstance(value, str):
            return []
        ftype = self._types.get(field)
        known = field in self._types
        if known and ftype in ENUMERATED_TYPES:
            return []                      # D-412 membership's jurisdiction

        stripped = value.strip()
        checks: list[RepresentationCheck] = []

        # -- Check 1: placeholder leak --------------------------------------
        if _STRUCTURAL.match(stripped):
            # Zero legitimate variants — INVALID even on an unknown type.
            checks.append(RepresentationCheck(
                field, value, "placeholder", Verdict.INVALID,
                "placeholder_structural", ftype))
        elif stripped.lower() in FILLER_WORDS:
            if known and ftype is not None:
                checks.append(RepresentationCheck(
                    field, value, "placeholder", Verdict.INVALID,
                    "placeholder_filler", ftype))
            else:
                # Unknown type: the word could be an org-real enumerated
                # value we cannot see — no refusal on a knowledge gap.
                checks.append(RepresentationCheck(
                    field, value, "placeholder", Verdict.CANNOT_VALIDATE,
                    "unknown_field_type", ftype))

        # -- Check 2: label-vs-Id -------------------------------------------
        if known and ftype in ID_TYPES:
            if _SYMBOLIC.match(stripped):
                checks.append(RepresentationCheck(
                    field, value, "label_vs_id", Verdict.VALID,
                    "symbolic_ref", ftype))
            elif _SF_ID.match(stripped):
                checks.append(RepresentationCheck(
                    field, value, "label_vs_id", Verdict.VALID,
                    "id_shaped", ftype))
            else:
                checks.append(RepresentationCheck(
                    field, value, "label_vs_id", Verdict.INVALID,
                    "label_on_id_field", ftype))
        return checks

    def validate(self, *bodies: Any) -> RepresentationValidation:
        """Extract + check every structured literal in ``bodies`` — the same
        extraction walker as membership (prose never parsed)."""
        result = RepresentationValidation()
        for f, v in extract_field_literals(*bodies):
            result.checks.extend(self.check_literal(f, v))
        return result

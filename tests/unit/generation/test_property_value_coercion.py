"""Property-claim grounding: semantic value comparison (D-246).

Unit/conformance proof for ``_property_value_matches`` — the exact comparator
the property resolver uses at the ``insufficient_grounding`` gate
(``governance_core.py`` ``_resolve_property``: ``if asserted is not None and
not _property_value_matches(asserted, s1_value)``).

The fix: an LLM-asserted value expressed in a different representation (the
string ``"8"`` for an S1 integer ``8``) must GROUND, not dismiss; a genuinely
different value (``"9"`` vs ``8``) must still DISMISS. Coercion is generic
against the S1-stored native type (never type-specific), and lossy/ambiguous
coercion is rejected so we never false-match. The detail properties in play
(length / precision / scale) are ints here; a str-native case is included to
prove the comparator is not int-hardcoded.

Blast radius: this comparison is global to property-claim grounding, not just
SQ-212 — the DISMISS cases below are the guard against false matches.
"""
from __future__ import annotations

import pytest

from primeqa.generation.governance_core import _property_value_matches


# (asserted, s1_value, expect_match) — the comparator truth table.
@pytest.mark.parametrize("asserted, s1_value, expect_match", [
    # --- the fix: correct value, different representation -> GROUNDS ---
    ("8", 8, True),        # AC6 SLA_Code__c length=8: string "8" vs int 8
    ("4", 4, True),        # AC7 precision=4 (as a single property) vs int 4
    ("1", 1, True),        # AC7 scale=1 vs int 1
    ("255", 255, True),    # another length, larger
    ("0", 0, True),        # zero edge
    (8, 8, True),          # already-native int (untouched path)

    # --- preserve correct rejection: genuinely different value -> DISMISSES ---
    ("9", 8, False),       # the canonical wrong value
    (9, 8, False),         # wrong value, native int
    ("80", 8, False),      # not a representation of 8
    ("5", 4, False),       # wrong precision
    ("2", 1, False),       # wrong scale

    # --- false-match guards: lossy / ambiguous / uncoercible -> DISMISSES ---
    (8.7, 8, False),       # float truncation int(8.7)==8 must NOT match
    ("8.0", 8, False),     # int("8.0") raises -> uncoercible -> dismiss
    ("08", 8, False),      # odd repr: round-trip str(8)="8" != "08" -> dismiss
    ("abc", 8, False),     # uncoercible to int -> dismiss
    (None, 8, False),      # no assertion (resolver guards this; helper is safe)

    # --- generic, not int-hardcoded: str-native S1 value ---
    (8, "8", True),        # int 8 vs S1 string "8" coerces + round-trips
    ("x", "x", True),      # str == str, untouched path
    ("y", "x", False),     # different strings dismiss
])
def test_property_value_matches(asserted, s1_value, expect_match):
    assert _property_value_matches(asserted, s1_value) is expect_match

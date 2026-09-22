"""Round 4 (AUD-043): a test-design RelativeDate value reads in words wherever a
claim's asserted value is rendered — never as the object's Python repr."""
from __future__ import annotations

import pytest

from primeqa.intelligence.claim_presentation import _literal, relative_date_in_words
from primeqa.test_representation.temporal import relative_date

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("offset, words", [
    (0, "the run date"), (1, "1 day after the run date"), (5, "5 days after the run date"),
    (-1, "1 day before the run date"), (-30, "30 days before the run date")])
def test_a_relative_date_reads_in_words(offset, words):
    assert relative_date_in_words(relative_date(offset)) == words
    assert _literal(relative_date(offset)) == words
    assert _literal({"value": relative_date(offset)}) == words           # inside a LiteralValue


def test_other_values_are_untouched():
    assert relative_date_in_words("2026-09-22") is None
    assert relative_date_in_words({"$relative_date": {"anchor": "TODAY", "offset_days": 1}}) is None
    assert _literal("Activated") == '"Activated"' and _literal(3) == "3"
    assert _literal({"value": "x"}) == '"x"'
    assert "$relative_date" not in _literal(relative_date(5))

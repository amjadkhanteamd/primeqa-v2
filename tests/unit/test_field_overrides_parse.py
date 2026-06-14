"""Unit: D-235 — the run-time test-data injection textarea parser
(`_parse_field_overrides`). One `Field=Value` per line → a bare-keyed dict;
tolerant of blanks / missing '=' / whitespace; capped."""
from __future__ import annotations

import pytest

from primeqa.views import _MAX_FIELD_OVERRIDES, _parse_field_overrides

pytestmark = pytest.mark.unit


def test_parses_lines_to_dict():
    assert _parse_field_overrides("Amount=2000000\nStageName=Prospecting") == {
        "Amount": "2000000", "StageName": "Prospecting"}


def test_trims_whitespace_and_keeps_value_spaces():
    assert _parse_field_overrides("  Name =  Acme Corp  ") == {"Name": "Acme Corp"}


def test_skips_blank_and_malformed_lines():
    assert _parse_field_overrides("Amount=5\n\n   \nno-equals-here\n=novalue-key") == {
        "Amount": "5"}


def test_value_may_contain_equals():
    assert _parse_field_overrides("Formula=a=b=c") == {"Formula": "a=b=c"}


def test_empty_and_none_yield_empty():
    assert _parse_field_overrides("") == {}
    assert _parse_field_overrides(None) == {}


def test_caps_entry_count():
    raw = "\n".join(f"F{i}=v{i}" for i in range(_MAX_FIELD_OVERRIDES + 25))
    assert len(_parse_field_overrides(raw)) == _MAX_FIELD_OVERRIDES


def test_caps_key_and_value_length():
    out = _parse_field_overrides("K" * 200 + "=" + "V" * 500)
    (k, v), = out.items()
    assert len(k) == 100 and len(v) == 255

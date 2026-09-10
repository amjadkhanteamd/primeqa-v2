"""The class behind the Step 4 plan-view 500, closed for every template: Jinja's
subscript on a dict falls back to ATTRIBUTE lookup when the key is absent, so
``x['keys']`` (or ``['items']``, ``['values']``, ``['get']`` …) on a row that
lacks the entry silently yields the dict's own method — and the next filter
dies at request time. Templates read such entries with ``.get(...)``.
"""
from __future__ import annotations

import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "templates"
_METHOD_NAMES = "keys|values|items|get|copy|update|pop|clear|setdefault|fromkeys"
_SUBSCRIPT = re.compile(r"\[['\"](" + _METHOD_NAMES + r")['\"]\]")
# attribute-style reads of a dict-method NAME that are not calls (``x.keys`` meaning "the item")
_ATTRIBUTE = re.compile(r"\.(" + _METHOD_NAMES + r")\b(?!\s*\()")


def _hits(pattern):
    out = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                out.append(f"{path.relative_to(TEMPLATES)}:{i}: {line.strip()[:100]}")
    return out


def test_no_template_subscripts_a_dict_method_name():
    assert _hits(_SUBSCRIPT) == []


def test_no_template_reads_a_dict_method_name_as_an_attribute():
    assert _hits(_ATTRIBUTE) == []

"""AUD-042 (round 3, part E): an HTML entity inside a Jinja EXPRESSION is text,
and Jinja escapes it — so the page shows the entity's source, not the character.

Found on a production render: the landing page's flaky list shows
``errored &middot; errored`` because the template joins with the STRING
``' &middot; '`` inside ``{{ ... }}``; and the run page shows
``Loans over &amp;#8377;50,00,000`` because the org's own message already
carries ``&#8377;`` and nothing unescapes it before Jinja escapes it again.

CLAUDE.md states the rule already ("never write \\uXXXX escapes directly in
Jinja/HTML content — use the actual UTF-8 character or &#NNNN;"); this is the
same rule for the other direction: an entity belongs in the TEMPLATE's markup,
never inside an expression the template will escape.

Round 4 closed the finding: the message is unescaped once at the read and the
flaky list joins with the character; the assertion is the gate now.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "templates"

#: an HTML entity inside {{ ... }} — the expression's value is escaped, so the
#: entity's own ampersand becomes &amp; and the reader sees the source text
ENTITY_IN_EXPRESSION = re.compile(r"\{\{[^}]*&(?:[a-zA-Z][a-zA-Z0-9]+|#\d+);[^}]*\}\}")


def _hits(root):
    out = []
    for path in sorted(root.rglob("*.html")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            for m in ENTITY_IN_EXPRESSION.finditer(line):
                if re.search(r"\|\s*safe\b", m.group(0)):
                    continue                     # marked safe: rendered as markup on purpose
                out.append(f"{path.relative_to(root)}:{i}: {m.group(0)[:80]}")
    return out


def test_no_template_puts_an_html_entity_inside_a_jinja_expression():
    """Round 4 (AUD-042): the flaky list joins with the character now; the
    strict marker is gone and this is the gate."""
    hits = _hits(TEMPLATES)
    assert hits == [], "an entity inside an expression is escaped and shown as source:\n  " + "\n  ".join(hits)


def test_the_check_finds_a_planted_case_and_not_an_entity_in_markup(tmp_path):
    """The gate is shown able to fail: an entity inside {{ }} is reported; an
    entity in ordinary markup (outside any expression) is not."""
    (tmp_path / "bad.html").write_text("<span>{{ items|join(' &middot; ') }}</span>\n")
    (tmp_path / "good.html").write_text("<p>approved &middot; open &rarr;</p><b>{{ n }}&#8377;</b>\n"
                                        "{{ btn('&#9654; Run' | safe) }} {{ x or '&mdash;' | safe }}\n")
    hits = _hits(tmp_path)
    assert hits == ["bad.html:1: {{ items|join(' &middot; ') }}"], hits

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

The finding is open and AK triages it, so the assertion is marked
``xfail(strict=True)``: it does not pass today, it does not pretend to, and the
day someone fixes it the strict marker turns the unexpected pass into a
failure that says "remove this marker".
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


def _hits():
    out = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            for m in ENTITY_IN_EXPRESSION.finditer(line):
                out.append(f"{path.relative_to(TEMPLATES)}:{i}: {m.group(0)[:80]}")
    return out


@pytest.mark.xfail(strict=True, reason="AUD-042 is open: the flaky list joins with ' &middot; ' inside an expression")
def test_no_template_puts_an_html_entity_inside_a_jinja_expression():
    hits = _hits()
    assert hits == [], "an entity inside an expression is escaped and shown as source:\n  " + "\n  ".join(hits)


def test_the_check_finds_the_known_case_and_nothing_else():
    """The gate is shown to work: it names the case the production render
    showed, and it does not fire on an entity in ordinary markup."""
    hits = _hits()
    assert any("dashboard.html" in h and "middot" in h for h in hits), hits
    # an entity in markup (outside {{ }}) is correct and must NOT be reported
    assert not any(re.search(r"^\S+:\d+: [^{]", h) for h in hits), hits

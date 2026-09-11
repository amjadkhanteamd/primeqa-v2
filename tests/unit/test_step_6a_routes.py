"""Step 6a — route hygiene (LLD_STEP_6A §f): every moved path answers, and no
template links anywhere that neither resolves nor redirects."""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "templates"


def _rules():
    from primeqa.app import app
    return app.url_map


def test_every_moved_path_still_answers():
    rules = {str(r.rule) for r in _rules().iter_rules()}
    for path in ("/claims", "/claims/inbox", "/reviews", "/test-cases",
                 "/ui-report", "/ui-report/runs/<job_id>", "/dashboard",
                 "/run", "/runs", "/tickets", "/suites"):
        assert path in rules, f"{path} no longer answers — §f says it must, for one release cycle"


def test_the_rehomed_surfaces_exist():
    rules = {str(r.rule) for r in _rules().iter_rules()}
    assert "/runs/conformance/<job_id>" in rules
    assert "/requirements" in rules and "/runs/substrate" in rules


def test_no_template_links_to_a_path_that_does_not_answer():
    """The dead-link sweep: every internal href in a template must match a
    route, a redirect or a static asset. Anything else is a dead link."""
    from primeqa.app import app
    rules = list(app.url_map.iter_rules())
    literal = {str(r.rule) for r in rules if "<" not in str(r.rule)}
    patterns = [re.compile("^" + re.sub(r"<[^>]+>", "[^/]+", str(r.rule)) + "$")
                for r in rules if "<" in str(r.rule)]
    href = re.compile(r'href="(/[^"{}\s]*)"')
    dead = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            for url in href.findall(line):
                clean = url.split("?")[0].split("#")[0].rstrip("/") or "/"
                if clean.startswith("/static"):
                    continue
                if clean in literal or any(p.match(clean) for p in patterns):
                    continue
                dead.append(f"{path.relative_to(TEMPLATES)}:{i}: {url}")
    assert dead == [], "dead internal links:\n  " + "\n  ".join(dead)

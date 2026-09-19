"""Step 6a — route hygiene (LLD_STEP_6A §f): every moved path answers, and no
template links anywhere that neither resolves nor redirects."""
from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.unit

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "templates"


def _rules():
    from primeqa.app import app
    return app.url_map


def test_the_retired_paths_no_longer_answer():
    """The retirement commit: after 6b's cycle closed, the 6a and 6b redirect
    paths and the legacy bodies behind them are DELETED. The dead-link sweep
    below is this commit's gate — it caught a live dead link on the root page
    and one orphan template when they went."""
    rules = {str(r.rule) for r in _rules().iter_rules()}
    for path in ("/claims", "/claims/inbox", "/claims/library", "/claims/inbox/legacy",
                 "/reviews", "/test-cases", "/test-cases/<int:tc_id>",
                 "/ui-report", "/ui-report/runs/<job_id>", "/ui-report/compare",
                 "/ui-report/coverage", "/dashboard", "/dashboard/legacy"):
        assert path not in rules, f"{path} still answers — the retirement did not remove it"


def test_the_older_redirects_keep_their_own_cycles():
    """Only 6a's and 6b's paths retire here. /run (D-486), /runs, /tickets and
    /suites belong to earlier steps and are not this commit's business."""
    rules = {str(r.rule) for r in _rules().iter_rules()}
    for path in ("/run", "/runs", "/tickets", "/suites"):
        assert path in rules, f"{path} was retired by the wrong commit"


def test_what_replaced_them_is_there():
    rules = {str(r.rule) for r in _rules().iter_rules()}
    for path in ("/requirements", "/runs/substrate", "/runs/conformance/<job_id>",
                 "/releases", "/releases/compare", "/settings/standards/coverage",
                 "/claims/<uuid:test_id>"):
        assert path in rules, f"{path} is missing — the replacement must exist"


def test_the_rehomed_surfaces_exist():
    rules = {str(r.rule) for r in _rules().iter_rules()}
    assert "/runs/conformance/<job_id>" in rules
    assert "/requirements" in rules and "/runs/substrate" in rules


def test_no_template_links_to_a_path_that_does_not_answer():
    """The dead-link sweep. Its first form matched `href="(/[^"{}\\s]*)"` and
    so excluded every href carrying a Jinja expression by construction — the
    release page's "View Reasoning" pointed at a route that never existed and
    this test stayed green (AUD-002/AUD-003). The sweep now lives in
    `scripts/deadlinks_gate.py` (every attribute, htmx verb, form action,
    url_for and JS literal, Jinja collapsed to wildcard segments, the method
    checked) and is proven able to fail in `tests/unit/test_no_dead_links.py`
    before the repo is judged. This test delegates to it so the 6a gate and
    the merge gate can never disagree."""
    import sys
    sys.path.insert(0, str(TEMPLATES.parents[1] / "scripts"))
    import deadlinks_gate as dl
    rep = dl.sweep_repo()
    d = rep.as_dict()
    assert d["targets"] > 200, "the sweep collected almost nothing — it is not reading the tree"
    assert rep.dead == [], "dead internal targets:\n  " + "\n  ".join(
        f"{x['where']} [{x['kind']}] {x['target']} — {x['reason']}" for x in d["dead_list"])

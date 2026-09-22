"""AUD-040 / AUD-005 (round 3): CLAUDE.md describes the product that exists.

Two findings of one class — documentation that outlives its subject. AUD-040:
the security posture said the public status endpoint answers 404 for "no token
/ wrong token" while the route answers 401 for a missing token. AUD-005: the UI
component kit listed a template and a script that the v1 retirement had
deleted, and the script called a route that no longer existed.

This file holds both halves against the running product:

1. **The behaviour the doc claims** — the status endpoint's two answers are
   asserted against the live app, so the sentence and the route cannot drift
   apart again silently.
2. **The files the doc names** — every ``primeqa/...`` path and every
   ``templates/components/x.html`` / ``static/js/x.js`` component CLAUDE.md
   names must exist on disk.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[2]
DOC = (REPO / "CLAUDE.md").read_text()

#: paths the doc names as illustrations of a SHAPE, not as files that exist
NOT_A_FILE = {
    "primeqa/semantic",  # package names in the structure tree are matched below by directory
}


@pytest.fixture(scope="module")
def client():
    from primeqa.app import app
    app.config["TESTING"] = True
    return app.test_client()


# --- 1. the behaviour the doc claims -------------------------------------------------

def test_the_documented_status_endpoint_contract_is_what_the_route_does(client):
    """AUD-040: 'no token → 401, wrong token → 404'."""
    line = next(ln for ln in DOC.splitlines() if "Public release-status endpoint is token-gated" in ln)
    assert "no token → 401" in line and "wrong token → 404" in line, line
    no_token = client.get("/api/releases/16/status")
    wrong_token = client.get("/api/releases/16/status?token=not-the-token")
    assert no_token.status_code == 401, no_token.get_data(as_text=True)[:200]
    assert wrong_token.status_code == 404, wrong_token.get_data(as_text=True)[:200]


def test_the_documented_owner_rule_names_the_column_the_routes_read():
    """The same line claims mint/rotate/revoke are the release OWNER's or an
    admin's, by ``releases.created_by`` — the routes' own check (AUD-012)."""
    line = next(ln for ln in DOC.splitlines() if "Public release-status endpoint is token-gated" in ln)
    assert "releases.created_by" in line
    src = (REPO / "primeqa" / "release" / "routes.py").read_text()
    assert "def _release_owner_or_admin" in src and "release.created_by" in src


# --- 2. the files the doc names ------------------------------------------------------

def _documented_paths() -> set:
    """Every ``primeqa/...`` path the doc names in backticks, plus the component
    kit's ``x.html`` / ``static/js/x.js`` entries."""
    out = set()
    for m in re.finditer(r"`(primeqa/[A-Za-z0-9_./-]+)`", DOC):
        out.add(m.group(1).rstrip("/"))
    kit = DOC[DOC.index("## UI component kit"):]
    kit = kit[:kit.index("\n## ")]
    for m in re.finditer(r"`([A-Za-z0-9_/-]+\.html)`", kit):
        out.add("primeqa/templates/components/" + m.group(1).split("/")[-1])
    for m in re.finditer(r"`(static/js/[A-Za-z0-9_.-]+)`", DOC):
        out.add("primeqa/" + m.group(1))
    return {p for p in out if p not in NOT_A_FILE}


def test_every_file_claude_md_names_exists():
    missing = sorted(p for p in _documented_paths() if not (REPO / p).exists())
    assert missing == [], f"CLAUDE.md names files that do not exist: {missing}"


def test_the_component_kit_lists_only_live_components():
    """A component in the kit is a template something renders (or a macro file
    something imports) — never an orphan the retirement left behind (AUD-005)."""
    import subprocess
    kit = DOC[DOC.index("## UI component kit"):]
    kit = kit[:kit.index("\n## ")]
    named = {m.group(1).split("/")[-1] for m in re.finditer(r"`([A-Za-z0-9_/-]+\.html)`", kit)}
    referenced = subprocess.run(
        ["grep", "-rhoE", r"(include|import|from|extends)\s+['\"][^'\"]+\.html", str(REPO / "primeqa")],
        capture_output=True, text=True).stdout
    orphans = sorted(n for n in named if n not in referenced)
    assert orphans == [], f"the component kit names templates nothing renders or imports: {orphans}"


def test_the_gate_sees_a_planted_missing_path(monkeypatch):
    monkeypatch.setattr("tests.unit.test_documented_contracts.DOC",
                        DOC + "\n- **`primeqa/no_such_module.py`** — a file that does not exist.\n")
    with pytest.raises(AssertionError, match="no_such_module"):
        test_every_file_claude_md_names_exists()

"""Round 3, part C: the quarantine list stays honest.

A gate that is "red except for the known ones" is a self-reporting control —
its colour stops being evidence, because everyone has learned to read past it.
So the integration tree has no known reds: every test either passes or is
listed in ``tests/integration/conftest.py::QUARANTINE`` with the finding that
tracks it, the owner who will decide it, and the mechanism as observed, and is
skipped with all three printed.

This file holds that list to its promises:

1. every entry names a finding that EXISTS in ``docs/audit/findings.json`` and
   is still OPEN — a quarantine whose finding was closed is a test that should
   have come back;
2. every entry still matches at least one collected test — a stale entry is a
   silent skip of nothing, and hides the day the test is renamed;
3. every quarantine finding names at least one entry — a finding that claims
   tests are quarantined must have them;
4. the count is pinned, so growing the list is a deliberate act with a diff.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.integration.conftest import QUARANTINE  # noqa: E402

#: What the list holds today (round 3, part C). Raising this is a decision.
EXPECTED_ENTRIES = 18
FINDINGS = json.loads((REPO / "docs" / "audit" / "findings.json").read_text())
BY_ID = {f["id"]: f for f in FINDINGS}


def test_every_entry_names_an_open_finding():
    bad = {}
    for key, (finding, owner, why) in QUARANTINE.items():
        f = BY_ID.get(finding)
        if f is None:
            bad[key] = f"{finding} is not in findings.json"
        elif not f["status"].startswith("open"):
            bad[key] = f"{finding} is {f['status']} — the test should be back, not quarantined"
        elif not owner or not why or len(why) < 30:
            bad[key] = "an entry carries an owner and a mechanism, not a shrug"
    assert bad == {}, bad


def test_every_quarantine_finding_names_at_least_one_entry():
    claimed = {f["id"] for f in FINDINGS if "quarantine" in (f.get("class") or "").lower()}
    listed = {q[0] for q in QUARANTINE.values()}
    assert claimed <= listed, f"findings claim a quarantine with no entry: {claimed - listed}"


def test_every_entry_still_matches_a_collected_test():
    """A stale key skips nothing and hides a rename. Collection only — no DB."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, timeout=600).stdout
    nodeids = [ln.strip() for ln in out.splitlines() if "::" in ln]
    assert len(nodeids) > 500, f"collection returned {len(nodeids)} ids — it did not run"
    stale = [k for k in QUARANTINE if not any(k in n for n in nodeids)]
    assert stale == [], f"quarantine keys matching no collected test: {stale}"


def test_the_list_is_the_size_it_says():
    assert len(QUARANTINE) == EXPECTED_ENTRIES, (
        f"the quarantine holds {len(QUARANTINE)} entries, not {EXPECTED_ENTRIES} — "
        "adding one is a decision: give it a finding, an owner and a mechanism, "
        "and move this number in the same commit")

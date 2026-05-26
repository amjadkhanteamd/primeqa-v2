"""Unit tests for the v1-side S3 enqueue mapping (D-106.4 slice 4) — pure, no DB.

``_requirement_to_ref`` maps a v1 Requirement to the substrate's caller-supplied
``{key, text}``. (``resolve_requirement`` adds the DB fetch; ``enqueue_s3_generation``
is PG-backed → integration suite.)
"""
from __future__ import annotations

from types import SimpleNamespace

from primeqa.intelligence.s3_enqueue import _requirement_to_ref


def _req(**kw):
    base = dict(id=1, jira_key=None, jira_summary=None, jira_description=None,
                acceptance_criteria=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_jira_imported_requirement_keyed_by_jira_key():
    ref = _requirement_to_ref(_req(
        id=12, jira_key="PROJ-7", jira_summary="Reject save",
        jira_description="without a reason", acceptance_criteria="must error"))
    assert ref["key"] == "PROJ-7"
    assert ref["text"] == "Reject save\n\nwithout a reason\n\nmust error"


def test_manual_requirement_keyed_by_int_id():
    # Manual reqs have NULL jira_key -> keyed by req-<id>.
    ref = _requirement_to_ref(_req(id=9, jira_key=None, jira_summary="Only summary"))
    assert ref["key"] == "req-9"
    assert ref["text"] == "Only summary"


def test_text_drops_empty_and_whitespace_fields():
    ref = _requirement_to_ref(_req(
        id=3, jira_key="X-1", jira_summary="  ", jira_description="real body",
        acceptance_criteria=None))
    assert ref["key"] == "X-1"
    assert ref["text"] == "real body"        # blank summary + None criteria dropped


def test_all_text_empty_yields_empty_string():
    ref = _requirement_to_ref(_req(id=5, jira_key="X-2"))
    assert ref["key"] == "X-2" and ref["text"] == ""

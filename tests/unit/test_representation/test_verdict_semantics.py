"""D-465 fix slice — PASS requires positive evidence.

The acceptance set from LLD_VERDICT_SEMANTICS §e, items 1-5 and 9-10
(the DB-real re-decide, item 6, lives in the integration suite; item 7
in the transcript script).
"""
from __future__ import annotations

import inspect

import pytest

from primeqa.interpretation import ui_conformance as UC
from primeqa.interpretation.ui_conformance import (
    FAIL,
    NEEDS_HUMAN,
    NOT_DETERMINED,
    PASS,
    decide_verdict,
)

pytestmark = pytest.mark.unit

_IDS = frozenset({"image-alt"})


def _obs(*, violations=(), incomplete=None, passes_ids=None,
         inapplicable_ids=None, run_set=None):
    eo = {"violations": list(violations)}
    if incomplete is not None:
        eo["incomplete"] = list(incomplete)
    if passes_ids is not None:
        eo["passes_ids"] = list(passes_ids)
    if inapplicable_ids is not None:
        eo["inapplicable_ids"] = list(inapplicable_ids)
    if run_set is not None:
        eo["run_set"] = list(run_set)
    return {"status": "OK", "fingerprint": {"sha256": "f" * 64},
            "engine_observations": eo}


def _auto(**kw):
    base = dict(applicability="APPLICABLE", executable=True,
                capability="AUTO", rule_engine_ids=_IDS)
    base.update(kw)
    return base


# --- §e.1 a disabled/unrun bound rule is NOT a pass -------------------

def test_rule_outside_the_pinned_run_set_is_not_determined():
    v, b = decide_verdict(**_auto(), observation=_obs(
        run_set=["some-other-rule"], passes_ids=["some-other-rule"]))
    assert v == NOT_DETERMINED
    assert b["reason"] == "rule_not_executed"
    assert b["engine_ids"] == ["image-alt"]


def test_same_rule_inside_the_run_set_and_attested_passes():
    v, b = decide_verdict(**_auto(), observation=_obs(
        run_set=["image-alt"], passes_ids=["image-alt"]))
    assert v == PASS
    assert b["attested_by"] == ["image-alt"]


# --- §e.2 incomplete, both capability rows ----------------------------

def test_incomplete_under_auto_is_not_determined_with_candidates():
    inc = [{"id": "image-alt", "nodes": [{"html": "<img>"}]}]
    v, b = decide_verdict(**_auto(), observation=_obs(
        incomplete=inc, run_set=["image-alt"], passes_ids=["image-alt"]))
    assert v == NOT_DETERMINED
    assert b["reason"] == "engine_incomplete"
    assert b["candidates"] == inc
    # even WITH a pass attestation, incomplete wins — the engine said it
    # could not determine this rule on this surface.


def test_same_observation_under_human_with_candidate_needs_human():
    inc = [{"id": "image-alt", "nodes": [{"html": "<img>"}]}]
    v, b = decide_verdict(**_auto(capability="HUMAN_WITH_CANDIDATE"),
                          observation=_obs(incomplete=inc))
    assert v == NEEDS_HUMAN
    assert b["candidates"] == inc


# --- §e.3 inapplicable is not a pass ----------------------------------

def test_inapplicable_is_not_determined_never_pass():
    v, b = decide_verdict(**_auto(), observation=_obs(
        run_set=["image-alt"], passes_ids=[],
        inapplicable_ids=["image-alt"]))
    assert v == NOT_DETERMINED
    assert b["reason"] == "rule_inapplicable"


# --- §e.5 in the run set but unattested -------------------------------

def test_in_run_set_but_unattested():
    v, b = decide_verdict(**_auto(), observation=_obs(
        run_set=["image-alt"], passes_ids=[]))
    assert v == NOT_DETERMINED
    assert b["reason"] == "rule_unattested"


# --- legacy + FAIL survival -------------------------------------------

def test_legacy_observation_is_never_a_retroactive_pass():
    v, b = decide_verdict(**_auto(), observation=_obs())
    assert v == NOT_DETERMINED
    assert b["reason"] == "legacy_unattested"


def test_fail_survives_the_change_and_needs_no_attestation():
    v, b = decide_verdict(**_auto(), observation=_obs(violations=[
        {"id": "image-alt", "nodes": [{"html": "<img>", "target": ["img"]}]}]))
    assert v == FAIL          # a violation IS positive evidence
    assert b["engine_ids"] == ["image-alt"]


# --- §e.9 the evidence law is stated in the module --------------------

def test_evidence_law_is_stated_in_the_module_docstring():
    doc = UC.__doc__ or ""
    assert "EVIDENCE LAW" in doc
    assert "Offline analysis" in doc and "not evidence" in doc
    assert "legacy_unattested" in doc
    # and the acquit half of the arm-H posture
    assert "acquit" in doc.lower()


def test_every_not_determined_reason_is_named():
    """No unnamed NOT_DETERMINED: each path carries a reason string."""
    src = inspect.getsource(UC._decide_non_violation)
    for reason in ("engine_incomplete", "legacy_unattested",
                   "rule_not_executed", "rule_inapplicable",
                   "rule_unattested"):
        assert f'"{reason}"' in src

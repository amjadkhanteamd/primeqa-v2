"""Unit: the async consumer's result-shape handling (D-284, Slice 4e) — the crash
fix. The consumer + the sync console's _map_run_result both used to assume the
single :class:`RunPathResult` shape (``.evidence``); a run-all :class:`RunAllResult`
(``.probes`` / ``.batch_id``, NO ``.evidence``) would crash. These prove the single
shape stays byte-identical and a RunAllResult is handled without crashing and
without computing Verified. Pure, no DB.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

from primeqa.execution_engine.consumer import _async_log_outcome
from primeqa.intelligence.s4_execution_console import _map_run_result


def _single(outcome="passed", *, ran=True, reason=None):
    # a RunPathResult-shaped stand-in (the single path).
    return SimpleNamespace(
        ran=ran, reason=reason,
        evidence=SimpleNamespace(outcome=outcome) if ran else None,
        interpretation=None, selected_recipe_id=None)


def _runall(batch_id="b-1", n=3):
    # a RunAllResult-shaped stand-in: has batch_id + probes, NO .evidence.
    return SimpleNamespace(ran=True, batch_id=batch_id,
                           probes=tuple(range(n)), reason=None)


# --- _async_log_outcome (consumer line) --------------------------------------

def test_async_log_outcome_single_is_byte_identical():
    # the EXACT prior expression for a single result.
    assert _async_log_outcome(_single("passed")) == "passed"
    assert _async_log_outcome(_single("errored")) == "errored"
    assert _async_log_outcome(_single(ran=False, reason="no_eligible_recipe")) \
        == "no_eligible_recipe"
    assert _async_log_outcome(_single(ran=False, reason=None)) == "ran"


def test_old_direct_evidence_read_would_crash_on_runall():
    # documents the bug 4e fixes: the old consumer line did
    # ``result.evidence.outcome`` — a RunAllResult has no .evidence.
    with pytest.raises(AttributeError):
        _ = _runall().evidence.outcome


def test_async_log_outcome_runall_does_not_crash_and_reports_batch():
    # THE crash regression: a RunAllResult must NOT crash, must NOT assume
    # .evidence, must report the batch, and must NOT compute Verified.
    out = _async_log_outcome(_runall("batch-xyz", 3))
    assert "batch-xyz" in out and "3" in out          # batch + probe count


# --- _map_run_result (sync console) ------------------------------------------

def test_map_run_result_single_unchanged():
    d = _map_run_result(_single("failed"))
    assert d["ok"] is True and d["ran"] is True
    assert d["outcome"] == "failed"
    assert "batch_id" not in d                         # single shape, no batch key


def test_map_run_result_no_run_unchanged():
    d = _map_run_result(SimpleNamespace(ran=False, reason="no_eligible_recipe"))
    assert d == {"ok": True, "ran": False, "reason": "no_eligible_recipe"}


def test_map_run_result_runall_reports_batch_no_verified():
    d = _map_run_result(_runall("b-42", 2))
    assert d["ok"] is True and d["ran"] is True
    assert d["batch_id"] == "b-42" and d["probe_count"] == 2
    # Verified is NOT computed here (the decision engine reads the batch, 4d).
    assert d["outcome"] is None and d["verdict"] is None and d["recipe_id"] is None

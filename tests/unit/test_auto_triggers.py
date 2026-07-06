"""Unit: the D-199 automated execution triggers. Pure — enqueue + env policy
stubbed via monkeypatch; asserts the orchestration of each trigger."""
from __future__ import annotations

import types

import pytest

import primeqa.execution_engine.intake as intake
import primeqa.intelligence.s4_execution_console as console

pytestmark = pytest.mark.unit


@pytest.fixture
def recorded(monkeypatch):
    """Stub enqueue_s4_execution at its source module (every trigger imports it
    function-locally, so the source attr is the one seam)."""
    calls = []

    def _fake_enqueue(*, tenant_id, test_id, environment_id, created_by=None):
        calls.append({"tenant_id": tenant_id, "test_id": test_id,
                      "environment_id": environment_id})
        return types.SimpleNamespace(id=len(calls), status="queued")
    monkeypatch.setattr(intake, "enqueue_s4_execution", _fake_enqueue)
    return calls


# --- trigger 1: auto-enqueue on approval — REMOVED (AK 2026-07-07) ------------
# Approval is decision-only: the fan-out ran every approval on every
# sandbox-flagged env (including a mis-flagged real production org). This test
# pins the removal so the trigger cannot silently return.

def test_approval_no_longer_auto_enqueues():
    assert not hasattr(console, "auto_enqueue_on_approval")
    assert not hasattr(console, "auto_verify_environment_ids")


# --- trigger 2: scheduled re-verification ------------------------------------

# (fire_substrate_schedule retired with the v1 scheduled_runs store, D-221 R3 —
#  per-claim scheduling returns as an s4_run_schedules extension when needed.)


def test_enqueue_claims_for_keys_empty_keys_short_circuits(recorded):
    out = console.enqueue_claims_for_keys(1, [], 59)
    assert out == {"enqueued": [], "claim_count": 0}
    assert recorded == []


def test_enqueue_claims_for_keys_best_effort_bad_tenant(recorded):
    out = console.enqueue_claims_for_keys(-1, ["X-1"], 59)
    assert out == {"enqueued": [], "claim_count": 0}       # never raises
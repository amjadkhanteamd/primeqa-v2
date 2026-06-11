"""Unit: the D-199 automated execution triggers. Pure — enqueue + env policy
stubbed via monkeypatch; asserts the orchestration of each trigger."""
from __future__ import annotations

import types
from uuid import uuid4

import pytest

import primeqa.db as db_mod
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


# --- trigger 1: auto-enqueue on approval -------------------------------------

def test_auto_enqueue_fans_out_to_all_auto_verify_envs(monkeypatch, recorded):
    monkeypatch.setattr(db_mod, "get_db", lambda: iter([types.SimpleNamespace(
        close=lambda: None)]))
    monkeypatch.setattr(console, "auto_verify_environment_ids",
                        lambda db, tid: [7, 9])
    tid = uuid4()
    out = console.auto_enqueue_on_approval(1, tid)
    assert out["environments"] == [7, 9]
    assert len(out["enqueued"]) == 2
    assert [c["environment_id"] for c in recorded] == [7, 9]
    assert all(c["test_id"] == tid for c in recorded)


def test_auto_enqueue_one_env_failure_never_blocks_the_rest(monkeypatch, recorded):
    monkeypatch.setattr(db_mod, "get_db", lambda: iter([types.SimpleNamespace(
        close=lambda: None)]))
    monkeypatch.setattr(console, "auto_verify_environment_ids",
                        lambda db, tid: [7, 9])
    real = intake.enqueue_s4_execution

    def _flaky(**kw):
        if kw["environment_id"] == 7:
            raise RuntimeError("boom")
        return real(**kw)
    monkeypatch.setattr(intake, "enqueue_s4_execution", _flaky)
    out = console.auto_enqueue_on_approval(1, uuid4())
    assert len(out["enqueued"]) == 1                       # env 9 still enqueued


def test_auto_enqueue_never_raises(monkeypatch):
    monkeypatch.setattr(db_mod, "get_db",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    out = console.auto_enqueue_on_approval(1, uuid4())
    assert out == {"enqueued": [], "environments": []}


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
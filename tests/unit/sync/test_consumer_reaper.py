"""Unit: run_s1_sync_reaper_tick wires BOTH reaps with per-reap isolation.

The reaper tick now does two things per tenant: fail stale s1_sync_jobs AND
finalize stranded sync_runs (structurally complete but never finalized). These
tests pin the wiring + the isolation (one reap failing never skips the other)
without a DB — SyncJobStore, get_tenant_connection, and the readiness reaper are
mocked.
"""
from __future__ import annotations

from unittest import mock

import pytest

from primeqa.sync import consumer

pytestmark = pytest.mark.unit


def _store(*, jobs_reaped=0, jobs_raises=False):
    store = mock.MagicMock()
    if jobs_raises:
        store.reap_stale_jobs.side_effect = RuntimeError("boom")
    else:
        store.reap_stale_jobs.return_value = jobs_reaped
    return store


def _patched(store, *, runs_return=0, runs_raises=False):
    runs = mock.patch(
        "primeqa.sync.readiness.reap_stranded_sync_runs",
        side_effect=(RuntimeError("db down") if runs_raises else None),
        return_value=(None if runs_raises else runs_return))
    return (
        mock.patch.object(consumer, "SyncJobStore", return_value=store),
        mock.patch.object(consumer, "get_tenant_connection"),
        runs,
    )


class TestRunS1SyncReaperTick:
    def test_runs_both_reaps_and_sums(self) -> None:
        store = _store(jobs_reaped=2)
        p_store, p_conn, p_runs = _patched(store, runs_return=3)
        with p_store, p_conn, p_runs as mock_runs:
            out = consumer.run_s1_sync_reaper_tick([1])
        assert out == {1: 5}                          # 2 jobs + 3 runs
        store.reap_stale_jobs.assert_called_once()
        mock_runs.assert_called_once()
        # the stranded-run reaper receives the structural final phase + a window
        assert mock_runs.call_args.kwargs["final_phase"] == "LightningComponentBundle"  # 3A-5: the structural final phase (D-308.1 sentinel = ENTITY_ORDER[-1])
        assert mock_runs.call_args.kwargs["stale_minutes"] == 360

    def test_job_reap_failure_does_not_skip_run_reap(self) -> None:
        store = _store(jobs_raises=True)
        p_store, p_conn, p_runs = _patched(store, runs_return=1)
        with p_store, p_conn, p_runs as mock_runs:
            out = consumer.run_s1_sync_reaper_tick([1])
        mock_runs.assert_called_once()                # ran despite job-reap raise
        assert out == {1: 1}

    def test_run_reap_failure_isolated(self) -> None:
        store = _store(jobs_reaped=4)
        p_store, p_conn, p_runs = _patched(store, runs_raises=True)
        with p_store, p_conn, p_runs:
            out = consumer.run_s1_sync_reaper_tick([1])
        assert out == {1: 4}                          # job count kept; run-reap isolated

    def test_custom_run_stale_minutes_flows_through(self) -> None:
        store = _store()
        p_store, p_conn, p_runs = _patched(store, runs_return=0)
        with p_store, p_conn, p_runs as mock_runs:
            consumer.run_s1_sync_reaper_tick([1], run_stale_minutes=1440)
        assert mock_runs.call_args.kwargs["stale_minutes"] == 1440

"""Unit pins for the D-438 post-sync drift hook.

THE test that matters: a detector raising inside the hook cannot fail a sync
that succeeded — proven at the consumer level (the sync job still completes)
and at the hook level (the hook never raises, and the failure is LOUD via the
S1-DRIFT-HOOK-FAILURE marker, never silent). Plus: never-reviewed vs
reviewed-at-0 are distinguishable; the hook never writes the watermark;
the emission obeys the compactness contract.
"""
from __future__ import annotations

import contextlib
import logging
from unittest import mock

import pytest

from primeqa.sync import consumer, drift_hook
from primeqa.sync.drift_hook import (
    FAILURE_MARKER, format_drift_lines, read_watermark,
    run_post_sync_drift_hook, since_seq_for)
from primeqa.semantic.metadata_drift import DriftEvent

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _RecordingConn:
    """Records every executed statement; serves the watermark read."""

    def __init__(self, watermark_row=None):
        self.statements: list[str] = []
        self._watermark_row = watermark_row

    def execute(self, clause, params=None):
        self.statements.append(" ".join(str(clause).split()))
        return _FakeResult(self._watermark_row)


def _connect_factory(conn):
    @contextlib.contextmanager
    def _connect():
        yield conn
    return _connect


def _event(seq=66, kind="ACTIVATION", rule="Opportunity.R",
           before="active", after="inactive", at="2026-06-15T08:28:57"):
    return DriftEvent(seq=seq, at=at, rule=rule, kind=kind,
                      before=before, after=after)


# ---------------------------------------------------------------------------
# THE test: a detector failure cannot fail a sync
# ---------------------------------------------------------------------------

class _StubJob:
    id = 7
    environment_id = 59
    connected_org_id = "902850e3-89c0-4d74-9141-66084045f439"
    last_sync_run_id = None
    status = "running"


def _stub_store():
    store = mock.MagicMock()
    store.claim_next_queued_job.return_value = _StubJob()
    store.heartbeat.return_value = True
    store.get_job.return_value = _StubJob()
    return store


def test_detector_raising_inside_hook_cannot_fail_the_sync(caplog):
    """The sync completes (store.complete called, job id returned, nothing
    raised) even when the drift detector explodes — and the failure is LOUD
    (the marker appears), never silent."""
    store = _stub_store()
    engine = mock.MagicMock()
    engine.run_sync.return_value = "run-1"
    with mock.patch.object(consumer, "SyncJobStore", return_value=store), \
         mock.patch.object(consumer, "get_engine",
                           return_value=mock.MagicMock()), \
         mock.patch.object(consumer, "_read_sync_run_outcome",
                           return_value={"status": "success",
                                         "error_message": None}), \
         mock.patch.object(drift_hook, "collect_drift_events",
                           side_effect=RuntimeError("detector exploded")), \
         caplog.at_level(logging.DEBUG):
        out = consumer.process_sync_job_for_tenant(
            1, sf_client_resolver=lambda t, e: mock.MagicMock(),
            engine_factory=lambda db, sf, schema: engine)
    assert out == 7                                   # the job id — no raise
    store.complete.assert_called_once_with(7)         # the sync COMPLETED
    store.fail.assert_not_called()                    # and was never failed
    assert FAILURE_MARKER in caplog.text              # and the failure is loud


def test_hook_itself_never_raises_and_logs_the_marker(caplog):
    conn = _RecordingConn(watermark_row=None)
    with mock.patch.object(drift_hook, "collect_drift_events",
                           side_effect=RuntimeError("boom")), \
         caplog.at_level(logging.DEBUG):
        run_post_sync_drift_hook(1, "org-1", "run-1", "success",
                                 _connect=_connect_factory(conn))
    assert FAILURE_MARKER in caplog.text
    assert "unaffected" in caplog.text


def test_hook_skips_non_success_statuses(caplog):
    conn = _RecordingConn()
    with caplog.at_level(logging.DEBUG):
        run_post_sync_drift_hook(1, "org-1", "run-1", "running",
                                 _connect=_connect_factory(conn))
    assert conn.statements == []                      # no DB touch at all


# ---------------------------------------------------------------------------
# Watermark semantics
# ---------------------------------------------------------------------------

def test_never_reviewed_and_reviewed_at_zero_are_distinguishable():
    assert read_watermark(_RecordingConn(watermark_row=None), "o") is None
    assert read_watermark(_RecordingConn(watermark_row=(0,)), "o") == 0
    # and they drive DIFFERENT detector windows:
    assert since_seq_for(None) is None                # full backlog
    assert since_seq_for(0) == 1                      # events after seq 0


def test_hook_does_not_advance_the_watermark():
    """The hook's connection issues ZERO writes — the watermark advances only
    via the explicit CLI review command (D-438)."""
    conn = _RecordingConn(watermark_row=(100,))
    with mock.patch.object(drift_hook, "collect_drift_events",
                           return_value=([_event()], {"vr": 1})), \
         mock.patch("primeqa.shared.notifications.notify_metadata_drift"):
        run_post_sync_drift_hook(1, "org-1", "run-1", "success",
                                 _connect=_connect_factory(conn))
    writes = [s for s in conn.statements
              if any(w in s.upper()
                     for w in ("INSERT", "UPDATE", "DELETE", "UPSERT"))]
    assert writes == []


# ---------------------------------------------------------------------------
# Emission compactness contract
# ---------------------------------------------------------------------------

def test_zero_events_is_one_quiet_line():
    lines = format_drift_lines(
        [], watermark=162, org_id="902850e3-89c0", sync_run_id="r1",
        sync_status="success", counts_by_type={"vr": 0})
    assert len(lines) == 1
    assert "no unreviewed drift events" in lines[0]
    assert "watermark=seq 162" in lines[0]


def test_handful_lists_each_event_once():
    events = [_event(seq=66),
              _event(seq=120, kind="VERSION_MOVED",
                     rule="HL_Auto_Submit_Approval", before="1", after="2")]
    lines = format_drift_lines(
        events, watermark=None, org_id="902850e3-89c0", sync_run_id="r1",
        sync_status="success", counts_by_type={"vr": 1, "flow": 1})
    assert "2 UNREVIEWED" in lines[0]
    assert "watermark=never-reviewed" in lines[0]
    assert any("[ACTIVATION] seq 66" in ln for ln in lines)
    assert any("[VERSION_MOVED] seq 120" in ln for ln in lines)


def test_large_backlog_caps_at_five_lines_plus_pointer():
    events = [_event(seq=100 + i, rule=f"R{i}") for i in range(40)]
    lines = format_drift_lines(
        events, watermark=None, org_id="o", sync_run_id="r1",
        sync_status="success", counts_by_type={"vr": 40})
    event_lines = [ln for ln in lines if "] seq " in ln]
    assert len(event_lines) == 5                      # never a wall of text
    assert any("+35 more" in ln for ln in lines)


def test_partial_sync_is_labelled():
    lines = format_drift_lines(
        [_event()], watermark=10, org_id="o", sync_run_id="r1",
        sync_status="partial_success", counts_by_type={"vr": 1})
    assert any("PARTIAL sync" in ln and "undetected" in ln for ln in lines)

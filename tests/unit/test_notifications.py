"""Unit: notification dispatch + the D-234 substrate run-failure notify + its
consumer hook. Mock-patched, no DB, no network.

Covers (1) send_email provider selection + best-effort never-raise; (2)
notify_substrate_run_failed (recipients/body, quarantine skip, no-recipients
no-op, never-raises); (3) the execution-consumer hook (fires on failed/errored,
skips on passed, and a notify problem can NEVER fail an already-completed job).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest

from primeqa.shared import notifications as N

pytestmark = pytest.mark.unit


# ---- send_email provider dispatch -----------------------------------------

def test_send_email_log_default_returns_true(monkeypatch):
    monkeypatch.delenv("NOTIFICATIONS_PROVIDER", raising=False)
    assert N.send_email(N.Notification(
        kind="x", subject="s", body="b", recipients=["a@x.io"])) is True


def test_send_email_empty_recipients_is_noop():
    assert N.send_email(N.Notification(
        kind="x", subject="s", body="b", recipients=[])) is False


def test_send_email_unknown_provider_returns_false(monkeypatch):
    monkeypatch.setenv("NOTIFICATIONS_PROVIDER", "carrier-pigeon")
    assert N.send_email(N.Notification(
        kind="x", subject="s", body="b", recipients=["a@x.io"])) is False


def test_send_email_never_raises(monkeypatch):
    # a provider that blows up must be swallowed to False, not propagated
    monkeypatch.setenv("NOTIFICATIONS_PROVIDER", "smtp")
    with mock.patch.object(N, "_send_smtp", side_effect=RuntimeError("boom")):
        assert N.send_email(N.Notification(
            kind="x", subject="s", body="b", recipients=["a@x.io"])) is False


# ---- notify_substrate_run_failed ------------------------------------------

def _fake_db():
    # next(get_db()) yields this; the notify closes it.
    return iter([SimpleNamespace(close=lambda: None)])


def test_notify_resolves_recipients_and_sends(monkeypatch):
    rid, tid = uuid4(), uuid4()
    with mock.patch("primeqa.intelligence.quarantine.is_quarantined",
                    return_value=False), \
         mock.patch("primeqa.db.get_db", return_value=_fake_db()), \
         mock.patch.object(N, "_admin_emails", return_value=["admin@x.io"]), \
         mock.patch.object(N, "send_email") as send:
        N.notify_substrate_run_failed(
            7, run_id=str(rid), test_id=str(tid), environment_id=59,
            outcome="failed", error_message="VALIDATION_RULE")
    send.assert_called_once()
    note = send.call_args.args[0]
    assert note.kind == "substrate_run_failed"
    assert note.recipients == ["admin@x.io"]
    assert "failed" in note.subject and str(rid)[:8] in note.subject
    assert "VALIDATION_RULE" in note.body and f"/runs/{rid}" in note.body
    assert note.extras["outcome"] == "failed"


def test_notify_skips_quarantined_claim(monkeypatch):
    with mock.patch("primeqa.intelligence.quarantine.is_quarantined",
                    return_value=True), \
         mock.patch.object(N, "send_email") as send:
        N.notify_substrate_run_failed(
            7, run_id=str(uuid4()), test_id=str(uuid4()), environment_id=59,
            outcome="failed")
    send.assert_not_called()


def test_notify_no_recipients_is_noop(monkeypatch):
    with mock.patch("primeqa.intelligence.quarantine.is_quarantined",
                    return_value=False), \
         mock.patch("primeqa.db.get_db", return_value=_fake_db()), \
         mock.patch.object(N, "_admin_emails", return_value=[]), \
         mock.patch.object(N, "send_email") as send:
        N.notify_substrate_run_failed(
            7, run_id=str(uuid4()), test_id=str(uuid4()), environment_id=59,
            outcome="errored")
    send.assert_not_called()


def test_notify_never_raises_on_db_error(monkeypatch):
    with mock.patch("primeqa.intelligence.quarantine.is_quarantined",
                    return_value=False), \
         mock.patch("primeqa.db.get_db", side_effect=RuntimeError("db down")), \
         mock.patch.object(N, "send_email") as send:
        # must not raise
        N.notify_substrate_run_failed(
            7, run_id=str(uuid4()), test_id=str(uuid4()), environment_id=59,
            outcome="failed")
    send.assert_not_called()


# ---- the execution-consumer hook ------------------------------------------

from primeqa.execution_engine import consumer as C


def _store_mock(job):
    store = mock.MagicMock()
    store.claim_next_queued_job.return_value = job
    return store


def _job():
    return SimpleNamespace(id=42, test_id=str(uuid4()), environment_id=59)


def _result(outcome, *, ran=True, err=None):
    ev = SimpleNamespace(outcome=outcome, run_id=uuid4(), claim_test_id=uuid4(),
                         environment_id=59,
                         error=(SimpleNamespace(message=err) if err else None))
    return SimpleNamespace(ran=ran, evidence=ev, reason=None)


def test_consumer_notifies_on_failed():
    job = _job()
    with mock.patch.object(C, "ExecutionJobStore", return_value=_store_mock(job)) as S, \
         mock.patch("primeqa.shared.notifications.notify_substrate_run_failed") as notify:
        out = C.process_execution_job_for_tenant(
            7, run_fn=lambda *a, **k: _result("failed", err="boom"))
    assert out == 42
    S.return_value.complete.assert_called_once_with(42)
    S.return_value.fail.assert_not_called()
    notify.assert_called_once()
    assert notify.call_args.kwargs["outcome"] == "failed"
    assert notify.call_args.kwargs["error_message"] == "boom"


def test_consumer_notifies_on_errored():
    job = _job()
    with mock.patch.object(C, "ExecutionJobStore", return_value=_store_mock(job)), \
         mock.patch("primeqa.shared.notifications.notify_substrate_run_failed") as notify:
        C.process_execution_job_for_tenant(
            7, run_fn=lambda *a, **k: _result("errored"))
    notify.assert_called_once()
    assert notify.call_args.kwargs["outcome"] == "errored"


def test_consumer_no_notify_on_passed():
    job = _job()
    with mock.patch.object(C, "ExecutionJobStore", return_value=_store_mock(job)), \
         mock.patch("primeqa.shared.notifications.notify_substrate_run_failed") as notify:
        C.process_execution_job_for_tenant(
            7, run_fn=lambda *a, **k: _result("passed"))
    notify.assert_not_called()


def test_consumer_notify_failure_cannot_fail_the_job():
    # a notify that raises must NOT route to store.fail (the job is already done)
    job = _job()
    store = _store_mock(job)
    with mock.patch.object(C, "ExecutionJobStore", return_value=store), \
         mock.patch("primeqa.shared.notifications.notify_substrate_run_failed",
                    side_effect=RuntimeError("smtp exploded")):
        out = C.process_execution_job_for_tenant(
            7, run_fn=lambda *a, **k: _result("failed"))
    assert out == 42
    store.complete.assert_called_once_with(42)
    store.fail.assert_not_called()


def test_consumer_no_notify_when_run_raises():
    job = _job()
    store = _store_mock(job)
    def boom(*a, **k):
        raise RuntimeError("run blew up")
    with mock.patch.object(C, "ExecutionJobStore", return_value=store), \
         mock.patch("primeqa.shared.notifications.notify_substrate_run_failed") as notify:
        C.process_execution_job_for_tenant(7, run_fn=boom)
    store.fail.assert_called_once()
    notify.assert_not_called()

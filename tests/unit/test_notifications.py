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


# ---- D-288: notify_substrate_batch_failed (the batch-shaped sibling) -------

def _batch_result(outcomes, *, ran=True, batch_id=None):
    """A run-all RunAllResult-shape: has .batch_id + .probes, NO .evidence (the
    single-vs-batch discriminator is hasattr(result, 'batch_id'))."""
    probes = [SimpleNamespace(recipe_id=uuid4(), run_id=uuid4(), outcome=o)
              for o in outcomes]
    return SimpleNamespace(ran=ran, batch_id=(batch_id or uuid4()),
                           probes=probes, reason=None)


def test_batch_notify_resolves_recipients_and_sends():
    bid, tid = uuid4(), uuid4()
    r1, r2 = uuid4(), uuid4()
    with mock.patch("primeqa.intelligence.quarantine.is_quarantined",
                    return_value=False), \
         mock.patch("primeqa.db.get_db", return_value=_fake_db()), \
         mock.patch.object(N, "_admin_emails", return_value=["admin@x.io"]), \
         mock.patch.object(N, "send_email") as send:
        N.notify_substrate_batch_failed(
            7, batch_id=str(bid), test_id=str(tid), environment_id=59,
            failed_count=2, run_ids=[str(r1), str(r2)])
    send.assert_called_once()
    note = send.call_args.args[0]
    assert note.kind == "substrate_batch_failed"
    assert note.recipients == ["admin@x.io"]
    assert "2" in note.subject and str(bid)[:8] in note.subject
    assert str(bid) in note.body and str(r1) in note.body and str(r2) in note.body
    assert f"/claims/{tid}" in note.body
    assert note.extras["failed_count"] == 2
    assert note.extras["run_ids"] == [str(r1), str(r2)]


def test_batch_notify_skips_quarantined_claim():
    with mock.patch("primeqa.intelligence.quarantine.is_quarantined",
                    return_value=True), \
         mock.patch.object(N, "send_email") as send:
        N.notify_substrate_batch_failed(
            7, batch_id=str(uuid4()), test_id=str(uuid4()), environment_id=59,
            failed_count=1, run_ids=[str(uuid4())])
    send.assert_not_called()


def test_batch_notify_no_recipients_is_noop():
    with mock.patch("primeqa.intelligence.quarantine.is_quarantined",
                    return_value=False), \
         mock.patch("primeqa.db.get_db", return_value=_fake_db()), \
         mock.patch.object(N, "_admin_emails", return_value=[]), \
         mock.patch.object(N, "send_email") as send:
        N.notify_substrate_batch_failed(
            7, batch_id=str(uuid4()), test_id=str(uuid4()), environment_id=59,
            failed_count=1, run_ids=[str(uuid4())])
    send.assert_not_called()


def test_batch_notify_never_raises_on_db_error():
    with mock.patch("primeqa.intelligence.quarantine.is_quarantined",
                    return_value=False), \
         mock.patch("primeqa.db.get_db", side_effect=RuntimeError("db down")), \
         mock.patch.object(N, "send_email") as send:
        N.notify_substrate_batch_failed(           # must not raise
            7, batch_id=str(uuid4()), test_id=str(uuid4()), environment_id=59,
            failed_count=1, run_ids=[str(uuid4())])
    send.assert_not_called()


# ---- D-288: the consumer hook's batch-vs-single discriminator -------------

def test_consumer_batch_notifies_on_failed_probe():
    job = _job()
    bid = uuid4()
    with mock.patch.object(C, "ExecutionJobStore", return_value=_store_mock(job)) as S, \
         mock.patch("primeqa.shared.notifications.notify_substrate_batch_failed") as bnotify, \
         mock.patch("primeqa.shared.notifications.notify_substrate_run_failed") as snotify:
        out = C.process_execution_job_for_tenant(
            7, run_fn=lambda *a, **k: _batch_result(
                ["passed", "failed", "passed"], batch_id=bid))
    assert out == 42
    S.return_value.complete.assert_called_once_with(42)
    S.return_value.fail.assert_not_called()
    bnotify.assert_called_once()                   # batch sibling fired
    snotify.assert_not_called()                    # single notifier did NOT
    kw = bnotify.call_args.kwargs
    assert kw["batch_id"] == str(bid)
    assert kw["test_id"] == str(job.test_id)       # from the job, not a probe
    assert kw["environment_id"] == 59              # from the job
    assert kw["failed_count"] == 1
    assert len(kw["run_ids"]) == 1


def test_consumer_batch_notifies_on_errored_probe():
    job = _job()
    with mock.patch.object(C, "ExecutionJobStore", return_value=_store_mock(job)), \
         mock.patch("primeqa.shared.notifications.notify_substrate_batch_failed") as bnotify:
        C.process_execution_job_for_tenant(
            7, run_fn=lambda *a, **k: _batch_result(["passed", "errored"]))
    bnotify.assert_called_once()
    assert bnotify.call_args.kwargs["failed_count"] == 1


def test_consumer_batch_no_notify_when_all_probes_passed():
    job = _job()
    with mock.patch.object(C, "ExecutionJobStore", return_value=_store_mock(job)), \
         mock.patch("primeqa.shared.notifications.notify_substrate_batch_failed") as bnotify, \
         mock.patch("primeqa.shared.notifications.notify_substrate_run_failed") as snotify:
        C.process_execution_job_for_tenant(
            7, run_fn=lambda *a, **k: _batch_result(["passed", "passed"]))
    bnotify.assert_not_called()
    snotify.assert_not_called()


def test_consumer_batch_no_notify_when_not_ran():
    job = _job()
    with mock.patch.object(C, "ExecutionJobStore", return_value=_store_mock(job)), \
         mock.patch("primeqa.shared.notifications.notify_substrate_batch_failed") as bnotify:
        C.process_execution_job_for_tenant(
            7, run_fn=lambda *a, **k: _batch_result(["failed"], ran=False))
    bnotify.assert_not_called()


def test_consumer_single_does_not_route_to_batch_notify():
    # the discriminator must not misroute a single RunPathResult to the batch sibling
    job = _job()
    with mock.patch.object(C, "ExecutionJobStore", return_value=_store_mock(job)), \
         mock.patch("primeqa.shared.notifications.notify_substrate_run_failed") as snotify, \
         mock.patch("primeqa.shared.notifications.notify_substrate_batch_failed") as bnotify:
        C.process_execution_job_for_tenant(
            7, run_fn=lambda *a, **k: _result("failed", err="boom"))
    snotify.assert_called_once()
    bnotify.assert_not_called()

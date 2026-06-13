"""Unit: the F6.1 cleanup spine — CreatedRecordTracker reverse-order teardown
(D-196). Pure (no SF/DB); the delete is an injected stub."""
import pytest

from primeqa.execution_engine.evidence import CleanupRecord
from primeqa.execution_engine.provisioning import (
    CreatedRecord, CreatedRecordTracker,
)

pytestmark = pytest.mark.unit


def _stub_delete(calls, *, succeed=True):
    """A delete_fn that records its call order and returns a CleanupRecord."""
    def fn(client, sobject, record_id):
        calls.append((sobject, record_id))
        return CleanupRecord(attempted=True, succeeded=succeed, record_id=record_id)
    return fn


class TestCreatedRecordTracker:
    def test_records_in_create_order_with_sequence(self):
        t = CreatedRecordTracker()
        t.record("Account", "001A")
        t.record("Contact", "003C")
        assert t.records == (
            CreatedRecord("Account", "001A", 0),
            CreatedRecord("Contact", "003C", 1),
        )

    def test_teardown_reverse_order_children_before_parents(self):
        # parent created first, child second → child deleted first (reverse).
        t = CreatedRecordTracker()
        t.record("Account", "001A")
        t.record("Contact", "003C")
        calls = []
        cleanups = t.teardown(client=object(), delete_fn=_stub_delete(calls))
        assert calls == [("Contact", "003C"), ("Account", "001A")]
        assert len(cleanups) == 2 and all(c.succeeded for c in cleanups)

    def test_single_record_teardown_matches_legacy_behaviour(self):
        # The F6.1-neutral case: one tracked record → one delete → index 0 is it.
        t = CreatedRecordTracker()
        t.record("Account", "001A")
        calls = []
        cleanups = t.teardown(object(), _stub_delete(calls))
        assert calls == [("Account", "001A")]
        assert cleanups[0].record_id == "001A" and cleanups[0].succeeded

    def test_empty_tracker_teardown_is_noop(self):
        t = CreatedRecordTracker()
        calls = []
        assert t.teardown(object(), _stub_delete(calls)) == ()
        assert calls == []

    def test_teardown_propagates_delete_failures_without_raising(self):
        # delete_fn owns the best-effort contract; a recorded failure flows back.
        t = CreatedRecordTracker()
        t.record("Account", "001A")
        cleanups = t.teardown(object(), _stub_delete([], succeed=False))
        assert cleanups[0].succeeded is False


class _FakeSink:
    """Records the sink contract calls (D-230) — created(run_id, ...) on record,
    cleaned(run_id, ...) on a successful teardown delete."""
    def __init__(self):
        self.created_calls = []
        self.cleaned_calls = []

    def created(self, run_id, sobject, record_id, created_seq):
        self.created_calls.append((run_id, sobject, record_id, created_seq))

    def cleaned(self, run_id, record_id):
        self.cleaned_calls.append((run_id, record_id))


class TestTrackerSinkWriteAhead:
    """D-230: the write-ahead durability contract through CreatedRecordTracker."""

    def test_record_write_aheads_to_the_sink(self):
        sink = _FakeSink()
        t = CreatedRecordTracker(run_id="RUN1", sink=sink)
        t.record("Account", "001A")
        t.record("Contact", "003C")
        # written the moment each is tracked (before any later SF call)
        assert sink.created_calls == [
            ("RUN1", "Account", "001A", 0),
            ("RUN1", "Contact", "003C", 1),
        ]

    def test_successful_teardown_marks_cleaned(self):
        sink = _FakeSink()
        t = CreatedRecordTracker(run_id="RUN1", sink=sink)
        t.record("Account", "001A")
        t.record("Contact", "003C")
        t.teardown(client=object(), delete_fn=_stub_delete([], succeed=True))
        # reverse order, only successful deletes flip cleaned
        assert sink.cleaned_calls == [("RUN1", "003C"), ("RUN1", "001A")]

    def test_failed_teardown_does_not_mark_cleaned(self):
        # A delete that did not succeed leaves the row cleaned=false so the reaper
        # reclaims it — durability's whole point.
        sink = _FakeSink()
        t = CreatedRecordTracker(run_id="RUN1", sink=sink)
        t.record("Account", "001A")
        t.teardown(client=object(), delete_fn=_stub_delete([], succeed=False))
        assert sink.cleaned_calls == []

    def test_no_sink_is_a_silent_no_op(self):
        # Back-compat: the sink-less path (tests / not-yet-async) behaves exactly
        # as before — record + teardown work, nothing is written.
        t = CreatedRecordTracker()          # no run_id, no sink
        t.record("Account", "001A")
        cleanups = t.teardown(client=object(), delete_fn=_stub_delete([]))
        assert len(cleanups) == 1 and cleanups[0].succeeded

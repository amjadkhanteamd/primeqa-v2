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

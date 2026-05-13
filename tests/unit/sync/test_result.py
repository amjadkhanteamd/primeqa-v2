"""Unit tests for PhaseResult dataclass.

Covers the counter fields + edges_skipped_by_type observability
counter added in the Profile cycle (corrections-log §19).
"""
from __future__ import annotations

import pytest

from primeqa.sync.result import PhaseResult


class TestPhaseResultDefaults:
    def test_defaults_all_zero(self) -> None:
        r = PhaseResult(entity_type="Profile")
        assert r.entity_type == "Profile"
        assert r.entities_inserted == 0
        assert r.entities_superseded == 0
        assert r.entities_unchanged == 0
        assert r.edges_inserted == 0
        assert r.edges_superseded == 0
        assert r.embeddings_queued == 0
        assert r.summaries_queued == 0
        assert r.edges_skipped_by_type == {}
        assert r.error_message is None

    def test_succeeded_true_when_no_error(self) -> None:
        assert PhaseResult(entity_type="Profile").succeeded is True

    def test_succeeded_false_on_error(self) -> None:
        r = PhaseResult(entity_type="Profile", error_message="boom")
        assert r.succeeded is False

    def test_edges_skipped_by_type_is_independent_per_instance(
        self,
    ) -> None:
        """Defaults to a fresh dict, not a shared one. Catches a
        regression where default_factory was misused (e.g.,
        =dict() instead of =field(default_factory=dict))."""
        r1 = PhaseResult(entity_type="Profile")
        r2 = PhaseResult(entity_type="Field")
        r1.edges_skipped_by_type["GRANTS_OBJECT_ACCESS"] = 1
        assert r2.edges_skipped_by_type == {}


class TestRecordSkippedEdge:
    def test_first_call_initializes_count_to_one(self) -> None:
        r = PhaseResult(entity_type="Profile")
        r.record_skipped_edge("GRANTS_OBJECT_ACCESS")
        assert r.edges_skipped_by_type == {"GRANTS_OBJECT_ACCESS": 1}

    def test_multiple_calls_increment_per_edge_type(self) -> None:
        r = PhaseResult(entity_type="Profile")
        for _ in range(5):
            r.record_skipped_edge("GRANTS_OBJECT_ACCESS")
        for _ in range(3):
            r.record_skipped_edge("GRANTS_FIELD_ACCESS")
        assert r.edges_skipped_by_type == {
            "GRANTS_OBJECT_ACCESS": 5,
            "GRANTS_FIELD_ACCESS": 3,
        }

    def test_record_distinguishes_edge_types(self) -> None:
        """Each edge_type is a separate bucket; no cross-contamination."""
        r = PhaseResult(entity_type="Field")
        r.record_skipped_edge("HAS_RELATIONSHIP_TO")
        r.record_skipped_edge("BELONGS_TO")
        r.record_skipped_edge("HAS_RELATIONSHIP_TO")
        assert r.edges_skipped_by_type == {
            "HAS_RELATIONSHIP_TO": 2,
            "BELONGS_TO": 1,
        }

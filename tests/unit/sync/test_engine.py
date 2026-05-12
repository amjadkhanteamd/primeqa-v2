"""Tests for primeqa.sync.engine — SyncEngine orchestration.

Strategy: the engine has a clean split between orchestration logic
(loops, sequencing, error handling) and DB-touching helpers
(_create_sync_run_row, _advance_last_completed_phase, etc.). We
mock the helpers and PHASE_REGISTRY to test orchestration in
isolation. DB correctness is verified separately at integration
time.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from primeqa.sync.engine import SyncEngine
from primeqa.sync.exceptions import (
    EntityOrderViolation,
    PhaseExecutionError,
    SyncEngineError,
)
from primeqa.sync.fk_assertion import ENTITY_ORDER
from primeqa.sync.result import PhaseResult


def _make_engine() -> SyncEngine:
    """Build a SyncEngine with mock SF + DB. FK assertion runs as
    part of __init__; for substrate-1's single-entities-table
    schema, it logs a warning and returns without raising."""
    mock_db = MagicMock(name="engine_db")
    mock_sf = MagicMock(name="sf_client")
    return SyncEngine(
        engine_db=mock_db,
        sf_client=mock_sf,
        tenant_schema="tenant_1",
    )


def _success_phase_fn(entity_type: str, count: int = 1):
    """Return a phase function that records a single insert and
    returns success."""
    def phase(ctx) -> PhaseResult:
        return PhaseResult(
            entity_type=entity_type,
            entities_inserted=count,
        )
    return phase


def _failure_phase_fn(entity_type: str, msg: str = "phase failed"):
    """Return a phase function that returns a failure PhaseResult."""
    def phase(ctx) -> PhaseResult:
        return PhaseResult(
            entity_type=entity_type,
            error_message=msg,
        )
    return phase


def _raising_phase_fn(entity_type: str, exc: Exception):
    """Return a phase function that raises an exception."""
    def phase(ctx) -> PhaseResult:
        raise exc
    return phase


class TestSyncEngineInit:
    def test_sync_engine_init_runs_fk_assertion(self) -> None:
        """Engine constructor invokes assert_entity_order_respects_
        schema_fks. We patch the assertion to verify the call."""
        with patch(
            "primeqa.sync.engine.assert_entity_order_respects_schema_fks"
        ) as mock_assert:
            SyncEngine(
                engine_db=MagicMock(),
                sf_client=MagicMock(),
                tenant_schema="tenant_42",
            )
            mock_assert.assert_called_once()
            # Verify it received the engine and tenant_schema
            call_args = mock_assert.call_args
            assert call_args.kwargs.get("tenant_schema") == "tenant_42" or \
                "tenant_42" in call_args.args

    def test_sync_engine_init_raises_on_entity_order_violation(self) -> None:
        """If the FK assertion raises, __init__ propagates it.
        No sync_run row should be created — engine never reaches
        run_sync."""
        with patch(
            "primeqa.sync.engine.assert_entity_order_respects_schema_fks",
            side_effect=EntityOrderViolation("bad order"),
        ):
            with pytest.raises(EntityOrderViolation):
                SyncEngine(
                    engine_db=MagicMock(),
                    sf_client=MagicMock(),
                    tenant_schema="tenant_1",
                )


class TestRunSyncOrchestration:
    """Tests that focus on the engine's run_sync orchestration
    using patched DB helpers + injected phase functions."""

    def test_run_sync_creates_new_sync_run_when_no_resume(self) -> None:
        """When resume_sync_run_id is None, _create_sync_run_row is
        called; otherwise it isn't."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value="new-run-id")
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        # Make all phases no-ops so the loop completes
        registry = {et: _success_phase_fn(et, count=0) for et in ENTITY_ORDER}
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry[et]):
            result = engine.run_sync(connected_org_id="org-1")

        engine._create_sync_run_row.assert_called_once_with("org-1")
        assert result == "new-run-id"

    def test_run_sync_resumes_from_last_completed_phase(self) -> None:
        """When resume_sync_run_id is provided, _create_sync_run_row
        is NOT called; engine resumes the existing run."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock()
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        registry = {et: _success_phase_fn(et, count=0) for et in ENTITY_ORDER}
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry[et]):
            result = engine.run_sync(
                connected_org_id="org-1",
                resume_sync_run_id="existing-run-id",
            )

        engine._create_sync_run_row.assert_not_called()
        assert result == "existing-run-id"

    def test_run_sync_advances_phase_marker_after_each_commit(self) -> None:
        """_advance_last_completed_phase is called for each successful
        phase in the run."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value="run-1")
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        registry = {et: _success_phase_fn(et, count=2) for et in ENTITY_ORDER}
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry[et]):
            engine.run_sync(connected_org_id="org-1")

        # Called once per entity type in ENTITY_ORDER
        assert engine._advance_last_completed_phase.call_count == len(ENTITY_ORDER)
        # Phase names passed in order
        called_phases = [
            c.args[1] for c in engine._advance_last_completed_phase.call_args_list
        ]
        assert called_phases == list(ENTITY_ORDER)

    def test_run_sync_runs_phases_in_entity_order(self) -> None:
        """Phase functions are invoked in ENTITY_ORDER sequence."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value="run-1")
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        call_order: list[str] = []

        def tracking_phase(entity_type: str):
            def phase(ctx) -> PhaseResult:
                call_order.append(entity_type)
                return PhaseResult(entity_type=entity_type)
            return phase

        registry = {et: tracking_phase(et) for et in ENTITY_ORDER}
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry[et]):
            engine.run_sync(connected_org_id="org-1")

        assert call_order == list(ENTITY_ORDER)

    def test_run_sync_calls_each_phase_exactly_once_per_full_run(self) -> None:
        """On a clean run with no resume, each phase function is
        called exactly once."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value="run-1")
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        call_counts: dict[str, int] = {et: 0 for et in ENTITY_ORDER}

        def counting_phase(entity_type: str):
            def phase(ctx) -> PhaseResult:
                call_counts[entity_type] += 1
                return PhaseResult(entity_type=entity_type)
            return phase

        registry = {et: counting_phase(et) for et in ENTITY_ORDER}
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry[et]):
            engine.run_sync(connected_org_id="org-1")

        for et in ENTITY_ORDER:
            assert call_counts[et] == 1, f"{et} called {call_counts[et]} times"

    def test_run_sync_skips_completed_phases_on_resume(self) -> None:
        """If last_completed_phase='Field', resume starts at the next
        phase (RecordType) and skips Object/PicklistValueSet/
        PicklistValue/Field."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock()
        engine._get_last_completed_phase = MagicMock(return_value="Field")
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        call_order: list[str] = []

        def tracking_phase(entity_type: str):
            def phase(ctx) -> PhaseResult:
                call_order.append(entity_type)
                return PhaseResult(entity_type=entity_type)
            return phase

        registry = {et: tracking_phase(et) for et in ENTITY_ORDER}
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry[et]):
            engine.run_sync(
                connected_org_id="org-1",
                resume_sync_run_id="run-1",
            )

        # Skipped: Object, PicklistValueSet, PicklistValue, Field
        # Resumed at: RecordType (idx 4)
        skipped = {"Object", "PicklistValueSet", "PicklistValue", "Field"}
        assert set(call_order).isdisjoint(skipped)
        assert call_order[0] == "RecordType"
        assert call_order == list(ENTITY_ORDER[4:])

    def test_run_sync_halts_on_first_phase_failure(self) -> None:
        """When a phase raises (or returns error), subsequent phases
        are NOT invoked."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value="run-1")
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        call_order: list[str] = []

        def make_phase(entity_type: str):
            def phase(ctx) -> PhaseResult:
                call_order.append(entity_type)
                if entity_type == "Field":
                    return PhaseResult(
                        entity_type=entity_type,
                        error_message="simulated failure",
                    )
                return PhaseResult(entity_type=entity_type)
            return phase

        registry = {et: make_phase(et) for et in ENTITY_ORDER}
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry[et]):
            engine.run_sync(connected_org_id="org-1")

        # Phases up to and including Field were called
        assert "Field" in call_order
        field_idx = call_order.index("Field")
        # Nothing after Field in ENTITY_ORDER was called
        post_field_phases = set(ENTITY_ORDER[ENTITY_ORDER.index("Field") + 1:])
        assert set(call_order[field_idx + 1:]).isdisjoint(post_field_phases)

    def test_run_sync_rolls_back_failed_phase_transaction(self) -> None:
        """When a phase fails, _phase_transaction's __exit__ receives
        the exception (signaling rollback should occur). The engine
        catches PhaseExecutionError at the run_sync level so it
        doesn't propagate."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value="run-1")
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()

        # Track __exit__ calls + their exception args
        exit_calls = []

        class TrackingContext:
            def __enter__(self):
                return MagicMock()

            def __exit__(self, exc_type, exc_val, exc_tb):
                exit_calls.append((exc_type, exc_val))
                # Returning False propagates the exception; engine
                # catches PhaseExecutionError at the outer try
                return False

        engine._phase_transaction = MagicMock(return_value=TrackingContext())

        registry = {
            "Object": _failure_phase_fn("Object", msg="boom"),
        }
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry.get(et)
                   or _success_phase_fn(et)):
            engine.run_sync(connected_org_id="org-1")

        # The first __exit__ call should have received
        # PhaseExecutionError (rollback signal)
        assert len(exit_calls) >= 1
        first_exc_type, first_exc_val = exit_calls[0]
        assert first_exc_type is PhaseExecutionError
        # Subsequent phases should not have opened transactions
        assert engine._mark_sync_run_failed.called

    def test_run_sync_marks_sync_run_structural_complete_on_success(self) -> None:
        """When all phases succeed, _mark_sync_run_structural_complete
        is called (NOT _mark_sync_run_failed)."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value="run-1")
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        registry = {et: _success_phase_fn(et) for et in ENTITY_ORDER}
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry[et]):
            engine.run_sync(connected_org_id="org-1")

        engine._mark_sync_run_structural_complete.assert_called_once_with(
            "run-1", "org-1",
        )
        engine._mark_sync_run_failed.assert_not_called()

    def test_run_sync_marks_sync_run_failed_on_phase_error(self) -> None:
        """When a phase fails, _mark_sync_run_failed is called with
        the failing phase name + PhaseExecutionError."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value="run-1")
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        registry = {"Object": _failure_phase_fn("Object", msg="kaboom")}
        with patch("primeqa.sync.engine.get_phase_function",
                   side_effect=lambda et: registry.get(et)
                   or _success_phase_fn(et)):
            engine.run_sync(connected_org_id="org-1")

        engine._mark_sync_run_structural_complete.assert_not_called()
        engine._mark_sync_run_failed.assert_called_once()
        call_args = engine._mark_sync_run_failed.call_args
        # First two positional args: sync_run_id, failed_phase
        assert call_args.args[0] == "run-1"
        assert call_args.args[1] == "Object"
        # Third positional arg: PhaseExecutionError
        assert isinstance(call_args.args[2], PhaseExecutionError)


class TestResumeFromUnknownPhase:
    def test_run_sync_raises_if_last_completed_phase_not_in_entity_order(
        self,
    ) -> None:
        """If the DB has a stale phase value that's not in ENTITY_ORDER,
        the engine raises SyncEngineError rather than silently
        miscounting."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value="run-1")
        engine._get_last_completed_phase = MagicMock(
            return_value="DeprecatedPhase",
        )
        engine._mark_sync_run_failed = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()

        with pytest.raises(SyncEngineError) as excinfo:
            engine.run_sync(connected_org_id="org-1")
        assert "DeprecatedPhase" in str(excinfo.value)
        assert "ENTITY_ORDER" in str(excinfo.value)

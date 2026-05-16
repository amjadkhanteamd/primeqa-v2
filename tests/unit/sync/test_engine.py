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
    returns success.

    Phase function signature is (ctx, conn) per the
    connection-threading refactor."""
    def phase(ctx, conn) -> PhaseResult:
        return PhaseResult(
            entity_type=entity_type,
            entities_inserted=count,
        )
    return phase


def _failure_phase_fn(entity_type: str, msg: str = "phase failed"):
    """Return a phase function that returns a failure PhaseResult."""
    def phase(ctx, conn) -> PhaseResult:
        return PhaseResult(
            entity_type=entity_type,
            error_message=msg,
        )
    return phase


def _raising_phase_fn(entity_type: str, exc: Exception):
    """Return a phase function that raises an exception."""
    def phase(ctx, conn) -> PhaseResult:
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
        engine._create_sync_run_row = MagicMock(return_value=("new-run-id", 1))
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
        engine._get_logical_version_seq = MagicMock(return_value=42)
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
        engine._create_sync_run_row = MagicMock(return_value=("run-1", 1))
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
        # Phase names passed in order. Signature is now
        # (conn, sync_run_id, phase_name, result) post-refactor;
        # phase_name is args[2].
        called_phases = [
            c.args[2] for c in engine._advance_last_completed_phase.call_args_list
        ]
        assert called_phases == list(ENTITY_ORDER)

    def test_run_sync_runs_phases_in_entity_order(self) -> None:
        """Phase functions are invoked in ENTITY_ORDER sequence."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value=("run-1", 1))
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        call_order: list[str] = []

        def tracking_phase(entity_type: str):
            def phase(ctx, conn) -> PhaseResult:
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
        engine._create_sync_run_row = MagicMock(return_value=("run-1", 1))
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        call_counts: dict[str, int] = {et: 0 for et in ENTITY_ORDER}

        def counting_phase(entity_type: str):
            def phase(ctx, conn) -> PhaseResult:
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
        engine._get_logical_version_seq = MagicMock(return_value=42)
        engine._get_last_completed_phase = MagicMock(return_value="Field")
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        call_order: list[str] = []

        def tracking_phase(entity_type: str):
            def phase(ctx, conn) -> PhaseResult:
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
        engine._create_sync_run_row = MagicMock(return_value=("run-1", 1))
        engine._get_last_completed_phase = MagicMock(return_value=None)
        engine._advance_last_completed_phase = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()
        engine._mark_sync_run_failed = MagicMock()
        engine._phase_transaction = MagicMock()
        engine._phase_transaction.return_value.__enter__ = MagicMock()
        engine._phase_transaction.return_value.__exit__ = MagicMock(return_value=False)

        call_order: list[str] = []

        def make_phase(entity_type: str):
            def phase(ctx, conn) -> PhaseResult:
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
        engine._create_sync_run_row = MagicMock(return_value=("run-1", 1))
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
        engine._create_sync_run_row = MagicMock(return_value=("run-1", 1))
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
        engine._create_sync_run_row = MagicMock(return_value=("run-1", 1))
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


class TestConnectAndAllocateLogicalVersion:
    """Per-Object-phase additions: _connect now sets app.tenant_id
    GUC; sync_run creation allocates a logical_versions row."""

    def test_connect_sets_tenant_id_guc(self) -> None:
        """_connect issues both SET search_path AND
        SET app.tenant_id, derived from tenant_schema."""
        mock_db = MagicMock(name="engine_db")
        mock_sf = MagicMock(name="sf_client")
        with patch(
            "primeqa.sync.engine.assert_entity_order_respects_schema_fks"
        ):
            engine = SyncEngine(
                engine_db=mock_db, sf_client=mock_sf,
                tenant_schema="tenant_42",
            )

        # Capture the connection mock's execute calls
        mock_conn = MagicMock()
        mock_db.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.begin.return_value.__enter__.return_value = None

        # Enter the _connect context to trigger the SET statements
        with engine._connect() as conn:
            pass

        # Collect all execute calls' SQL text
        sql_calls = [
            str(call.args[0]) for call in mock_conn.execute.call_args_list
        ]
        assert any("search_path" in s for s in sql_calls), (
            f"search_path SET not issued; calls: {sql_calls}"
        )
        assert any("app.tenant_id" in s for s in sql_calls), (
            f"app.tenant_id SET not issued; calls: {sql_calls}"
        )

    def test_create_sync_run_allocates_logical_version(self) -> None:
        """_create_sync_run_row calls _allocate_logical_version and
        back-links logical_version_seq onto the sync_run row.
        Returns (sync_run_id, version_seq) tuple."""
        engine = _make_engine()

        # Mock the inner methods: _connect returns a connection
        # whose execute returns a fetchone result.
        mock_conn = MagicMock()
        # First execute (INSERT sync_run): returns ('run-1',)
        # Second execute (INSERT logical_versions): handled by
        # _allocate_logical_version, which we patch
        # Third execute (UPDATE sync_run): no fetchone needed
        mock_conn.execute.return_value.fetchone.return_value = ("run-1",)

        # Patch _connect to yield this mock conn
        from contextlib import contextmanager

        @contextmanager
        def fake_connect():
            yield mock_conn

        engine._connect = fake_connect
        engine._allocate_logical_version = MagicMock(return_value=99)

        sync_run_id, version_seq = engine._create_sync_run_row("org-1")

        assert sync_run_id == "run-1"
        assert version_seq == 99
        engine._allocate_logical_version.assert_called_once_with(
            mock_conn, "run-1",
        )


class TestResumeFromUnknownPhase:
    def test_run_sync_raises_if_last_completed_phase_not_in_entity_order(
        self,
    ) -> None:
        """If the DB has a stale phase value that's not in ENTITY_ORDER,
        the engine raises SyncEngineError rather than silently
        miscounting."""
        engine = _make_engine()
        engine._create_sync_run_row = MagicMock(return_value=("run-1", 1))
        engine._get_last_completed_phase = MagicMock(
            return_value="DeprecatedPhase",
        )
        engine._mark_sync_run_failed = MagicMock()
        engine._mark_sync_run_structural_complete = MagicMock()

        with pytest.raises(SyncEngineError) as excinfo:
            engine.run_sync(connected_org_id="org-1")
        assert "DeprecatedPhase" in str(excinfo.value)
        assert "ENTITY_ORDER" in str(excinfo.value)


class TestMarkSyncRunStructuralCompleteAdvancesReadiness:
    """§26: ``_mark_sync_run_structural_complete`` advances
    readiness for EVERY org in the tenant whose ``sync_run`` is
    still ``'running'`` after seeding
    ``ai_enrichment_status='structural_only'``.

    Single-org case: exactly one running run → one iteration →
    behaviorally identical to the single-org pre-§26 single
    call.

    Multi-org case: D-030's touch path can shift entity
    attribution across orgs. When sync B completes and rotates
    ``last_synced_from_org_id`` to org B, org A's sync_run
    (still 'running') loses its queue-row credits. Without this
    loop, org A's sync_run stays 'running' indefinitely.

    Both readiness functions are idempotent.
    """

    @staticmethod
    def _mk_conn(running_orgs):
        """Return a mock_conn whose .execute() emits:
          - call 0 (UPDATE sync_runs): generic result
          - call 1 (UPDATE connected_orgs): generic result
          - call 2 (SELECT running orgs): result whose fetchall()
            returns a list of namespace-like rows with an .org_id
            attribute matching ``running_orgs``.
        """
        conn = MagicMock()
        select_result = MagicMock()
        select_result.fetchall.return_value = [
            MagicMock(org_id=oid) for oid in running_orgs
        ]
        # First 2 executes return generic MagicMock; 3rd returns
        # the configured select_result.
        conn.execute.side_effect = [
            MagicMock(),         # UPDATE sync_runs
            MagicMock(),         # UPDATE connected_orgs
            select_result,       # SELECT running orgs
        ]
        return conn

    def _setup_engine_with_conn(self, mock_conn):
        engine = _make_engine()
        engine._connect = MagicMock()
        engine._connect.return_value.__enter__.return_value = mock_conn
        engine._connect.return_value.__exit__.return_value = False
        return engine

    def test_single_org_advances_just_that_org(self) -> None:
        """Single-org case: SELECT returns one running org → both
        readiness functions called exactly once."""
        mock_conn = self._mk_conn(running_orgs=["org-a"])
        engine = self._setup_engine_with_conn(mock_conn)

        with patch(
            "primeqa.sync.readiness.apply_org_status"
        ) as apply_mock, patch(
            "primeqa.sync.readiness.maybe_finalize_run"
        ) as finalize_mock:
            engine._mark_sync_run_structural_complete(
                sync_run_id="run-1", connected_org_id="org-a",
            )

        apply_mock.assert_called_once()
        finalize_mock.assert_called_once()
        assert apply_mock.call_args.args[0] is mock_conn
        assert apply_mock.call_args.args[1] == "org-a"
        assert finalize_mock.call_args.args[0] is mock_conn
        assert finalize_mock.call_args.args[1] == "org-a"

    def test_multi_org_advances_every_running_org(self) -> None:
        """§26 multi-org closure: when SELECT returns multiple
        running orgs (e.g., org A's sync_run is still 'running'
        when org B completes structural and touches entities to
        org B), readiness is advanced for EACH. Captures the
        D-030 cross-org attribution shift."""
        mock_conn = self._mk_conn(running_orgs=["org-a", "org-b"])
        engine = self._setup_engine_with_conn(mock_conn)

        with patch(
            "primeqa.sync.readiness.apply_org_status"
        ) as apply_mock, patch(
            "primeqa.sync.readiness.maybe_finalize_run"
        ) as finalize_mock:
            # The "current" sync is org-b (this is the call that
            # completed structural for org B). Engine should
            # advance both org-a and org-b.
            engine._mark_sync_run_structural_complete(
                sync_run_id="run-b", connected_org_id="org-b",
            )

        assert apply_mock.call_count == 2
        assert finalize_mock.call_count == 2
        called_orgs_apply = {
            c.args[1] for c in apply_mock.call_args_list
        }
        called_orgs_finalize = {
            c.args[1] for c in finalize_mock.call_args_list
        }
        assert called_orgs_apply == {"org-a", "org-b"}
        assert called_orgs_finalize == {"org-a", "org-b"}

    def test_zero_running_orgs_calls_nothing(self) -> None:
        """Defensive: if no orgs have running sync_runs
        (race/edge), neither readiness function fires.
        Structural seed UPDATEs still emit normally."""
        mock_conn = self._mk_conn(running_orgs=[])
        engine = self._setup_engine_with_conn(mock_conn)

        with patch(
            "primeqa.sync.readiness.apply_org_status"
        ) as apply_mock, patch(
            "primeqa.sync.readiness.maybe_finalize_run"
        ) as finalize_mock:
            engine._mark_sync_run_structural_complete(
                sync_run_id="run-1", connected_org_id="org-a",
            )

        apply_mock.assert_not_called()
        finalize_mock.assert_not_called()

    def test_structural_complete_emits_expected_updates(self) -> None:
        """Sanity: the two pre-existing UPDATE statements are
        still emitted in the same order with the same parameters,
        plus the §26 multi-org SELECT. Catches a regression where
        the §26 readiness wiring accidentally drops or reorders
        the seed updates."""
        mock_conn = self._mk_conn(running_orgs=[])
        engine = self._setup_engine_with_conn(mock_conn)

        with patch("primeqa.sync.readiness.apply_org_status"), \
             patch("primeqa.sync.readiness.maybe_finalize_run"):
            engine._mark_sync_run_structural_complete(
                sync_run_id="run-1", connected_org_id="org-a",
            )

        # 3 executes now: UPDATE sync_runs / UPDATE connected_orgs
        # / SELECT running orgs.
        assert mock_conn.execute.call_count == 3
        sync_runs_call = mock_conn.execute.call_args_list[0]
        orgs_call = mock_conn.execute.call_args_list[1]
        select_call = mock_conn.execute.call_args_list[2]
        assert "UPDATE sync_runs" in str(sync_runs_call.args[0])
        assert sync_runs_call.args[1] == {"id": "run-1"}
        assert "UPDATE connected_orgs" in str(orgs_call.args[0])
        assert orgs_call.args[1] == {
            "run_id": "run-1", "id": "org-a",
        }
        assert "SELECT" in str(select_call.args[0])
        assert "sr.status = 'running'" in str(select_call.args[0])

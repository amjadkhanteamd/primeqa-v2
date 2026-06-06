"""Unit tests for §24 feature readiness signaling.

Strategy mirrors test_worker_enrichment.py / test_materialize.py: the
DB session is a MagicMock, and we verify (a) the SQL strings issued
to the session, (b) the orchestration around them (which branches
fire, in what order), and (c) the classification logic (terminal
status selection, idempotency, no-op short-circuits).

The actual SQL correctness — does the CTE compute the right four-state
status across a real queue + entities + sync_runs combination — is
exercised by tests/integration/test_live_enrichment.py's lifecycle
assertions.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest

from primeqa import worker
from primeqa.sync import readiness

pytestmark = pytest.mark.unit


ORG_A = "00000000-0000-0000-0000-00000000000a"
ORG_B = "00000000-0000-0000-0000-00000000000b"


# ----------------------------------------------------------------------
# TickResult — pure dataclass behaviour (no DB)
# ----------------------------------------------------------------------

class TestTickResult:
    def test_empty_result_has_no_work(self) -> None:
        r = worker.TickResult()
        assert r.did_work is False
        assert r.claimed == 0
        assert r.affected_orgs == set()
        assert r.succeeded_for(ORG_A) == 0
        assert r.failed_permanent_for(ORG_A) == 0

    def test_claimed_makes_did_work_true(self) -> None:
        r = worker.TickResult(claimed=3)
        assert r.did_work is True

    def test_credit_adds_to_per_org(self) -> None:
        r = worker.TickResult(claimed=2)
        r.credit(ORG_A, succeeded=1)
        r.credit(ORG_A, failed_permanent=1)
        assert r.succeeded_for(ORG_A) == 1
        assert r.failed_permanent_for(ORG_A) == 1
        assert r.affected_orgs == {ORG_A}

    def test_credit_aggregates_across_calls(self) -> None:
        r = worker.TickResult(claimed=5)
        r.credit(ORG_A, succeeded=2)
        r.credit(ORG_A, succeeded=3)
        assert r.succeeded_for(ORG_A) == 5

    def test_credit_with_null_org_is_noop(self) -> None:
        """An entity with NULL last_synced_from_org_id was already
        warned about in _fetch_org_ids; credit() drops it on the
        floor rather than crash."""
        r = worker.TickResult(claimed=1)
        r.credit(None, succeeded=1)
        assert r.affected_orgs == set()

    def test_credit_multi_org(self) -> None:
        r = worker.TickResult(claimed=4)
        r.credit(ORG_A, succeeded=2)
        r.credit(ORG_B, succeeded=1, failed_permanent=1)
        assert r.affected_orgs == {ORG_A, ORG_B}
        assert r.succeeded_for(ORG_A) == 2
        assert r.succeeded_for(ORG_B) == 1
        assert r.failed_permanent_for(ORG_B) == 1

    def test_succeeded_for_unknown_org(self) -> None:
        r = worker.TickResult(claimed=1)
        r.credit(ORG_A, succeeded=1)
        assert r.succeeded_for("not-an-org-id") == 0
        assert r.failed_permanent_for("not-an-org-id") == 0


# ----------------------------------------------------------------------
# compute_org_status — verifies the function reads the scalar
# ----------------------------------------------------------------------

class TestComputeOrgStatus:
    @pytest.mark.parametrize("scalar", [
        "none", "structural_only", "partial", "complete",
    ])
    def test_returns_status_from_query(self, scalar: str) -> None:
        session = mock.MagicMock()
        session.execute.return_value.fetchone.return_value = (scalar,)
        assert readiness.compute_org_status(session, ORG_A) == scalar

    def test_returns_none_when_no_row(self) -> None:
        """fetchone returning None (no connected_orgs row at all) is
        a defensive 'none' rather than a crash."""
        session = mock.MagicMock()
        session.execute.return_value.fetchone.return_value = None
        assert readiness.compute_org_status(session, ORG_A) == "none"

    def test_org_id_is_passed_as_str(self) -> None:
        """UUID objects are stringified at the boundary so the
        psycopg2 binding always sees text — matches the pattern used
        elsewhere in worker.py."""
        import uuid
        session = mock.MagicMock()
        session.execute.return_value.fetchone.return_value = ("none",)
        readiness.compute_org_status(session, uuid.UUID(ORG_A))
        params = session.execute.call_args[0][1]
        assert params["id"] == ORG_A


# ----------------------------------------------------------------------
# apply_org_status — compute + UPDATE; idempotent
# ----------------------------------------------------------------------

class TestApplyOrgStatus:
    def test_computes_then_updates(self) -> None:
        session = mock.MagicMock()
        # First execute() is the SELECT (compute); second is the UPDATE.
        session.execute.return_value.fetchone.return_value = ("partial",)
        result = readiness.apply_org_status(session, ORG_A)
        assert result == "partial"
        assert session.execute.call_count == 2
        update_sql = session.execute.call_args_list[1][0][0].text
        assert "UPDATE connected_orgs" in update_sql
        update_params = session.execute.call_args_list[1][0][1]
        assert update_params["status"] == "partial"
        assert update_params["id"] == ORG_A

    def test_idempotent_two_calls_same_value(self) -> None:
        session = mock.MagicMock()
        session.execute.return_value.fetchone.return_value = ("complete",)
        first = readiness.apply_org_status(session, ORG_A)
        second = readiness.apply_org_status(session, ORG_A)
        assert first == second == "complete"
        # Each call issues SELECT + UPDATE; row-level write is a no-op
        # at the DB layer but the UPDATE is emitted either way.
        assert session.execute.call_count == 4


# ----------------------------------------------------------------------
# increment_run_counters — emits UPDATE conditionally
# ----------------------------------------------------------------------

class TestIncrementRunCounters:
    def test_emits_update_when_any_delta_nonzero(self) -> None:
        session = mock.MagicMock()
        readiness.increment_run_counters(
            session, ORG_A, embeddings_delta=128,
            summaries_delta=5, summaries_failed_delta=1,
        )
        assert session.execute.call_count == 1
        sql = session.execute.call_args[0][0].text
        assert "UPDATE sync_runs" in sql
        params = session.execute.call_args[0][1]
        assert params == {"emb": 128, "sum": 5, "failed": 1, "org": ORG_A}

    def test_skips_update_when_all_deltas_zero(self) -> None:
        session = mock.MagicMock()
        readiness.increment_run_counters(session, ORG_A)
        assert session.execute.call_count == 0

    def test_only_embeddings_delta(self) -> None:
        session = mock.MagicMock()
        readiness.increment_run_counters(
            session, ORG_A, embeddings_delta=12,
        )
        params = session.execute.call_args[0][1]
        assert params["emb"] == 12
        assert params["sum"] == 0
        assert params["failed"] == 0


# ----------------------------------------------------------------------
# maybe_finalize_run — terminal classification + idempotency
# ----------------------------------------------------------------------

class TestMaybeFinalizeRun:
    def _setup_session(self, *, status: str, retryable_count: int = 0,
                       has_failed_perm: bool = False, rowcount: int = 1):
        """Wire the MagicMock execute() to return:
        - 1st call (status SELECT): scalar=status
        - 2nd call (retryable_count SELECT): scalar=retryable_count
        - 3rd call (failed_permanent EXISTS): scalar=has_failed_perm
        - 4th call (UPDATE): rowcount=rowcount
        Earlier calls are not made if status != 'complete'.
        """
        session = mock.MagicMock()
        scalars = []
        if status != "complete":
            scalars = [status]
        else:
            scalars = [status, retryable_count, has_failed_perm]

        scalar_iter = iter(scalars)
        def _exec(*args, **kwargs):
            r = mock.MagicMock()
            r.scalar.side_effect = lambda: next(scalar_iter, None)
            r.rowcount = rowcount
            return r
        session.execute.side_effect = _exec
        return session

    def test_not_complete_short_circuits(self) -> None:
        for status in ("none", "structural_only", "partial"):
            session = self._setup_session(status=status)
            assert readiness.maybe_finalize_run(session, ORG_A) is False
            # Only the status-SELECT was issued.
            assert session.execute.call_count == 1

    def test_complete_no_failed_permanent_emits_success(self) -> None:
        session = self._setup_session(
            status="complete", has_failed_perm=False, rowcount=1,
        )
        assert readiness.maybe_finalize_run(session, ORG_A) is True
        update_call = session.execute.call_args_list[-1]
        params = update_call[0][1]
        assert params["terminal"] == "success"

    def test_complete_with_failed_permanent_emits_partial_success(self) -> None:
        session = self._setup_session(
            status="complete", has_failed_perm=True, rowcount=1,
        )
        assert readiness.maybe_finalize_run(session, ORG_A) is True
        params = session.execute.call_args_list[-1][0][1]
        assert params["terminal"] == "partial_success"

    def test_complete_with_retryable_logs_warning(self, caplog) -> None:
        """failed_retryable rows shouldn't be present when status is
        'complete' (compute_org_status puts them in non-terminal).
        If they are: log warning, but still proceed to finalize."""
        session = self._setup_session(
            status="complete", retryable_count=2, has_failed_perm=False,
        )
        with caplog.at_level("WARNING"):
            readiness.maybe_finalize_run(session, ORG_A)
        assert any("worker-bug indicator" in r.message for r in caplog.records)

    def test_idempotent_when_already_finalized(self) -> None:
        """A second call after the run was already finalized: the
        UPDATE matches zero rows (WHERE status='running' blocks it),
        so the function returns False."""
        session = self._setup_session(
            status="complete", has_failed_perm=False, rowcount=0,
        )
        assert readiness.maybe_finalize_run(session, ORG_A) is False


# ----------------------------------------------------------------------
# requeue_failed_enrichment / count_failed_enrichment (D-180)
# ----------------------------------------------------------------------

class TestRequeueFailedEnrichment:
    def _session(self, *, rowcount, status="partial"):
        """execute() yields a fresh mock per call: the UPDATE reads
        .rowcount; apply_org_status's compute-SELECT reads .fetchone."""
        session = mock.MagicMock()

        def _exec(*a, **k):
            r = mock.MagicMock()
            r.rowcount = rowcount
            r.fetchone.return_value = (status,)
            return r
        session.execute.side_effect = _exec
        return session

    def test_resets_failed_permanent_to_pending(self) -> None:
        session = self._session(rowcount=5)
        n = readiness.requeue_failed_enrichment(session, ORG_A)
        assert n == 5
        upd = session.execute.call_args_list[0][0][0].text
        assert "UPDATE ai_enrichment_queue" in upd
        assert "failed_permanent" in upd
        assert "'pending'" in upd
        assert "attempts = 0" in upd
        assert session.execute.call_args_list[0][0][1]["id"] == ORG_A

    def test_recomputes_org_status_when_rows_reset(self) -> None:
        """≥1 row reset → apply_org_status runs (SELECT compute + UPDATE
        connected_orgs) so the org flips off 'complete'."""
        session = self._session(rowcount=3)
        readiness.requeue_failed_enrichment(session, ORG_A)
        # UPDATE queue + apply_org_status(SELECT + UPDATE connected_orgs) = 3
        assert session.execute.call_count == 3
        assert "UPDATE connected_orgs" in \
            session.execute.call_args_list[-1][0][0].text

    def test_no_rows_skips_status_recompute(self) -> None:
        session = self._session(rowcount=0)
        n = readiness.requeue_failed_enrichment(session, ORG_A)
        assert n == 0
        assert session.execute.call_count == 1  # only the UPDATE, no apply

    def test_null_rowcount_is_zero(self) -> None:
        session = self._session(rowcount=None)
        assert readiness.requeue_failed_enrichment(session, ORG_A) == 0

    def test_org_id_stringified(self) -> None:
        import uuid
        session = self._session(rowcount=1)
        readiness.requeue_failed_enrichment(session, uuid.UUID(ORG_A))
        assert session.execute.call_args_list[0][0][1]["id"] == ORG_A


class TestCountFailedEnrichment:
    def test_returns_count(self) -> None:
        session = mock.MagicMock()
        session.execute.return_value.scalar.return_value = 42
        assert readiness.count_failed_enrichment(session, ORG_A) == 42
        sql = session.execute.call_args[0][0].text
        assert "COUNT(*)" in sql
        assert "failed_permanent" in sql

    def test_none_scalar_is_zero(self) -> None:
        session = mock.MagicMock()
        session.execute.return_value.scalar.return_value = None
        assert readiness.count_failed_enrichment(session, ORG_A) == 0

    def test_org_id_stringified(self) -> None:
        import uuid
        session = mock.MagicMock()
        session.execute.return_value.scalar.return_value = 0
        readiness.count_failed_enrichment(session, uuid.UUID(ORG_A))
        assert session.execute.call_args[0][1]["id"] == ORG_A


# ----------------------------------------------------------------------
# enrichment_tick harness — readiness wiring around the subticks
# ----------------------------------------------------------------------

class TestEnrichmentTickHarness:
    """Verify enrichment_tick calls readiness functions for each
    affected org with the right deltas."""

    def _setup(self, emb_result, sum_result):
        """Patch the subticks to return predetermined TickResults
        and capture the readiness function calls."""
        stack = ExitStack()
        # Patch the discovery so we hit exactly one fake tenant.
        stack.enter_context(mock.patch.object(
            worker, "_discover_tenant_schemas",
            return_value=[("tenant_1", 1)],
        ))
        stack.enter_context(mock.patch.object(
            worker, "_set_tenant_context",
        ))
        stack.enter_context(mock.patch.object(
            worker, "_embedding_subtick", return_value=emb_result,
        ))
        stack.enter_context(mock.patch.object(
            worker, "_summary_subtick", return_value=sum_result,
        ))
        inc = stack.enter_context(mock.patch.object(
            worker.readiness, "increment_run_counters",
        ))
        apply = stack.enter_context(mock.patch.object(
            worker.readiness, "apply_org_status",
        ))
        finalize = stack.enter_context(mock.patch.object(
            worker.readiness, "maybe_finalize_run",
        ))
        factory = mock.MagicMock()
        return stack, inc, apply, finalize, factory

    def test_no_work_no_readiness_calls(self) -> None:
        emb_result = worker.TickResult(claimed=0)
        sum_result = worker.TickResult(claimed=0)
        stack, inc, apply, finalize, factory = self._setup(
            emb_result, sum_result,
        )
        with stack:
            result = worker.enrichment_tick(db_factory=factory)
        assert result is False
        assert inc.call_count == 0
        assert apply.call_count == 0
        assert finalize.call_count == 0

    def test_single_org_credits_correctly(self) -> None:
        emb_result = worker.TickResult(claimed=10)
        emb_result.credit(ORG_A, succeeded=10)
        sum_result = worker.TickResult(claimed=3)
        sum_result.credit(ORG_A, succeeded=2, failed_permanent=1)
        stack, inc, apply, finalize, factory = self._setup(
            emb_result, sum_result,
        )
        with stack:
            result = worker.enrichment_tick(db_factory=factory)
        assert result is True
        # increment_run_counters called once for ORG_A with combined deltas
        assert inc.call_count == 1
        inc_kwargs = inc.call_args.kwargs
        assert inc_kwargs["embeddings_delta"] == 10
        assert inc_kwargs["summaries_delta"] == 2
        assert inc_kwargs["summaries_failed_delta"] == 1
        # apply_org_status + maybe_finalize_run each called once for ORG_A
        assert apply.call_count == 1
        assert finalize.call_count == 1

    def test_multi_org_calls_per_org(self) -> None:
        emb_result = worker.TickResult(claimed=5)
        emb_result.credit(ORG_A, succeeded=3)
        emb_result.credit(ORG_B, succeeded=2)
        sum_result = worker.TickResult(claimed=0)
        stack, inc, apply, finalize, factory = self._setup(
            emb_result, sum_result,
        )
        with stack:
            worker.enrichment_tick(db_factory=factory)
        # Each org gets one of each readiness call.
        assert inc.call_count == 2
        assert apply.call_count == 2
        assert finalize.call_count == 2
        called_orgs = {c.args[1] for c in inc.call_args_list}
        assert called_orgs == {ORG_A, ORG_B}

    def test_summary_only_org_still_credited(self) -> None:
        """An org touched only by the summary subtick (no embedding
        work this tick) still gets credited and finalized — the
        affected_orgs union includes it."""
        emb_result = worker.TickResult(claimed=0)
        sum_result = worker.TickResult(claimed=2)
        sum_result.credit(ORG_A, succeeded=2)
        stack, inc, apply, finalize, factory = self._setup(
            emb_result, sum_result,
        )
        with stack:
            worker.enrichment_tick(db_factory=factory)
        assert inc.call_count == 1
        assert inc.call_args.kwargs["embeddings_delta"] == 0
        assert inc.call_args.kwargs["summaries_delta"] == 2

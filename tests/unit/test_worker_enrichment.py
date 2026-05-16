"""Unit tests for the §23 enrichment worker (primeqa.worker).

Strategy mirrors test_materialize.py: the DB session is a MagicMock,
and the helper functions / external clients (embed_batch, llm_call,
record_embedding_usage) are patched. Tests verify the orchestration —
which helpers run, with what arguments, and the success/retry/permanent
classification — not the raw SQL (the SQL is exercised by
test_live_enrichment.py).
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest

from primeqa import worker
from primeqa.intelligence.embeddings import VoyageError

pytestmark = pytest.mark.unit


def _patch(name):
    return mock.patch.object(worker, name)


def _claimed(queue_id, entity_id, *, entity_type="Field", attempts=1):
    return {
        "queue_id": queue_id, "entity_type": entity_type,
        "entity_id": entity_id, "attempts": attempts,
    }


# ----------------------------------------------------------------------
# _mark_failed — retry vs. permanent classification
# ----------------------------------------------------------------------

class TestMarkFailed:
    def _status_written(self, session) -> str:
        return session.execute.call_args[0][1]["st"]

    def test_retryable_under_cap_is_failed_retryable(self) -> None:
        session = mock.MagicMock()
        worker._mark_failed(session, "q1", retryable=True, err="boom",
                            attempts=1)
        assert self._status_written(session) == "failed_retryable"

    def test_retryable_at_cap_is_failed_permanent(self) -> None:
        """attempts == ENRICHMENT_MAX_ATTEMPTS → no more retries."""
        session = mock.MagicMock()
        worker._mark_failed(session, "q1", retryable=True, err="boom",
                            attempts=worker.ENRICHMENT_MAX_ATTEMPTS)
        assert self._status_written(session) == "failed_permanent"

    def test_non_retryable_is_failed_permanent_immediately(self) -> None:
        session = mock.MagicMock()
        worker._mark_failed(session, "q1", retryable=False, err="bad",
                            attempts=1)
        assert self._status_written(session) == "failed_permanent"

    def test_error_text_truncated_to_2000(self) -> None:
        session = mock.MagicMock()
        worker._mark_failed(session, "q1", retryable=True,
                            err="x" * 5000, attempts=1)
        assert len(session.execute.call_args[0][1]["err"]) == 2000


# ----------------------------------------------------------------------
# _discover_tenant_schemas
# ----------------------------------------------------------------------

class TestDiscoverTenantSchemas:
    def test_parses_schema_name_to_tenant_id(self) -> None:
        session = mock.MagicMock()
        session.execute.return_value.fetchall.return_value = [
            ("tenant_1",), ("tenant_42",),
        ]
        assert worker._discover_tenant_schemas(session) == [
            ("tenant_1", 1), ("tenant_42", 42),
        ]

    def test_empty_when_no_tenant_schemas(self) -> None:
        session = mock.MagicMock()
        session.execute.return_value.fetchall.return_value = []
        assert worker._discover_tenant_schemas(session) == []


# ----------------------------------------------------------------------
# _claim_batch — SQL shape
# ----------------------------------------------------------------------

class TestClaimBatch:
    def test_claims_pending_and_failed_retryable_skip_locked(self) -> None:
        """The claim picks up both pending AND failed_retryable rows
        (so transient failures retry on a later tick), uses FOR UPDATE
        SKIP LOCKED, and clears completed_at (a re-claimed
        failed_retryable row had it set)."""
        session = mock.MagicMock()
        session.execute.return_value.fetchall.return_value = []
        worker._claim_batch(session, "embedding", 128)
        sql = str(session.execute.call_args[0][0])
        assert "status IN ('pending', 'failed_retryable')" in sql
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "completed_at = NULL" in sql
        assert "attempts = attempts + 1" in sql

    def test_entity_types_filter_applied_when_given(self) -> None:
        session = mock.MagicMock()
        session.execute.return_value.fetchall.return_value = []
        worker._claim_batch(session, "summary", 5,
                            entity_types=["Flow", "ValidationRule"])
        params = session.execute.call_args[0][1]
        assert params["ets"] == ["Flow", "ValidationRule"]
        assert "entity_type = ANY(:ets)" in str(session.execute.call_args[0][0])

    def test_returns_claimed_rows_as_dicts(self) -> None:
        session = mock.MagicMock()
        session.execute.return_value.fetchall.return_value = [
            ("q1", "Field", "11111111-1111-1111-1111-111111111111", 1),
        ]
        rows = worker._claim_batch(session, "embedding", 128)
        assert rows == [{
            "queue_id": "q1", "entity_type": "Field",
            "entity_id": "11111111-1111-1111-1111-111111111111",
            "attempts": 1,
        }]


# ----------------------------------------------------------------------
# _embedding_subtick — orchestration
# ----------------------------------------------------------------------

class TestEmbeddingSubtick:
    def test_empty_queue_returns_no_work(self) -> None:
        session = mock.MagicMock()
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = []
            result = worker._embedding_subtick(session, tenant_id=1)
        assert result.did_work is False
        assert result.claimed == 0

    def test_happy_path_embeds_writes_marks_records_usage(self) -> None:
        session = mock.MagicMock()
        claimed = [_claimed("q1", "e1"), _claimed("q2", "e2")]
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = claimed
            s.enter_context(_patch("_fetch_semantic_texts")).return_value = {
                "e1": "text one", "e2": "text two",
            }
            s.enter_context(_patch("_fetch_org_ids")).return_value = {
                "e1": "00000000-0000-0000-0000-00000000000a",
                "e2": "00000000-0000-0000-0000-00000000000a",
            }
            mock_embed = s.enter_context(_patch("embed_batch"))
            mock_embed.return_value = [[0.1] * 1024, [0.2] * 1024]
            mock_write = s.enter_context(_patch("_write_embedding"))
            mock_ok = s.enter_context(_patch("_mark_succeeded"))
            mock_fail = s.enter_context(_patch("_mark_failed"))
            mock_usage = s.enter_context(_patch("record_embedding_usage"))

            result = worker._embedding_subtick(session, tenant_id=7)

        assert result.did_work is True
        assert result.claimed == 2
        assert result.succeeded_for(
            "00000000-0000-0000-0000-00000000000a") == 2
        mock_embed.assert_called_once_with(["text one", "text two"],
                                           input_type="document")
        assert mock_write.call_count == 2
        assert mock_ok.call_count == 2
        mock_fail.assert_not_called()
        # usage recorded once per batch, with the batch size + tenant
        mock_usage.assert_called_once()
        assert mock_usage.call_args[0][0] == 7
        assert mock_usage.call_args.kwargs["batch_size"] == 2

    def test_null_semantic_text_marked_succeeded_not_embedded(self) -> None:
        """An entity whose semantic_text is NULL/blank is a no-op
        success — no embed call for it, no failure."""
        session = mock.MagicMock()
        claimed = [_claimed("q1", "e1"), _claimed("q2", "e2")]
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = claimed
            s.enter_context(_patch("_fetch_semantic_texts")).return_value = {
                "e1": "real text", "e2": "   ",  # e2 blank
            }
            s.enter_context(_patch("_fetch_org_ids")).return_value = {
                "e1": "org-a", "e2": "org-a",
            }
            mock_embed = s.enter_context(_patch("embed_batch"))
            mock_embed.return_value = [[0.1] * 1024]
            mock_write = s.enter_context(_patch("_write_embedding"))
            mock_ok = s.enter_context(_patch("_mark_succeeded"))
            s.enter_context(_patch("record_embedding_usage"))

            worker._embedding_subtick(session, tenant_id=1)

        # Only e1's text was embedded
        mock_embed.assert_called_once_with(["real text"],
                                           input_type="document")
        # e2 still marked succeeded (no-op), e1 marked after write
        assert mock_ok.call_count == 2
        assert mock_write.call_count == 1

    def test_all_null_semantic_text_no_embed_call(self) -> None:
        session = mock.MagicMock()
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = [
                _claimed("q1", "e1"),
            ]
            s.enter_context(_patch("_fetch_semantic_texts")).return_value = {
                "e1": None,
            }
            s.enter_context(_patch("_fetch_org_ids")).return_value = {
                "e1": "org-a",
            }
            mock_embed = s.enter_context(_patch("embed_batch"))
            mock_ok = s.enter_context(_patch("_mark_succeeded"))
            result = worker._embedding_subtick(session, tenant_id=1)
        assert result.did_work is True  # rows were claimed
        assert result.claimed == 1
        mock_embed.assert_not_called()
        mock_ok.assert_called_once()

    def test_voyage_retryable_marks_all_failed_retryable(self) -> None:
        session = mock.MagicMock()
        claimed = [_claimed("q1", "e1", attempts=2)]
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = claimed
            s.enter_context(_patch("_fetch_semantic_texts")).return_value = {
                "e1": "text",
            }
            s.enter_context(_patch("embed_batch")).side_effect = VoyageError(
                "rate limited", retryable=True,
            )
            mock_fail = s.enter_context(_patch("_mark_failed"))
            s.enter_context(_patch("_mark_succeeded"))
            worker._embedding_subtick(session, tenant_id=1)
        mock_fail.assert_called_once()
        # (session, queue_id, retryable, err, attempts)
        assert mock_fail.call_args[0][1] == "q1"
        assert mock_fail.call_args[0][2] is True   # retryable

    def test_voyage_permanent_marks_all_failed_permanent(self) -> None:
        session = mock.MagicMock()
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = [
                _claimed("q1", "e1"),
            ]
            s.enter_context(_patch("_fetch_semantic_texts")).return_value = {
                "e1": "text",
            }
            s.enter_context(_patch("embed_batch")).side_effect = VoyageError(
                "auth error", retryable=False,
            )
            mock_fail = s.enter_context(_patch("_mark_failed"))
            worker._embedding_subtick(session, tenant_id=1)
        assert mock_fail.call_args[0][2] is False  # not retryable

    def test_vector_count_mismatch_marks_retryable(self) -> None:
        """Voyage returning fewer vectors than inputs → retryable."""
        session = mock.MagicMock()
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = [
                _claimed("q1", "e1"), _claimed("q2", "e2"),
            ]
            s.enter_context(_patch("_fetch_semantic_texts")).return_value = {
                "e1": "a", "e2": "b",
            }
            s.enter_context(_patch("embed_batch")).return_value = [
                [0.1] * 1024,  # only 1 vector for 2 inputs
            ]
            mock_fail = s.enter_context(_patch("_mark_failed"))
            mock_write = s.enter_context(_patch("_write_embedding"))
            worker._embedding_subtick(session, tenant_id=1)
        assert mock_fail.call_count == 2
        assert all(c[0][2] is True for c in mock_fail.call_args_list)
        mock_write.assert_not_called()


# ----------------------------------------------------------------------
# _summary_subtick — orchestration
# ----------------------------------------------------------------------

class TestSummarySubtick:
    def test_no_anthropic_key_returns_no_work_without_claiming(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        session = mock.MagicMock()
        with _patch("_claim_batch") as mock_claim:
            result = worker._summary_subtick(session, tenant_id=1)
        assert result.did_work is False
        mock_claim.assert_not_called()

    def test_empty_queue_returns_no_work(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        session = mock.MagicMock()
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = []
            result = worker._summary_subtick(session, tenant_id=1)
        assert result.did_work is False
        assert result.claimed == 0

    def test_happy_path_calls_llm_embeds_writes_marks(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        session = mock.MagicMock()
        claimed = [_claimed("q1", "e1", entity_type="Flow")]
        resp = mock.MagicMock(parsed_content="A flow that does X.",
                              model="claude-haiku-4-5-20251001",
                              prompt_version="entity_summary_flow@v1")
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = claimed
            s.enter_context(_patch("_fetch_summary_context")).return_value = {
                "name": "MyFlow",
            }
            s.enter_context(mock.patch(
                "primeqa.intelligence.llm.gateway.llm_call",
                return_value=resp,
            ))
            mock_embed = s.enter_context(_patch("embed_batch"))
            mock_embed.return_value = [[0.3] * 1024]
            mock_write = s.enter_context(_patch("_write_summary"))
            mock_ok = s.enter_context(_patch("_mark_succeeded"))
            result = worker._summary_subtick(session, tenant_id=4)
        assert result.did_work is True
        assert result.claimed == 1
        mock_write.assert_called_once()
        # _write_summary(session, entity_type, entity_id, text, vec, ...)
        assert mock_write.call_args[0][1] == "Flow"
        assert mock_write.call_args[0][3] == "A flow that does X."
        mock_ok.assert_called_once()

    def test_llm_validation_rule_routes_to_vr_task(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        session = mock.MagicMock()
        resp = mock.MagicMock(parsed_content="Rule.", model="m",
                              prompt_version="v")
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = [
                _claimed("q1", "e1", entity_type="ValidationRule"),
            ]
            s.enter_context(_patch("_fetch_summary_context")).return_value = {
                "name": "R",
            }
            mock_llm = s.enter_context(mock.patch(
                "primeqa.intelligence.llm.gateway.llm_call",
                return_value=resp,
            ))
            s.enter_context(_patch("embed_batch")).return_value = [[0.1] * 1024]
            s.enter_context(_patch("_write_summary"))
            s.enter_context(_patch("_mark_succeeded"))
            worker._summary_subtick(session, tenant_id=1)
        assert mock_llm.call_args.kwargs["task"] == "entity_summary_validation_rule"

    def test_llm_auth_error_is_permanent(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        session = mock.MagicMock()
        from primeqa.intelligence.llm.gateway import LLMError
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = [
                _claimed("q1", "e1", entity_type="Flow"),
            ]
            s.enter_context(_patch("_fetch_summary_context")).return_value = {
                "name": "F",
            }
            s.enter_context(mock.patch(
                "primeqa.intelligence.llm.gateway.llm_call",
                side_effect=LLMError("auth_error", "bad key"),
            ))
            mock_fail = s.enter_context(_patch("_mark_failed"))
            worker._summary_subtick(session, tenant_id=1)
        mock_fail.assert_called_once()
        assert mock_fail.call_args[0][2] is False  # auth_error → permanent

    def test_llm_rate_limited_is_retryable(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        session = mock.MagicMock()
        from primeqa.intelligence.llm.gateway import LLMError
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = [
                _claimed("q1", "e1", entity_type="Flow"),
            ]
            s.enter_context(_patch("_fetch_summary_context")).return_value = {
                "name": "F",
            }
            s.enter_context(mock.patch(
                "primeqa.intelligence.llm.gateway.llm_call",
                side_effect=LLMError("rate_limited", "slow down"),
            ))
            mock_fail = s.enter_context(_patch("_mark_failed"))
            worker._summary_subtick(session, tenant_id=1)
        assert mock_fail.call_args[0][2] is True  # rate_limited → retryable

    def test_entity_not_found_is_permanent(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        session = mock.MagicMock()
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = [
                _claimed("q1", "e1", entity_type="Flow"),
            ]
            s.enter_context(_patch("_fetch_summary_context")).return_value = None
            mock_fail = s.enter_context(_patch("_mark_failed"))
            worker._summary_subtick(session, tenant_id=1)
        assert mock_fail.call_args[0][2] is False

    def test_empty_summary_is_retryable(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        session = mock.MagicMock()
        resp = mock.MagicMock(parsed_content="   ", model="m",
                              prompt_version="v")
        with ExitStack() as s:
            s.enter_context(_patch("_reap_stalled"))
            s.enter_context(_patch("_claim_batch")).return_value = [
                _claimed("q1", "e1", entity_type="Flow"),
            ]
            s.enter_context(_patch("_fetch_summary_context")).return_value = {
                "name": "F",
            }
            s.enter_context(mock.patch(
                "primeqa.intelligence.llm.gateway.llm_call",
                return_value=resp,
            ))
            mock_fail = s.enter_context(_patch("_mark_failed"))
            worker._summary_subtick(session, tenant_id=1)
        assert mock_fail.call_args[0][2] is True


# ----------------------------------------------------------------------
# enrichment_tick — top-level orchestration
# ----------------------------------------------------------------------

class TestEnrichmentTick:
    def test_iterates_tenants_runs_both_subticks(self) -> None:
        sessions = [mock.MagicMock(), mock.MagicMock(), mock.MagicMock()]
        factory = mock.MagicMock(side_effect=sessions)
        with ExitStack() as s:
            s.enter_context(_patch("_discover_tenant_schemas")).return_value = [
                ("tenant_1", 1), ("tenant_2", 2),
            ]
            s.enter_context(_patch("_set_tenant_context"))
            # Patch readiness so we don't try to hit a real DB for the
            # signaling wiring — this test only verifies subtick fanout.
            s.enter_context(mock.patch.object(worker.readiness,
                                              "increment_run_counters"))
            s.enter_context(mock.patch.object(worker.readiness,
                                              "apply_org_status"))
            s.enter_context(mock.patch.object(worker.readiness,
                                              "maybe_finalize_run"))
            mock_emb = s.enter_context(_patch("_embedding_subtick"))
            mock_emb.return_value = worker.TickResult(claimed=0)
            mock_sum = s.enter_context(_patch("_summary_subtick"))
            mock_sum.return_value = worker.TickResult(claimed=0)
            result = worker.enrichment_tick(db_factory=factory)
        assert result is False  # no tenant did work
        # discovery session + one per tenant
        assert factory.call_count == 3
        assert mock_emb.call_count == 2
        assert mock_sum.call_count == 2

    def test_returns_true_when_any_tenant_did_work(self) -> None:
        factory = mock.MagicMock(side_effect=[mock.MagicMock(),
                                              mock.MagicMock()])
        with ExitStack() as s:
            s.enter_context(_patch("_discover_tenant_schemas")).return_value = [
                ("tenant_1", 1),
            ]
            s.enter_context(_patch("_set_tenant_context"))
            s.enter_context(mock.patch.object(worker.readiness,
                                              "increment_run_counters"))
            s.enter_context(mock.patch.object(worker.readiness,
                                              "apply_org_status"))
            s.enter_context(mock.patch.object(worker.readiness,
                                              "maybe_finalize_run"))
            s.enter_context(_patch("_embedding_subtick")).return_value = (
                worker.TickResult(claimed=10)
            )
            s.enter_context(_patch("_summary_subtick")).return_value = (
                worker.TickResult(claimed=0)
            )
            assert worker.enrichment_tick(db_factory=factory) is True

    def test_tenant_failure_isolated_others_continue(self) -> None:
        """An exception processing tenant 1 is caught + rolled back;
        tenant 2 still gets processed."""
        factory = mock.MagicMock(side_effect=[
            mock.MagicMock(), mock.MagicMock(), mock.MagicMock(),
        ])
        with ExitStack() as s:
            s.enter_context(_patch("_discover_tenant_schemas")).return_value = [
                ("tenant_1", 1), ("tenant_2", 2),
            ]
            s.enter_context(_patch("_set_tenant_context"))
            s.enter_context(mock.patch.object(worker.readiness,
                                              "increment_run_counters"))
            s.enter_context(mock.patch.object(worker.readiness,
                                              "apply_org_status"))
            s.enter_context(mock.patch.object(worker.readiness,
                                              "maybe_finalize_run"))
            mock_emb = s.enter_context(_patch("_embedding_subtick"))
            # tenant_1 raises; tenant_2 returns a working TickResult
            mock_emb.side_effect = [
                RuntimeError("boom"),
                worker.TickResult(claimed=3),
            ]
            s.enter_context(_patch("_summary_subtick")).return_value = (
                worker.TickResult(claimed=0)
            )
            result = worker.enrichment_tick(db_factory=factory)
        assert result is True  # tenant_2 still did work
        assert mock_emb.call_count == 2  # both attempted

    def test_no_tenants_returns_false(self) -> None:
        factory = mock.MagicMock(side_effect=[mock.MagicMock()])
        with ExitStack() as s:
            s.enter_context(_patch("_discover_tenant_schemas")).return_value = []
            assert worker.enrichment_tick(db_factory=factory) is False

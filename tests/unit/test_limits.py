"""Unit tests for primeqa.intelligence.llm.limits.

Focused on the §23 enrichment-worker extension — record_embedding_usage,
which logs an embedding batch to llm_usage_log so the existing per-
tenant call-count windows in check() count it. usage.record is mocked
(no DB).
"""
from __future__ import annotations

from unittest import mock

import pytest

from primeqa.intelligence.llm import limits

pytestmark = pytest.mark.unit


class TestRecordEmbeddingUsage:
    def test_logs_under_embedding_task_with_zero_cost(self) -> None:
        """One batch → one usage.record call, task=EMBEDDING_TASK,
        cost_usd=0.0, model passed through, prompt_version=model."""
        with mock.patch(
            "primeqa.intelligence.llm.usage.record", return_value=42,
        ) as mock_record:
            row_id = limits.record_embedding_usage(
                tenant_id=7, batch_size=128, model="voyage-3",
            )
        assert row_id == 42
        mock_record.assert_called_once()
        kw = mock_record.call_args.kwargs
        assert kw["tenant_id"] == 7
        assert kw["task"] == limits.EMBEDDING_TASK == "embedding_generation"
        assert kw["model"] == "voyage-3"
        assert kw["prompt_version"] == "voyage-3"
        assert kw["cost_usd"] == 0.0
        assert kw["status"] == "ok"

    def test_batch_size_lands_in_context(self) -> None:
        with mock.patch(
            "primeqa.intelligence.llm.usage.record", return_value=1,
        ) as mock_record:
            limits.record_embedding_usage(
                tenant_id=1, batch_size=72, model="voyage-3",
            )
        ctx = mock_record.call_args.kwargs["context"]
        assert ctx["batch_size"] == 72

    def test_extra_context_is_merged(self) -> None:
        with mock.patch(
            "primeqa.intelligence.llm.usage.record", return_value=1,
        ) as mock_record:
            limits.record_embedding_usage(
                tenant_id=1, batch_size=10, model="voyage-3",
                context={"primitive": "embedding", "entity_type": "Field"},
            )
        ctx = mock_record.call_args.kwargs["context"]
        assert ctx["batch_size"] == 10
        assert ctx["primitive"] == "embedding"
        assert ctx["entity_type"] == "Field"

    def test_latency_passed_through(self) -> None:
        with mock.patch(
            "primeqa.intelligence.llm.usage.record", return_value=1,
        ) as mock_record:
            limits.record_embedding_usage(
                tenant_id=1, batch_size=5, model="voyage-3",
                latency_ms=234,
            )
        assert mock_record.call_args.kwargs["latency_ms"] == 234

    def test_returns_none_when_usage_record_returns_none(self) -> None:
        """usage.record is fire-and-forget — returns None on write
        failure. record_embedding_usage propagates that."""
        with mock.patch(
            "primeqa.intelligence.llm.usage.record", return_value=None,
        ):
            assert limits.record_embedding_usage(
                tenant_id=1, batch_size=5, model="voyage-3",
            ) is None


class TestRecordEmbeddingUsageCost:
    """1d cost-telemetry: tokens -> measured cost + sync_run_id passthrough;
    missing tokens -> loud marker, NOT a fabricated measured-zero."""

    def test_tokens_compute_cost_from_the_rate(self) -> None:
        from primeqa.intelligence import embeddings as E
        from primeqa.intelligence.embeddings import (
            VOYAGE_3_USD_PER_1M_TOKENS, voyage_embedding_cost_usd,
        )
        with mock.patch(
            "primeqa.intelligence.llm.usage.record", return_value=9,
        ) as mock_record:
            limits.record_embedding_usage(
                tenant_id=3, batch_size=10, model="voyage-3",
                tokens=2_000_000, sync_run_id="run-abc",
            )
        kw = mock_record.call_args.kwargs
        assert kw["input_tokens"] == 2_000_000
        # cost is derived from the constant, never hard-coded
        assert kw["cost_usd"] == voyage_embedding_cost_usd(2_000_000)
        assert kw["cost_usd"] == 2 * VOYAGE_3_USD_PER_1M_TOKENS
        assert kw["sync_run_id"] == "run-abc"
        # a real measurement is NOT flagged as missing
        assert "tokens_missing" not in kw["context"]
        # but a cost from the unconfirmed PLACEHOLDER rate IS flagged provisional
        # (gated on the flag so this survives AK confirming the rate later)
        if not E.VOYAGE_3_RATE_CONFIRMED:
            assert kw["context"].get("rate_provisional") is True
        else:
            assert "rate_provisional" not in kw["context"]

    def test_missing_tokens_marks_not_fabricates(self) -> None:
        """tokens=None -> recorded for the call-count window but with
        input_tokens=0, cost 0.0, and a tokens_missing marker (no silent
        fabricated zero)."""
        with mock.patch(
            "primeqa.intelligence.llm.usage.record", return_value=1,
        ) as mock_record:
            limits.record_embedding_usage(
                tenant_id=3, batch_size=4, model="voyage-3",
                tokens=None, sync_run_id="run-xyz",
            )
        kw = mock_record.call_args.kwargs
        assert kw["input_tokens"] == 0
        assert kw["cost_usd"] == 0.0
        assert kw["context"]["tokens_missing"] is True
        # no rate was applied (no tokens) -> not flagged provisional
        assert "rate_provisional" not in kw["context"]
        # sync_run_id is still attributed even when tokens are unmeasured
        assert kw["sync_run_id"] == "run-xyz"

    def test_zero_tokens_is_a_measured_zero_not_missing(self) -> None:
        """tokens=0 (legitimately empty) -> cost 0.0 but NOT marked missing."""
        with mock.patch(
            "primeqa.intelligence.llm.usage.record", return_value=1,
        ) as mock_record:
            limits.record_embedding_usage(
                tenant_id=3, batch_size=1, model="voyage-3", tokens=0,
            )
        kw = mock_record.call_args.kwargs
        assert kw["input_tokens"] == 0
        assert kw["cost_usd"] == 0.0
        assert "tokens_missing" not in kw["context"]

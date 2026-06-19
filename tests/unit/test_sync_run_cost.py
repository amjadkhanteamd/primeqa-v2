"""Unit: the S1 sync cost roll-up (1d, migration 058).

``get_sync_run_cost`` sums llm_usage_log by sync_run_id into an embedding +
summary cost roll-up. The SQL conditional-bucketing runs against Postgres, so
it is integration-covered (tests/test_sync_run_cost_integration.py); here we
unit-test the PURE shaper ``_shape_sync_run_cost`` over a representative
post-aggregation row (mixed embedding + summary + an other-task contribution to
total), plus the None/zero contract and the set_sync_run guard. No DB.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from primeqa.intelligence.llm import usage

pytestmark = pytest.mark.unit


class TestShapeSyncRunCost:
    def test_none_row_is_the_zero_rollup(self) -> None:
        out = usage._shape_sync_run_cost("run-1", None)
        assert out["sync_run_id"] == "run-1"
        assert out["embedding"] == {"tokens": 0, "cost_usd": 0.0, "rows": 0}
        assert out["summary"] == {"tokens": 0, "cost_usd": 0.0, "rows": 0}
        assert out["total_cost_usd"] == 0.0
        assert out["tokens_missing_rows"] == 0
        assert out["rate_provisional_rows"] == 0

    def test_mixed_embedding_and_summary_row(self) -> None:
        # Simulates the SQL conditional-sum output: 3 embedding rows, 2 summary
        # rows, and a total that EXCEEDS emb+sum cost (an other-task row counted
        # toward total only) — proving total comes from the SQL total column,
        # not a Python re-sum of the two buckets. NUMERIC -> Decimal coercion.
        row = {
            "emb_tokens": 1500, "emb_cost": Decimal("0.000150"), "emb_rows": 3,
            "sum_tokens": 800, "sum_cost": Decimal("0.024000"), "sum_rows": 2,
            "total_cost": Decimal("0.030000"),   # > emb+sum: an other-task row
            "missing_rows": 1, "provisional_rows": 2,
        }
        out = usage._shape_sync_run_cost("run-7", row)
        assert out["embedding"] == {"tokens": 1500, "cost_usd": 0.000150, "rows": 3}
        assert out["summary"] == {"tokens": 800, "cost_usd": 0.024, "rows": 2}
        assert out["total_cost_usd"] == 0.03
        assert out["tokens_missing_rows"] == 1
        assert out["rate_provisional_rows"] == 2
        # all costs are plain floats, all counts plain ints
        assert isinstance(out["embedding"]["cost_usd"], float)
        assert isinstance(out["summary"]["tokens"], int)
        assert isinstance(out["total_cost_usd"], float)

    def test_null_aggregates_are_none_safe(self) -> None:
        # COALESCE makes this unlikely, but the shaper must not crash on a
        # partial/None-bearing row.
        out = usage._shape_sync_run_cost("run-9", {"emb_tokens": None,
                                                   "total_cost": None})
        assert out["embedding"]["tokens"] == 0
        assert out["total_cost_usd"] == 0.0


class TestGetSyncRunCostContract:
    def test_falsy_sync_run_id_returns_zero_without_db(self) -> None:
        # None short-circuits before any Session is opened.
        out = usage.get_sync_run_cost(None)
        assert out["sync_run_id"] is None
        assert out["total_cost_usd"] == 0.0
        assert out["embedding"]["rows"] == 0


class TestSetSyncRunGuard:
    def test_missing_ids_are_a_noop(self) -> None:
        # No DB touched when either id is missing.
        assert usage.set_sync_run(None, "run-1") is False
        assert usage.set_sync_run(5, None) is False
        assert usage.set_sync_run(None, None) is False


class TestSetSyncRunManyGuard:
    def test_empty_or_missing_is_a_noop(self) -> None:
        # No DB touched when no ids or no sync_run_id (covers the escalation
        # back-link path: all attempt ids attributed, but a no-op when empty).
        assert usage.set_sync_run_many([], "run-1") == 0
        assert usage.set_sync_run_many(None, "run-1") == 0
        assert usage.set_sync_run_many([1, 2], None) == 0
        assert usage.set_sync_run_many([None, 0], "run-1") == 0  # all falsy filtered

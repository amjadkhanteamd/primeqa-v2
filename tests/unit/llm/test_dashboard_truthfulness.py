"""D-391 pins: the analytics layer reads the LIVE task name and reports
absence, never fabricated zeros.

Source-level pins (the s3_generation_console attribution-pin style) plus the
pure unmeasured shapes — no DB.
"""
from __future__ import annotations

import inspect


def test_dashboard_uses_the_live_generation_task_name():
    # The v1 name 'test_plan_generation' matched ZERO rows ever (verified live,
    # D-391) and rendered "0 generations / $0.000000" through divide guards.
    from primeqa.intelligence.llm import dashboard
    src = inspect.getsource(dashboard)
    assert "task = 'generation'" in src
    # The docstring may name the old literal in prose; the SQL must not.
    sql_chunks = [c for c in src.split('"""') if "WITH gen AS" in c
                  or "cache_by_tenant AS" in c]
    assert sql_chunks, "the efficiency SQL block must exist"
    for chunk in sql_chunks:
        assert "test_plan_generation" not in chunk
        assert "agent_fix" not in chunk


def test_quality_proxy_reports_unmeasured_shape():
    from primeqa.intelligence.llm import dashboard
    q = dashboard.quality_proxy_summary(None, days=7)
    assert q == {"days": 7, "measured": False, "reason": q["reason"]}
    assert q["reason"]
    # The old fabricated keys must be gone — a template reading them should
    # fail loudly in dev, not render 0.0% as if measured.
    for dead in ("regeneration_rate", "post_gen_failure_rate", "total_tcs"):
        assert dead not in q


def test_efficiency_absence_semantics_are_none_not_zero():
    # The empty-denominator branches must yield None (absence), never 0.0.
    # Pin the exact guard expressions so a revert to `else 0.0` fails here.
    from primeqa.intelligence.llm import dashboard
    src = inspect.getsource(dashboard.efficiency_summary)
    assert "if runs else None" in src
    assert "if esc_total else None" in src
    assert "if err_total else None" in src
    assert "else 0.0" not in src

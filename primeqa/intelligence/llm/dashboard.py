"""Queries that power /settings/llm-usage superadmin dashboard.

Three views matter in practice (the architect's callout):
  1. Cost control   \u2014 who spent what, per feature, per test case
  2. Efficiency     \u2014 cache hit rate, cost per generation, escalation rate
  3. Quality proxy  \u2014 regeneration rate, post-gen failure rate

Everything here is a read-only aggregate over llm_usage_log. No caching,
no materialized views yet; at tens of thousands of rows Postgres returns
< 100ms for every query thanks to the indexes added in migration 031.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def _window(days: int):
    now = datetime.now(timezone.utc)
    return now - timedelta(days=days), now


def cost_summary(db, *, days: int = 30) -> Dict[str, Any]:
    """Totals + per-task + per-model + per-tenant rollups for the window.

    Returns (all dicts keyed by name \u2192 {count, input_tokens,
    output_tokens, cached_input_tokens, cost_usd}):
      total
      by_task     (e.g. "test_plan_generation": {...})
      by_model
      by_tenant   (top 20 by spend)
      by_day      list of dicts {day, cost_usd, calls}  (newest last)
    """
    from sqlalchemy import text as sql

    start, _end = _window(days)

    def _agg(group_col: str, limit: Optional[int] = None, where_extra: str = "") -> List[Dict[str, Any]]:
        lim = f"LIMIT {limit}" if limit else ""
        q = sql(f"""
            SELECT {group_col} AS key,
                   COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(cached_input_tokens),0) AS cached_tokens,
                   COALESCE(SUM(cost_usd),0)::float AS cost_usd
            FROM llm_usage_log
            WHERE ts >= :start AND status = 'ok' {where_extra}
            GROUP BY {group_col}
            ORDER BY cost_usd DESC
            {lim}
        """)
        return [dict(row._mapping) for row in db.execute(q, {"start": start})]

    # total
    total_row = db.execute(sql("""
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(input_tokens),0) AS input_tokens,
               COALESCE(SUM(output_tokens),0) AS output_tokens,
               COALESCE(SUM(cached_input_tokens),0) AS cached_tokens,
               COALESCE(SUM(cost_usd),0)::float AS cost_usd
        FROM llm_usage_log
        WHERE ts >= :start AND status = 'ok'
    """), {"start": start}).one()._mapping

    # per-day series
    by_day_rows = db.execute(sql("""
        SELECT DATE(ts) AS day,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd),0)::float AS cost_usd
        FROM llm_usage_log
        WHERE ts >= :start AND status = 'ok'
        GROUP BY DATE(ts)
        ORDER BY DATE(ts) ASC
    """), {"start": start}).all()
    by_day = [
        {"day": r._mapping["day"].isoformat(),
         "calls": r._mapping["calls"],
         "cost_usd": r._mapping["cost_usd"]}
        for r in by_day_rows
    ]

    return {
        "days": days,
        "total": dict(total_row),
        "by_task": _agg("task"),
        "by_model": _agg("model"),
        "by_tenant": _agg("tenant_id", limit=20),
        "by_day": by_day,
    }


def efficiency_summary(db, *, days: int = 30) -> Dict[str, Any]:
    """Cache hit rate, cost per generation, escalation rate."""
    from sqlalchemy import text as sql

    start, _end = _window(days)

    # Cache hit rate (across tasks with caching enabled: currently
    # test_plan_generation only). We define a "hit" as a call where
    # cached_input_tokens > 0.
    cache_row = db.execute(sql("""
        SELECT COUNT(*) AS calls,
               SUM(CASE WHEN cached_input_tokens > 0 THEN 1 ELSE 0 END) AS hits,
               COALESCE(SUM(cached_input_tokens),0) AS cached_tokens_total,
               COALESCE(SUM(input_tokens),0) AS uncached_input_total
        FROM llm_usage_log
        WHERE ts >= :start AND status = 'ok'
          AND task = 'test_plan_generation'
    """), {"start": start}).one()._mapping

    total_gen = cache_row["calls"] or 0
    hits = cache_row["hits"] or 0
    cache_hit_rate = (hits / total_gen) if total_gen else 0.0

    # Cost per generation
    cost_row = db.execute(sql("""
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(cost_usd),0)::float AS cost_usd
        FROM llm_usage_log
        WHERE ts >= :start AND status = 'ok'
          AND task = 'test_plan_generation'
    """), {"start": start}).one()._mapping
    avg_cost_per_gen = (cost_row["cost_usd"] / cost_row["calls"]) if cost_row["calls"] else 0.0

    # Escalation rate (how often did test_plan_generation or agent_fix
    # retry on the fallback model)
    esc_row = db.execute(sql("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN escalated THEN 1 ELSE 0 END) AS escalated
        FROM llm_usage_log
        WHERE ts >= :start AND status = 'ok'
          AND task IN ('test_plan_generation','agent_fix')
    """), {"start": start}).one()._mapping
    escalation_rate = (
        (esc_row["escalated"] or 0) / esc_row["total"]
    ) if esc_row["total"] else 0.0

    # Error rate (non-ok calls over total calls)
    err_row = db.execute(sql("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS errors
        FROM llm_usage_log
        WHERE ts >= :start
    """), {"start": start}).one()._mapping
    error_rate = (err_row["errors"] / err_row["total"]) if err_row["total"] else 0.0

    # Top error types
    top_errors = db.execute(sql("""
        SELECT status, COUNT(*) AS n
        FROM llm_usage_log
        WHERE ts >= :start AND status <> 'ok'
        GROUP BY status
        ORDER BY n DESC
        LIMIT 10
    """), {"start": start}).all()

    return {
        "days": days,
        "cache_hit_rate": round(cache_hit_rate, 3),
        "cache_hits": hits,
        "cache_total_calls": total_gen,
        "avg_cost_per_generation_usd": round(avg_cost_per_gen, 6),
        "generations": cost_row["calls"],
        "escalation_rate": round(escalation_rate, 3),
        "error_rate": round(error_rate, 3),
        "top_errors": [{"status": r._mapping["status"], "n": r._mapping["n"]}
                       for r in top_errors],
    }


def quality_proxy_summary(db, *, days: int = 30) -> Dict[str, Any]:
    """Quality proxy metrics: regeneration rate, post-gen failure rate.

    This is the architect's "very important" callout. Generation cost is
    wasted if users immediately regenerate or if the generated TCs all
    fail at runtime.
    """
    from sqlalchemy import text as sql

    start, _end = _window(days)

    # How many generation batches produced drafts that were superseded
    # within 15 minutes by the same user?
    regen_row = db.execute(sql("""
        WITH gens AS (
          SELECT id, tenant_id, requirement_id, created_by, created_at
          FROM generation_batches
          WHERE created_at >= :start
        )
        SELECT COUNT(*) FILTER (
          WHERE EXISTS (
            SELECT 1 FROM generation_batches g2
            WHERE g2.tenant_id = gens.tenant_id
              AND g2.requirement_id = gens.requirement_id
              AND g2.created_by = gens.created_by
              AND g2.created_at > gens.created_at
              AND g2.created_at < gens.created_at + INTERVAL '15 minutes'
          )
        ) AS regenerated_within_15m,
        COUNT(*) AS total_generations
        FROM gens
    """), {"start": start}).one()._mapping

    regen_rate = (
        (regen_row["regenerated_within_15m"] or 0)
        / regen_row["total_generations"]
    ) if regen_row["total_generations"] else 0.0

    # Validation-critical rate: how often does a generation produce a TC
    # with status=critical in its validation_report?
    validation_row = db.execute(sql("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN validation_report->>'status' = 'critical'
                        THEN 1 ELSE 0 END) AS critical
        FROM test_case_versions
        WHERE created_at >= :start
          AND generation_method IN ('ai', 'regenerated')
          AND validation_report IS NOT NULL
    """), {"start": start}).one()._mapping

    validation_critical_rate = (
        (validation_row["critical"] or 0) / validation_row["total"]
    ) if validation_row["total"] else 0.0

    # Post-gen failure rate: of TCs generated in window, what % failed
    # at least once on execution?
    # test_cases has no created_at; we use updated_at as a proxy (TCs
    # are rarely edited post-generation, so updated_at \u2248 created_at
    # for AI-generated TCs).
    exec_fail_row = db.execute(sql("""
        WITH recent_tcs AS (
          SELECT DISTINCT tc.id AS test_case_id
          FROM test_cases tc
          WHERE tc.updated_at >= :start
            AND tc.deleted_at IS NULL
            AND tc.generation_batch_id IS NOT NULL
        )
        SELECT COUNT(*) FILTER (
          WHERE EXISTS (
            SELECT 1 FROM run_test_results r
            WHERE r.test_case_id = recent_tcs.test_case_id
              AND r.status IN ('failed', 'error')
          )
        ) AS failed_at_least_once,
        COUNT(*) AS total
        FROM recent_tcs
    """), {"start": start}).one()._mapping

    fail_rate = (
        (exec_fail_row["failed_at_least_once"] or 0) / exec_fail_row["total"]
    ) if exec_fail_row["total"] else 0.0

    return {
        "days": days,
        "regeneration_rate": round(regen_rate, 3),
        "regenerated_within_15m": regen_row["regenerated_within_15m"] or 0,
        "total_generations": regen_row["total_generations"] or 0,
        "validation_critical_rate": round(validation_critical_rate, 3),
        "validations_critical": validation_row["critical"] or 0,
        "validations_total": validation_row["total"] or 0,
        "post_gen_failure_rate": round(fail_rate, 3),
        "failed_tcs": exec_fail_row["failed_at_least_once"] or 0,
        "total_tcs": exec_fail_row["total"] or 0,
    }


def tenant_feedback_summary(db, tenant_id: int, *, days: int = 30) -> Dict[str, Any]:
    """Per-tenant feedback counts for the `/settings/my-llm-usage` dashboard.

    Returns a dict with:
      counts    — dict keyed by signal_type → int
      by_day    — list of {day, counts: {...}}  for the trend chart
      top_issues — top-5 recurring rule groups (from feedback_rules)
      correction_rate — the north-star dict from feedback_rules

    All queries hit `generation_quality_signals` and `test_cases` —
    indexed on (tenant_id, captured_at desc) so <100ms even at scale.
    """
    from sqlalchemy import text as sql
    from primeqa.intelligence.llm import feedback_rules

    start, _end = _window(days)

    counts_rows = db.execute(sql("""
        SELECT signal_type, COUNT(*)::int AS n
        FROM generation_quality_signals
        WHERE tenant_id = :tid AND captured_at >= :start
        GROUP BY signal_type
    """), {"tid": tenant_id, "start": start}).all()
    counts = {r._mapping["signal_type"]: r._mapping["n"] for r in counts_rows}

    # Per-day series — one row per (day, signal_type). Aggregate into
    # {day: {signal_type: n}} client-side (small result set).
    by_day_rows = db.execute(sql("""
        SELECT DATE(captured_at) AS day, signal_type, COUNT(*)::int AS n
        FROM generation_quality_signals
        WHERE tenant_id = :tid AND captured_at >= :start
        GROUP BY DATE(captured_at), signal_type
        ORDER BY DATE(captured_at) ASC
    """), {"tid": tenant_id, "start": start}).all()
    by_day_map: Dict[str, Dict[str, int]] = {}
    for r in by_day_rows:
        day = r._mapping["day"].isoformat()
        by_day_map.setdefault(day, {})[r._mapping["signal_type"]] = r._mapping["n"]
    by_day = [{"day": d, "counts": c} for d, c in sorted(by_day_map.items())]

    top_issues = feedback_rules.top_recurring_issues(tenant_id, window_days=days)
    correction = feedback_rules.correction_rate(db, tenant_id, days=days)

    return {
        "days": days,
        "counts": counts,
        "by_day": by_day,
        "top_issues": top_issues,
        "correction_rate": correction,
    }


def tenant_summary(db, tenant_id: int, *, days: int = 30) -> Dict[str, Any]:
    """Per-tenant view — same shape as cost_summary/efficiency_summary
    merged into one dict, but filtered to one tenant.

    Drives /settings/my-llm-usage (visible to admin, not just superadmin).
    Keeps the template simple — one dict instead of three.
    """
    from sqlalchemy import text as sql

    start, _end = _window(days)

    total = db.execute(sql("""
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(input_tokens),0) AS input_tokens,
               COALESCE(SUM(output_tokens),0) AS output_tokens,
               COALESCE(SUM(cached_input_tokens),0) AS cached_tokens,
               COALESCE(SUM(cost_usd),0)::float AS cost_usd
        FROM llm_usage_log
        WHERE tenant_id = :tid AND ts >= :start AND status = 'ok'
    """), {"tid": tenant_id, "start": start}).one()._mapping

    by_task = db.execute(sql("""
        SELECT task AS key,
               COUNT(*) AS calls,
               COALESCE(SUM(input_tokens),0) AS input_tokens,
               COALESCE(SUM(output_tokens),0) AS output_tokens,
               COALESCE(SUM(cached_input_tokens),0) AS cached_tokens,
               COALESCE(SUM(cost_usd),0)::float AS cost_usd
        FROM llm_usage_log
        WHERE tenant_id = :tid AND ts >= :start AND status = 'ok'
        GROUP BY task
        ORDER BY cost_usd DESC
    """), {"tid": tenant_id, "start": start}).all()

    by_day_rows = db.execute(sql("""
        SELECT DATE(ts) AS day,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd),0)::float AS cost_usd
        FROM llm_usage_log
        WHERE tenant_id = :tid AND ts >= :start AND status = 'ok'
        GROUP BY DATE(ts)
        ORDER BY DATE(ts) ASC
    """), {"tid": tenant_id, "start": start}).all()

    # A friendly number: calls that were actually blocked because the
    # tenant hit a cap. Surfaces "you've been throttled N times" — the
    # single number every customer wants on the upgrade page.
    blocked_calls = db.execute(sql("""
        SELECT COUNT(*) AS n
        FROM llm_usage_log
        WHERE tenant_id = :tid AND ts >= :start AND status = 'rate_limited'
    """), {"tid": tenant_id, "start": start}).scalar() or 0

    return {
        "days": days,
        "total": dict(total),
        "by_task": [dict(r._mapping) for r in by_task],
        "by_day": [
            {"day": r._mapping["day"].isoformat(),
             "calls": r._mapping["calls"],
             "cost_usd": r._mapping["cost_usd"]}
            for r in by_day_rows
        ],
        "blocked_calls": int(blocked_calls),
    }


def top_spenders(db, *, days: int = 30, limit: int = 10) -> List[Dict[str, Any]]:
    """Top users by LLM spend in the window. For the superadmin dashboard."""
    from sqlalchemy import text as sql

    start, _end = _window(days)
    rows = db.execute(sql("""
        SELECT u.email,
               u.tenant_id,
               COUNT(l.id) AS calls,
               COALESCE(SUM(l.cost_usd),0)::float AS cost_usd
        FROM llm_usage_log l
        JOIN users u ON u.id = l.user_id
        WHERE l.ts >= :start AND l.status = 'ok'
        GROUP BY u.email, u.tenant_id
        ORDER BY cost_usd DESC
        LIMIT :limit
    """), {"start": start, "limit": limit}).all()
    return [dict(r._mapping) for r in rows]

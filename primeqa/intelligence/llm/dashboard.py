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

    Audit A.4 (2026-04-19): previously 5 round-trips (total, by_day,
    by_task, by_model, by_tenant). Over Railway's ~650ms RTT that's
    ~3.3s of pure network. Now one SELECT with sub-aggregates returning
    json_agg arrays — Postgres does the work internally for ~650ms.

    Returns same shape as before:
      total       dict {calls, input_tokens, output_tokens, cached_tokens, cost_usd}
      by_task     list of dicts (same shape + key=task)
      by_model    list of dicts (same shape + key=model)
      by_tenant   list of dicts (same shape + key=tenant_id), top 20
      by_day      list of dicts {day, calls, cost_usd}
    """
    from sqlalchemy import text as sql

    start, _end = _window(days)

    row = db.execute(sql("""
        WITH ok_calls AS (
          SELECT input_tokens, output_tokens, cached_input_tokens,
                 cost_usd, task, model, tenant_id, ts
          FROM llm_usage_log
          WHERE ts >= :start AND status = 'ok'
        ),
        totals AS (
          SELECT COUNT(*)                              AS calls,
                 COALESCE(SUM(input_tokens), 0)        AS input_tokens,
                 COALESCE(SUM(output_tokens), 0)       AS output_tokens,
                 COALESCE(SUM(cached_input_tokens), 0) AS cached_tokens,
                 COALESCE(SUM(cost_usd), 0)::float     AS cost_usd
          FROM ok_calls
        ),
        by_task AS (
          SELECT task AS key,
                 COUNT(*) AS calls,
                 COALESCE(SUM(input_tokens), 0)        AS input_tokens,
                 COALESCE(SUM(output_tokens), 0)       AS output_tokens,
                 COALESCE(SUM(cached_input_tokens), 0) AS cached_tokens,
                 COALESCE(SUM(cost_usd), 0)::float     AS cost_usd
          FROM ok_calls
          GROUP BY task
          ORDER BY cost_usd DESC
        ),
        by_model AS (
          SELECT model AS key,
                 COUNT(*) AS calls,
                 COALESCE(SUM(input_tokens), 0)        AS input_tokens,
                 COALESCE(SUM(output_tokens), 0)       AS output_tokens,
                 COALESCE(SUM(cached_input_tokens), 0) AS cached_tokens,
                 COALESCE(SUM(cost_usd), 0)::float     AS cost_usd
          FROM ok_calls
          GROUP BY model
          ORDER BY cost_usd DESC
        ),
        by_tenant AS (
          SELECT tenant_id AS key,
                 COUNT(*) AS calls,
                 COALESCE(SUM(input_tokens), 0)        AS input_tokens,
                 COALESCE(SUM(output_tokens), 0)       AS output_tokens,
                 COALESCE(SUM(cached_input_tokens), 0) AS cached_tokens,
                 COALESCE(SUM(cost_usd), 0)::float     AS cost_usd
          FROM ok_calls
          GROUP BY tenant_id
          ORDER BY cost_usd DESC
          LIMIT 20
        ),
        by_day AS (
          SELECT DATE(ts) AS day,
                 COUNT(*) AS calls,
                 COALESCE(SUM(cost_usd), 0)::float AS cost_usd
          FROM ok_calls
          GROUP BY DATE(ts)
          ORDER BY DATE(ts) ASC
        )
        SELECT
          (SELECT row_to_json(t) FROM totals t)                        AS total,
          COALESCE((SELECT json_agg(b) FROM by_task b), '[]'::json)    AS by_task,
          COALESCE((SELECT json_agg(b) FROM by_model b), '[]'::json)   AS by_model,
          COALESCE((SELECT json_agg(b) FROM by_tenant b), '[]'::json)  AS by_tenant,
          COALESCE((SELECT json_agg(d) FROM by_day d), '[]'::json)     AS by_day
    """), {"start": start}).one()._mapping

    total = row["total"] or {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "cached_tokens": 0, "cost_usd": 0.0,
    }

    return {
        "days": days,
        "total": dict(total),
        "by_task": list(row["by_task"] or []),
        "by_model": list(row["by_model"] or []),
        "by_tenant": list(row["by_tenant"] or []),
        "by_day": list(row["by_day"] or []),
    }


def efficiency_summary(db, *, days: int = 30) -> Dict[str, Any]:
    """Cache hit rate, cost per generation, escalation rate.

    Audit A.4: 5 queries → 1 via CTEs. All sub-metrics scope over the
    same window + rows so a single SELECT with conditional aggregates
    is strictly better.
    """
    from sqlalchemy import text as sql

    start, _end = _window(days)

    row = db.execute(sql("""
        WITH ok AS (
          SELECT cached_input_tokens, input_tokens, cost_usd, task,
                 escalated, status
          FROM llm_usage_log
          WHERE ts >= :start AND status = 'ok'
        ),
        all_calls AS (
          SELECT status FROM llm_usage_log WHERE ts >= :start
        ),
        cache_stats AS (
          SELECT COUNT(*) AS calls,
                 SUM(CASE WHEN cached_input_tokens > 0 THEN 1 ELSE 0 END) AS hits,
                 COALESCE(SUM(cost_usd), 0)::float AS cost_usd
          FROM ok
          WHERE task = 'test_plan_generation'
        ),
        escalation_stats AS (
          SELECT COUNT(*) AS total,
                 SUM(CASE WHEN escalated THEN 1 ELSE 0 END) AS escalated
          FROM ok
          WHERE task IN ('test_plan_generation', 'agent_fix')
        ),
        error_stats AS (
          SELECT COUNT(*) AS total,
                 SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS errors
          FROM all_calls
        ),
        top_errors AS (
          SELECT status, COUNT(*) AS n
          FROM llm_usage_log
          WHERE ts >= :start AND status <> 'ok'
          GROUP BY status
          ORDER BY n DESC
          LIMIT 10
        )
        SELECT
          (SELECT calls    FROM cache_stats)       AS gen_calls,
          (SELECT hits     FROM cache_stats)       AS cache_hits,
          (SELECT cost_usd FROM cache_stats)       AS gen_cost_usd,
          (SELECT total    FROM escalation_stats)  AS esc_total,
          (SELECT escalated FROM escalation_stats) AS esc_hits,
          (SELECT total    FROM error_stats)       AS err_total,
          (SELECT errors   FROM error_stats)       AS err_hits,
          COALESCE((SELECT json_agg(t) FROM top_errors t), '[]'::json) AS top_errors
    """), {"start": start}).one()._mapping

    total_gen = int(row["gen_calls"] or 0)
    hits = int(row["cache_hits"] or 0)
    gen_cost = float(row["gen_cost_usd"] or 0.0)
    cache_hit_rate = (hits / total_gen) if total_gen else 0.0
    avg_cost_per_gen = (gen_cost / total_gen) if total_gen else 0.0

    esc_total = int(row["esc_total"] or 0)
    esc_hits = int(row["esc_hits"] or 0)
    escalation_rate = (esc_hits / esc_total) if esc_total else 0.0

    err_total = int(row["err_total"] or 0)
    err_hits = int(row["err_hits"] or 0)
    error_rate = (err_hits / err_total) if err_total else 0.0

    return {
        "days": days,
        "cache_hit_rate": round(cache_hit_rate, 3),
        "cache_hits": hits,
        "cache_total_calls": total_gen,
        "avg_cost_per_generation_usd": round(avg_cost_per_gen, 6),
        "generations": total_gen,
        "escalation_rate": round(escalation_rate, 3),
        "error_rate": round(error_rate, 3),
        "top_errors": [dict(e) for e in (row["top_errors"] or [])],
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

    # Audit U2: merge counts + by_day into one round-trip.
    row = db.execute(sql("""
        WITH win AS (
          SELECT signal_type, captured_at
          FROM generation_quality_signals
          WHERE tenant_id = :tid AND captured_at >= :start
        ),
        totals AS (
          SELECT signal_type, COUNT(*)::int AS n FROM win GROUP BY signal_type
        ),
        per_day AS (
          SELECT DATE(captured_at) AS day, signal_type, COUNT(*)::int AS n
          FROM win
          GROUP BY DATE(captured_at), signal_type
          ORDER BY DATE(captured_at) ASC
        )
        SELECT
          COALESCE((SELECT json_agg(t) FROM totals t), '[]'::json) AS counts,
          COALESCE((SELECT json_agg(d) FROM per_day d), '[]'::json) AS by_day
    """), {"tid": tenant_id, "start": start}).one()._mapping

    counts = {c["signal_type"]: c["n"] for c in (row["counts"] or [])}
    by_day_map: Dict[str, Dict[str, int]] = {}
    for d in row["by_day"] or []:
        by_day_map.setdefault(d["day"], {})[d["signal_type"]] = d["n"]
    by_day = [{"day": d, "counts": c} for d, c in sorted(by_day_map.items())]

    top_issues = feedback_rules.top_recurring_issues(tenant_id, window_days=days, db=db)
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

    # Audit U2 (2026-04-19): previously four separate round-trips. Over
    # Railway's ~650ms RTT that was ~2.6s of pure network. Now one
    # SELECT with two sub-aggregates; Postgres handles the extra work
    # internally for ~650ms.
    #
    # We return: totals (single row) + by_task (array) + by_day (array)
    # + blocked_calls (single int).
    row = db.execute(sql("""
        WITH ok_calls AS (
          SELECT input_tokens, output_tokens, cached_input_tokens,
                 cost_usd, task, ts
          FROM llm_usage_log
          WHERE tenant_id = :tid AND ts >= :start AND status = 'ok'
        ),
        totals AS (
          SELECT COUNT(*)                            AS calls,
                 COALESCE(SUM(input_tokens), 0)      AS input_tokens,
                 COALESCE(SUM(output_tokens), 0)     AS output_tokens,
                 COALESCE(SUM(cached_input_tokens), 0) AS cached_tokens,
                 COALESCE(SUM(cost_usd), 0)::float   AS cost_usd
          FROM ok_calls
        ),
        by_task AS (
          SELECT task AS key,
                 COUNT(*) AS calls,
                 COALESCE(SUM(input_tokens), 0)      AS input_tokens,
                 COALESCE(SUM(output_tokens), 0)     AS output_tokens,
                 COALESCE(SUM(cached_input_tokens), 0) AS cached_tokens,
                 COALESCE(SUM(cost_usd), 0)::float   AS cost_usd
          FROM ok_calls
          GROUP BY task
          ORDER BY cost_usd DESC
        ),
        by_day AS (
          SELECT DATE(ts) AS day,
                 COUNT(*) AS calls,
                 COALESCE(SUM(cost_usd), 0)::float AS cost_usd
          FROM ok_calls
          GROUP BY DATE(ts)
          ORDER BY DATE(ts) ASC
        )
        SELECT
          (SELECT row_to_json(t) FROM totals t) AS total,
          COALESCE((SELECT json_agg(b) FROM by_task b), '[]'::json) AS by_task,
          COALESCE((SELECT json_agg(d) FROM by_day d), '[]'::json) AS by_day,
          (SELECT COUNT(*)::int
             FROM llm_usage_log
             WHERE tenant_id = :tid AND ts >= :start
               AND status = 'rate_limited') AS blocked_calls
    """), {"tid": tenant_id, "start": start}).one()._mapping

    total = row["total"] or {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "cached_tokens": 0, "cost_usd": 0.0,
    }
    by_task = row["by_task"] or []
    # by_day: day is a date; json_agg serialises it as ISO string, good.
    by_day_raw = row["by_day"] or []
    blocked_calls = int(row["blocked_calls"] or 0)

    return {
        "days": days,
        "total": dict(total),
        "by_task": list(by_task),
        "by_day": [
            # json_agg returns day as ISO-string already, just normalise
            {"day": d.get("day"),
             "calls": d.get("calls", 0),
             "cost_usd": d.get("cost_usd", 0.0)}
            for d in by_day_raw
        ],
        "blocked_calls": blocked_calls,
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

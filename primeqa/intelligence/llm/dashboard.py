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

    D-391 (2026-07-25): this used to filter `task = 'test_plan_generation'` —
    the v1 task name, under which ZERO rows have ever existed (verified against
    the live log) — then render the resulting 0/0 through divide-guard
    fallbacks as "0 generations / $0.000000 / 0% cache hit rate". The live S3
    path logs `task = 'generation'` (run.py GENERATION_TASK); the v1 name is
    not matched alongside it because no row ever carried it.

    Truthfulness rules (the s3_generation_console llm=None precedent):
      - A metric with an empty denominator is ``None`` ("not measured"), never
        a fabricated 0.0. The template renders absence.
      - "Generations" counts DISTINCT attributable runs (context->>
        's3_request_id', stamped since 72eed6c), not gateway calls — one run is
        2-5 calls. Untagged (pre-instrumentation) calls are surfaced as their
        own count, not folded into either number.
      - Cache hit rate is PER TENANT: Anthropic scopes prompt caches per API
        key and each tenant resolves its own key, so a cross-tenant average is
        meaningless. Escalation/error rates stay global (not cache-semantic).
    """
    from sqlalchemy import text as sql

    start, _end = _window(days)

    row = db.execute(sql("""
        WITH gen AS (
          SELECT tenant_id, cached_input_tokens, cost_usd,
                 context->>'s3_request_id' AS run_key
          FROM llm_usage_log
          WHERE ts >= :start AND status = 'ok' AND task = 'generation'
        ),
        cache_by_tenant AS (
          SELECT tenant_id,
                 COUNT(*) AS calls,
                 SUM(CASE WHEN cached_input_tokens > 0 THEN 1 ELSE 0 END) AS hits
          FROM gen
          GROUP BY tenant_id
          ORDER BY calls DESC
        ),
        runs AS (
          SELECT COUNT(DISTINCT run_key) AS n,
                 COALESCE(SUM(cost_usd), 0)::float AS cost_usd
          FROM gen WHERE run_key IS NOT NULL
        ),
        untagged AS (
          SELECT COUNT(*) AS n FROM gen WHERE run_key IS NULL
        ),
        escalation_stats AS (
          SELECT COUNT(*) AS total,
                 SUM(CASE WHEN escalated THEN 1 ELSE 0 END) AS escalated
          FROM llm_usage_log
          WHERE ts >= :start AND status = 'ok'
        ),
        error_stats AS (
          SELECT COUNT(*) AS total,
                 SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS errors
          FROM llm_usage_log WHERE ts >= :start
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
          COALESCE((SELECT json_agg(c) FROM cache_by_tenant c), '[]'::json)
                                                   AS cache_by_tenant,
          (SELECT n        FROM runs)              AS run_count,
          (SELECT cost_usd FROM runs)              AS run_cost_usd,
          (SELECT n        FROM untagged)          AS untagged_calls,
          (SELECT total    FROM escalation_stats)  AS esc_total,
          (SELECT escalated FROM escalation_stats) AS esc_hits,
          (SELECT total    FROM error_stats)       AS err_total,
          (SELECT errors   FROM error_stats)       AS err_hits,
          COALESCE((SELECT json_agg(t) FROM top_errors t), '[]'::json) AS top_errors
    """), {"start": start}).one()._mapping

    cache_by_tenant = [
        {"tenant_id": c["tenant_id"], "calls": int(c["calls"]),
         "hits": int(c["hits"] or 0),
         "rate": round(int(c["hits"] or 0) / int(c["calls"]), 3)}
        for c in (row["cache_by_tenant"] or []) if int(c["calls"])
    ]

    runs = int(row["run_count"] or 0)
    run_cost = float(row["run_cost_usd"] or 0.0)
    avg_cost_per_gen = round(run_cost / runs, 6) if runs else None

    esc_total = int(row["esc_total"] or 0)
    esc_hits = int(row["esc_hits"] or 0)
    escalation_rate = round(esc_hits / esc_total, 3) if esc_total else None

    err_total = int(row["err_total"] or 0)
    err_hits = int(row["err_hits"] or 0)
    error_rate = round(err_hits / err_total, 3) if err_total else None

    return {
        "days": days,
        # Per-tenant cache stats (caches are per API key — no global average).
        "cache_by_tenant": cache_by_tenant,
        # Distinct attributable runs; None-avg when there are none in window.
        "generations": runs,
        "avg_cost_per_generation_usd": avg_cost_per_gen,
        "unattributed_calls": int(row["untagged_calls"] or 0),
        "escalation_rate": escalation_rate,
        "escalations": esc_hits,
        "escalation_total": esc_total,
        "error_rate": error_rate,
        "top_errors": [dict(e) for e in (row["top_errors"] or [])],
    }


def quality_proxy_summary(db, *, days: int = 30) -> Dict[str, Any]:
    """Quality proxy metrics: regeneration rate, post-gen failure rate.

    This is the architect's "very important" callout. Generation cost is
    wasted if users immediately regenerate or if the generated TCs all
    fail at runtime.
    """
    # D-238 (drop-readiness): all three quality-proxy inputs lived in v1 tables
    # dropped by migration 053 \u2014 generation batches (regeneration rate),
    # ``test_case_versions`` (validation-critical rate), and ``test_cases`` \u00d7
    # ``run_test_results`` (post-gen failure rate). Re-sourcing from the
    # substrate (s3 generation jobs + s6 interpretations) is a logged residual.
    #
    # D-391 (2026-07-25): until that re-sourcing lands, this metric is NOT
    # MEASURED \u2014 and says so, instead of the previous hard-coded zero shape,
    # which rendered as "0.0% regeneration rate", a fabricated healthy reading
    # indistinguishable from a genuinely-measured zero.
    return {
        "days": days,
        "measured": False,
        "reason": ("Quality-proxy sources (generation batches, test case "
                   "versions, run results) were v1 tables dropped in "
                   "migration 053; the substrate re-sourcing is a logged "
                   "residual."),
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

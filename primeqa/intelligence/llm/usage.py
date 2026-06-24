"""Usage tracker \u2014 writes to llm_usage_log.

One call \u2192 one row. Always opens its own Session so the caller's
transaction lifecycle is untouched (same pattern as record_event in
primeqa/runs/streams.py).

Logs success AND failure calls so the dashboard can show error rates.
Never stores prompt text or response text \u2014 those can contain PII
and bloat the table. Use `context` (JSONB) for task-specific metadata.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


def record(
    *,
    tenant_id: int,
    task: str,
    model: str,
    prompt_version: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: Optional[int] = None,
    status: str = "ok",
    user_id: Optional[int] = None,
    complexity: Optional[str] = None,
    escalated: bool = False,
    request_id: Optional[str] = None,
    run_id: Optional[int] = None,
    requirement_id: Optional[int] = None,
    test_case_id: Optional[int] = None,
    generation_batch_id: Optional[int] = None,
    sync_run_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Fire-and-forget write to llm_usage_log. Never raises into the
    caller \u2014 if the write fails we log-warn and move on rather than
    breaking the user's Generate button on an observability hiccup.

    ``sync_run_id`` (migration 058): soft, no-FK cross-schema reference to
    the S1 ``sync_runs`` row this call was performed for (embedding +
    summary enrichment). NULL for all non-sync usage.

    Returns the inserted row id on success (None on write failure) so
    callers that only know their batch/run/sync-run id AFTER the LLM call
    can back-link via set_sync_run() below.
    """
    try:
        from sqlalchemy.orm import Session
        from primeqa.db import engine
        from primeqa.intelligence.models import LLMUsageLog

        sess = Session(bind=engine)
        try:
            row = LLMUsageLog(
                tenant_id=tenant_id,
                user_id=user_id,
                task=task,
                model=model,
                prompt_version=prompt_version,
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cached_input_tokens=int(cached_input_tokens or 0),
                cache_write_tokens=int(cache_write_tokens or 0),
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                status=status,
                complexity=complexity,
                escalated=escalated,
                request_id=request_id,
                run_id=run_id,
                requirement_id=requirement_id,
                test_case_id=test_case_id,
                generation_batch_id=generation_batch_id,
                sync_run_id=sync_run_id,
                context=context or {},
            )
            sess.add(row)
            sess.commit()
            return row.id
        finally:
            sess.close()
    except Exception as e:
        log.warning("llm usage log write failed task=%s tenant=%s: %s",
                    task, tenant_id, e)
        return None


# D-238 (drop-readiness): attach_batch() was removed — it post-hoc linked a
# usage_log row to its v1 ``generation_batches`` row, but had zero live callers
# (its only references were two gateway comments). The generation_batches FK on
# ``llm_usage_log`` becomes a plain int when migration 053 drops the table.


def set_sync_run(usage_log_id: Optional[int], sync_run_id: Optional[str]) -> bool:
    """Post-hoc attach an ``llm_usage_log`` row to its S1 sync_run.

    Used by the summary enrichment lane (migration 058): the gateway writes
    its own usage row inside ``llm_call``, so the worker back-links that row
    to the sync_run it was performed for AFTER the call returns (via the
    response's ``usage_log_id``). Least-invasive — no new param threaded
    through the heavily-used gateway hot path.

    Fire-and-forget: never raises into the worker; a no-op (returns False)
    when either id is missing or the write fails. Returns True on a
    successful update.
    """
    if not usage_log_id or not sync_run_id:
        return False
    try:
        from sqlalchemy import text
        from primeqa.db import engine
        from sqlalchemy.orm import Session

        sess = Session(bind=engine)
        try:
            sess.execute(
                text("UPDATE llm_usage_log SET sync_run_id = :srid "
                     "WHERE id = :rid"),
                {"srid": str(sync_run_id), "rid": int(usage_log_id)},
            )
            sess.commit()
            return True
        finally:
            sess.close()
    except Exception as e:
        log.warning("set_sync_run failed (usage_log_id=%s sync_run_id=%s): %s",
                    usage_log_id, sync_run_id, e)
        return False


def set_sync_run_many(usage_log_ids, sync_run_id: Optional[str]) -> int:
    """Post-hoc attach MANY ``llm_usage_log`` rows to one S1 sync_run.

    Used by the summary lane: a single ``llm_call`` may log MORE than one
    usage row when it escalates (primary attempt + escalated attempt), and
    every billable attempt must carry the sync_run_id — not just the final
    one (``LLMResponse.usage_log_ids`` is the complete list; ``usage_log_id``
    is only the last). Attributing just the last row would silently undercount
    the per-sync-run roll-up on escalation.

    Filters falsy ids; no-op (returns 0) when no ids or no sync_run_id.
    Fire-and-forget: never raises into the worker. Returns the number of rows
    updated.
    """
    if not sync_run_id:
        return 0
    try:
        ids = [int(i) for i in (usage_log_ids or []) if i]
        if not ids:
            return 0
        from sqlalchemy import text
        from primeqa.db import engine
        from sqlalchemy.orm import Session

        sess = Session(bind=engine)
        try:
            res = sess.execute(
                text("UPDATE llm_usage_log SET sync_run_id = :srid "
                     "WHERE id = ANY(:ids)"),
                {"srid": str(sync_run_id), "ids": ids},
            )
            sess.commit()
            return res.rowcount or 0
        finally:
            sess.close()
    except Exception as e:
        log.warning("set_sync_run_many failed (sync_run_id=%s): %s",
                    sync_run_id, e)
        return 0


def get_sync_run_cost(sync_run_id: Optional[str]) -> Dict[str, Any]:
    """Aggregate ``llm_usage_log`` cost for one S1 sync_run (migration 058).

    Sums the embedding + summary LLM usage attributed to ``sync_run_id`` into a
    per-sync-run cost roll-up::

        {
          "sync_run_id": str | None,
          "embedding": {"tokens": int, "cost_usd": float, "rows": int},
          "summary":   {"tokens": int, "cost_usd": float, "rows": int},
          "total_cost_usd": float,
          "tokens_missing_rows": int,
          "rate_provisional_rows": int,
        }

    Buckets by task: the embedding bucket is ``EMBEDDING_TASK`` (entity
    embeddings AND each summary's own embedding); the summary bucket is the
    ``entity_summary_*`` Claude generation tasks. ``total_cost_usd`` sums ALL
    rows for the run (so any other attributed task still counts toward total).
    ``tokens_missing_rows`` counts batches recorded with no usable token count
    (context.tokens_missing) — surfaced so a roll-up is never silently partial.
    ``rate_provisional_rows`` counts embedding rows whose cost came from the
    unconfirmed PLACEHOLDER Voyage rate (context.rate_provisional) — surfaced so
    a consumer never treats a provisional embedding cost as authoritative.

    Read-only; opens its own Session. Returns the zero roll-up when
    ``sync_run_id`` is falsy. Best-effort: never raises (returns zero on error).
    """
    if not sync_run_id:
        return _shape_sync_run_cost(sync_run_id, None)
    try:
        from sqlalchemy import text
        from sqlalchemy.orm import Session
        from primeqa.db import engine
        from primeqa.intelligence.llm.limits import EMBEDDING_TASK

        sql = text("""
            SELECT
              COALESCE(SUM(CASE WHEN task = :emb THEN input_tokens ELSE 0 END), 0) AS emb_tokens,
              COALESCE(SUM(CASE WHEN task = :emb THEN cost_usd     ELSE 0 END), 0) AS emb_cost,
              COALESCE(SUM(CASE WHEN task = :emb THEN 1            ELSE 0 END), 0) AS emb_rows,
              COALESCE(SUM(CASE WHEN task LIKE 'entity_summary%' THEN input_tokens ELSE 0 END), 0) AS sum_tokens,
              COALESCE(SUM(CASE WHEN task LIKE 'entity_summary%' THEN cost_usd     ELSE 0 END), 0) AS sum_cost,
              COALESCE(SUM(CASE WHEN task LIKE 'entity_summary%' THEN 1            ELSE 0 END), 0) AS sum_rows,
              COALESCE(SUM(cost_usd), 0) AS total_cost,
              COALESCE(SUM(CASE WHEN (context->>'tokens_missing') = 'true' THEN 1 ELSE 0 END), 0) AS missing_rows,
              COALESCE(SUM(CASE WHEN (context->>'rate_provisional') = 'true' THEN 1 ELSE 0 END), 0) AS provisional_rows
            FROM llm_usage_log
            WHERE sync_run_id = :srid
        """)
        sess = Session(bind=engine)
        try:
            row = sess.execute(
                sql, {"emb": EMBEDDING_TASK, "srid": str(sync_run_id)}
            ).mappings().first()
        finally:
            sess.close()
        return _shape_sync_run_cost(sync_run_id, row)
    except Exception as e:
        log.warning("get_sync_run_cost failed (sync_run_id=%s): %s",
                    sync_run_id, e)
        return _shape_sync_run_cost(sync_run_id, None)


def _shape_sync_run_cost(sync_run_id, row) -> Dict[str, Any]:
    """Pure shaper for :func:`get_sync_run_cost`: turn the SQL conditional-sum
    ``row`` (a mapping with emb_*/sum_*/total_cost/missing_rows, or None) into
    the public roll-up dict. Coerces NUMERIC cost -> float and token/row counts
    -> int, None-safe per field. Pure (no I/O) so the roll-up shaping is
    unit-testable without a DB; the SQL bucketing is integration-covered.
    """
    def _i(v): return int(v or 0)
    def _f(v): return float(v or 0)
    r = row or {}
    return {
        "sync_run_id": sync_run_id,
        "embedding": {
            "tokens": _i(r.get("emb_tokens")),
            "cost_usd": _f(r.get("emb_cost")),
            "rows": _i(r.get("emb_rows")),
        },
        "summary": {
            "tokens": _i(r.get("sum_tokens")),
            "cost_usd": _f(r.get("sum_cost")),
            "rows": _i(r.get("sum_rows")),
        },
        "total_cost_usd": _f(r.get("total_cost")),
        "tokens_missing_rows": _i(r.get("missing_rows")),
        "rate_provisional_rows": _i(r.get("provisional_rows")),
    }


def get_sync_run_costs_batch(sync_run_ids, *, tenant_id=None) -> Dict[str, Dict[str, Any]]:
    """Batched per-sync-run cost + summary-model roll-up for a PAGE of runs.

    The history table renders cost + model for N runs at once; calling
    :func:`get_sync_run_cost` per row is N sessions + N scans (it opens its own
    Session each call). This does it in ONE query — ``GROUP BY sync_run_id`` over
    the page's run-ids — index-backed by the partial index on
    ``llm_usage_log(sync_run_id)``.

    ``tenant_id`` (when supplied) adds a defense-in-depth ``tenant_id = :tid``
    predicate — the run-ids already come from a tenant-scoped read, but pinning the
    tenant keeps this cross-schema lookup honest if a sync_run UUID were ever reused
    across tenants. ``None`` (the default) leaves it tenant-agnostic for parity with
    :func:`get_sync_run_cost`.

    Returns ``{sync_run_id(str): {embedding{tokens,cost_usd,rows},
    summary{tokens,cost_usd,rows}, total_cost_usd, summary_models:[str]}}`` — only
    runs that have at least one ``llm_usage_log`` row appear; a run absent from the
    map had no attributed usage (caller renders cost 0 / model "—").

    ``summary_models`` is the DISTINCT models that actually produced THIS run's
    entity summaries, read from ``llm_usage_log.model`` (the provider-echoed model
    captured at run time) — the model THAT run used, NOT today's ``SUMMARY_MODEL``
    setting. An empty list means the run produced no summaries (render "—").

    Read-only; opens its own Session. Best-effort: ``{}`` on empty input or any
    error (the history table degrades to zero cost / "—" model, never breaks).
    """
    ids = [str(s) for s in (sync_run_ids or []) if s]
    if not ids:
        return {}
    try:
        from sqlalchemy import text
        from sqlalchemy.orm import Session
        from primeqa.db import engine
        from primeqa.intelligence.llm.limits import EMBEDDING_TASK

        # Cast the bound id list to uuid[] (the column is uuid) so the equality is
        # type-correct AND the partial index on sync_run_id stays usable.
        sql = text("""
            SELECT
              CAST(sync_run_id AS text) AS srid,
              COALESCE(SUM(CASE WHEN task = :emb THEN input_tokens ELSE 0 END), 0) AS emb_tokens,
              COALESCE(SUM(CASE WHEN task = :emb THEN cost_usd     ELSE 0 END), 0) AS emb_cost,
              COALESCE(SUM(CASE WHEN task = :emb THEN 1            ELSE 0 END), 0) AS emb_rows,
              COALESCE(SUM(CASE WHEN task LIKE 'entity_summary%' THEN input_tokens ELSE 0 END), 0) AS sum_tokens,
              COALESCE(SUM(CASE WHEN task LIKE 'entity_summary%' THEN cost_usd     ELSE 0 END), 0) AS sum_cost,
              COALESCE(SUM(CASE WHEN task LIKE 'entity_summary%' THEN 1            ELSE 0 END), 0) AS sum_rows,
              COALESCE(SUM(cost_usd), 0) AS total_cost,
              ARRAY_AGG(DISTINCT model) FILTER (WHERE task LIKE 'entity_summary%') AS summary_models
            FROM llm_usage_log
            WHERE sync_run_id = ANY(CAST(:ids AS uuid[]))
              AND (:tid IS NULL OR tenant_id = :tid)
            GROUP BY sync_run_id
        """)
        sess = Session(bind=engine)
        try:
            rows = sess.execute(
                sql, {"emb": EMBEDDING_TASK, "ids": ids, "tid": tenant_id}
            ).mappings().all()
        finally:
            sess.close()

        def _i(v): return int(v or 0)
        def _f(v): return float(v or 0)
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            models = sorted(m for m in (r.get("summary_models") or []) if m)
            out[r["srid"]] = {
                "embedding": {"tokens": _i(r.get("emb_tokens")),
                              "cost_usd": _f(r.get("emb_cost")),
                              "rows": _i(r.get("emb_rows"))},
                "summary": {"tokens": _i(r.get("sum_tokens")),
                            "cost_usd": _f(r.get("sum_cost")),
                            "rows": _i(r.get("sum_rows"))},
                "total_cost_usd": _f(r.get("total_cost")),
                "summary_models": models,
            }
        return out
    except Exception as e:
        log.warning("get_sync_run_costs_batch failed (%d ids): %s", len(ids), e)
        return {}

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
    context: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Fire-and-forget write to llm_usage_log. Never raises into the
    caller \u2014 if the write fails we log-warn and move on rather than
    breaking the user's Generate button on an observability hiccup.

    Returns the inserted row id on success (None on write failure) so
    callers that only know their batch/run id AFTER the LLM call can
    back-link via attach_batch() below.
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


def attach_batch(usage_log_id: int, generation_batch_id: int) -> None:
    """Post-hoc link a usage_log row to the batch it produced.

    Used by test_plan_generation: the LLM call happens BEFORE the batch
    row exists (we need the response to populate batch.input_tokens /
    cost_usd), so we attach the batch id back to the usage log once the
    batch has been created. Keeps the cost dashboard's per-run
    attribution accurate.

    **Expected-rollback handling (2026-04-24)**: when the outer batch
    transaction rolls back (e.g. the GenerationLinter blocks mid-loop),
    the target ``generation_batches`` row never makes it to COMMITTED
    state, and the FK check on this UPDATE fails with a
    ForeignKeyViolation on ``llm_usage_log_generation_batch_id_fkey``.
    That's not a bug — there's nothing to attach to. Swallow it at
    DEBUG level so the worker logs aren't polluted with a "failure" on
    every linter-blocked generation.

    Any OTHER integrity error (row missing, unrelated FK, check
    constraint) is still surfaced at WARNING — those are real
    regressions worth investigating.
    """
    if not usage_log_id or not generation_batch_id:
        return
    try:
        from sqlalchemy.orm import Session
        from sqlalchemy.exc import IntegrityError
        from primeqa.db import engine
        from primeqa.intelligence.models import LLMUsageLog

        sess = Session(bind=engine)
        try:
            row = sess.query(LLMUsageLog).filter(
                LLMUsageLog.id == usage_log_id,
            ).first()
            if row and row.generation_batch_id is None:
                row.generation_batch_id = generation_batch_id
                try:
                    sess.commit()
                except IntegrityError as ie:
                    # The specific case: batch row doesn't exist (either
                    # rolled back, or never visible because the outer
                    # session hasn't committed yet). Harmless; log DEBUG
                    # and move on. Any other integrity error (e.g. check
                    # constraint failure) falls through to the outer
                    # WARNING block where an operator can see it.
                    sess.rollback()
                    msg = str(ie.orig) if ie.orig is not None else str(ie)
                    if "generation_batch_id_fkey" in msg:
                        log.debug(
                            "attach_batch: batch %s not present "
                            "(rolled back or uncommitted); skipping "
                            "attach for usage_log=%s",
                            generation_batch_id, usage_log_id,
                        )
                        return
                    raise
        finally:
            sess.close()
    except Exception as e:
        log.warning("llm usage attach_batch failed id=%s batch=%s: %s",
                    usage_log_id, generation_batch_id, e)

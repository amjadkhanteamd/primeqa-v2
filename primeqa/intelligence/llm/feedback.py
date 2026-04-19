"""FeedbackCollector \u2014 close the loop from execution back to generation.

The architect's biggest callout: the system should learn from its
misses. When a generator hallucinates a field, the NEXT generation for
the same tenant should have that hallucination in its "don't do this"
context. No fine-tuning, no RAG infra \u2014 just in-context few-shot
from signals we're already emitting.

Signals collected (migration 033):

  validation_critical   TestCaseValidator caught a critical issue
                        immediately after generation.
                        Detail: {rule, object, field, message}

  validation_warning    Same, but warning-severity. Lower weight.

  regenerated_soon      Same user regenerated for the same requirement
                        within 15 minutes of a prior batch. Detail:
                        {delta_seconds, prior_batch_id}

  execution_failed      Run marked a TC execution as failed with an
                        error referencing a field / object / state_ref
                        mismatch. Detail: {error, step_order, object,
                        field}

  ba_rejected           BA review workflow explicitly rejected the
                        version. Highest-weight signal.

Call sites:

  service.py generate_test_plan
    \u2192 capture(tenant_id, type="validation_critical", ...) after each
      new TC whose validator report flagged critical
    \u2192 capture(type="regenerated_soon") on batch supersession

  worker.py _run_execute_stage
    \u2192 capture(type="execution_failed") when a TC result is terminal
      with an error referencing metadata

  ba review flow
    \u2192 capture(type="ba_rejected") when user rejects a version

  PromptBuilder.build()
    \u2192 recent_misses = FeedbackCollector.recent_for_tenant(tenant_id)
    \u2192 inject into prompt as last-block context
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


SIGNAL_VALIDATION_CRITICAL = "validation_critical"
SIGNAL_VALIDATION_WARNING = "validation_warning"
SIGNAL_REGENERATED_SOON = "regenerated_soon"
SIGNAL_EXECUTION_FAILED = "execution_failed"
SIGNAL_BA_REJECTED = "ba_rejected"


def capture(
    *,
    tenant_id: int,
    signal_type: str,
    detail: Dict[str, Any],
    severity: str = "medium",
    generation_batch_id: Optional[int] = None,
    test_case_id: Optional[int] = None,
    test_case_version_id: Optional[int] = None,
    ttl_days: Optional[int] = None,
) -> None:
    """Write one signal row. Never raises \u2014 feedback is best-effort.
    A failed capture shouldn't break the user action that produced it."""
    try:
        from sqlalchemy.orm import Session
        from primeqa.db import engine
        from primeqa.intelligence.models import GenerationQualitySignal

        sess = Session(bind=engine)
        try:
            expires = None
            if ttl_days:
                expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
            row = GenerationQualitySignal(
                tenant_id=tenant_id,
                generation_batch_id=generation_batch_id,
                test_case_id=test_case_id,
                test_case_version_id=test_case_version_id,
                signal_type=signal_type,
                severity=severity,
                detail=detail or {},
                expires_at=expires,
            )
            sess.add(row)
            sess.commit()
        finally:
            sess.close()
    except Exception as e:
        log.warning("feedback.capture failed tenant=%s type=%s: %s",
                    tenant_id, signal_type, e)


def recent_for_tenant(
    tenant_id: int,
    *,
    limit: int = 5,
    window_days: int = 7,
    min_severity: str = "medium",
) -> List[Dict[str, Any]]:
    """Return up to `limit` recent quality signals for a tenant, newest
    first, filtered by severity. Used by PromptBuilder.build() to add
    "recent misses" context to generation prompts.

    Deduped by (signal_type, detail_key) to avoid flooding the prompt
    with the same hallucinated field on every generation.
    """
    try:
        from sqlalchemy.orm import Session
        from primeqa.db import engine
        from primeqa.intelligence.models import GenerationQualitySignal

        severity_rank = {"low": 0, "medium": 1, "high": 2}
        min_rank = severity_rank.get(min_severity, 1)

        window_start = datetime.now(timezone.utc) - timedelta(days=window_days)
        now = datetime.now(timezone.utc)

        sess = Session(bind=engine)
        try:
            rows = sess.query(GenerationQualitySignal).filter(
                GenerationQualitySignal.tenant_id == tenant_id,
                GenerationQualitySignal.captured_at >= window_start,
            ).order_by(
                GenerationQualitySignal.captured_at.desc(),
            ).limit(limit * 4).all()  # over-fetch, dedup below

            dedup_keys = set()
            out: List[Dict[str, Any]] = []
            for row in rows:
                # Honour expires_at
                if row.expires_at and row.expires_at < now:
                    continue
                # Severity filter
                if severity_rank.get(row.severity, 1) < min_rank:
                    continue
                # Dedup key: type + rule/object/field combo when present
                detail = row.detail or {}
                dk = (row.signal_type,
                      detail.get("rule"),
                      detail.get("object"),
                      detail.get("field"),
                      detail.get("error", "")[:80] if detail.get("error") else None)
                if dk in dedup_keys:
                    continue
                dedup_keys.add(dk)
                out.append({
                    "signal_type": row.signal_type,
                    "severity": row.severity,
                    "detail": detail,
                    "captured_at": row.captured_at.isoformat() if row.captured_at else None,
                })
                if len(out) >= limit:
                    break
            return out
        finally:
            sess.close()
    except Exception as e:
        log.warning("feedback.recent_for_tenant failed tenant=%s: %s",
                    tenant_id, e)
        return []

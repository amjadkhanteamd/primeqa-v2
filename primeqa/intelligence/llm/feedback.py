"""FeedbackCollector — close the loop from execution + humans back to
generation.

The architect's biggest callout (Phase 4): the system should learn from
its misses. When a generator hallucinates a field, the NEXT generation
for the same tenant should have that hallucination in its "don't do
this" context. No fine-tuning, no RAG infra — just in-context few-shot
from signals we're already emitting.

Phase 7 adds the human half of the loop: thumbs-down, edit-as-implicit-
negative, and BA review reject. See `feedback_rules.build_rules_block()`
for how signals are aggregated into natural-language rules before they
hit the prompt.

Signals collected (migration 033 — signal_type is VARCHAR, new types
fit the existing schema without a new migration):

Machine-captured (Phase 4):
  validation_critical   TestCaseValidator caught a critical issue
                        immediately after generation.
                        Detail: {rule, object, field, message}

  validation_warning    Same, but warning-severity. Lower weight.

  regenerated_soon      Same user regenerated for the same requirement
                        within 15 minutes of a prior batch.
                        Detail: {delta_seconds, prior_batch_id}

  execution_failed      Run marked a TC execution as failed with an
                        error referencing a field / object / state_ref
                        mismatch.
                        Detail: {error, step_order, object, field}

Human-captured (Phase 7):
  user_thumbs_up        Explicit positive. Captured but NOT fed into
                        the prompt — "what you got right" is noise in
                        a "don't do this" context.
                        Detail: {tc_id, coverage_type, source:explicit}

  user_thumbs_down      Explicit negative with optional reason.
                        Detail: {tc_id, reason, reason_text,
                                 coverage_type, source:explicit}

  user_edited           Implicit: user edited an AI-generated TC within
                        24h of generation. Deduped per (tc_id,
                        10-minute bucket).
                        Detail: {tc_id, prior_version_id,
                                 coverage_type, source:implicit}

  ba_rejected           BA review workflow rejected the version.
                        Highest-weight human signal.
                        Detail: {tc_id, version_id, reason,
                                 reason_text, source:explicit}

Call sites:

  service.py generate_test_plan
    → capture(signal_type="validation_critical", ...) per flagged TC
    → capture(signal_type="regenerated_soon") on batch supersession

  worker.py _run_execute_stage
    → capture(signal_type="execution_failed") when a TC result is
      terminal with an error referencing metadata

  service.py submit_review (Phase 7)
    → capture(signal_type="ba_rejected") when status="rejected"

  service.py update_test_case (Phase 7)
    → capture(signal_type="user_edited") on first edit of an AI-
      generated TC within 24h of generation (deduped per 10-min bucket)

  routes.py POST /api/test-cases/:id/feedback (Phase 7)
    → capture_user_feedback() — thumbs up/down with rate limit

  feedback_rules.build_rules_block(tenant_id)
    → aggregates recent signals into a "don't do this" rules block
      consumed by test_plan_generation prompt
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ---- Signal type constants ------------------------------------------------

# Machine-captured (Phase 4)
SIGNAL_VALIDATION_CRITICAL = "validation_critical"
SIGNAL_VALIDATION_WARNING = "validation_warning"
SIGNAL_REGENERATED_SOON = "regenerated_soon"
SIGNAL_EXECUTION_FAILED = "execution_failed"

# Human-captured (Phase 7)
SIGNAL_USER_THUMBS_UP = "user_thumbs_up"
SIGNAL_USER_THUMBS_DOWN = "user_thumbs_down"
SIGNAL_USER_EDITED = "user_edited"
SIGNAL_BA_REJECTED = "ba_rejected"

# Signal sources (live inside `detail` JSONB, not a column)
SOURCE_EXPLICIT = "explicit"   # user deliberately submitted this signal
SOURCE_IMPLICIT = "implicit"   # we inferred it from behaviour

# Signals that should NEVER enter the prompt's "don't do this" context
# (positive signals would confuse the model).
_POSITIVE_SIGNALS = {SIGNAL_USER_THUMBS_UP}


# ---- Reason enum (thumbs-down / ba-rejected) ------------------------------
#
# Kept deliberately short — 4 common reasons + "other" with free text.
# Growing this list without data is a path to noise.

REASON_WRONG_OBJECT_OR_FIELD = "wrong_object_or_field"
REASON_INVALID_STEPS = "invalid_steps"
REASON_MISSING_COVERAGE = "missing_coverage"
REASON_REDUNDANT = "redundant"
REASON_OTHER = "other"

ALL_REASONS = (
    REASON_WRONG_OBJECT_OR_FIELD,
    REASON_INVALID_STEPS,
    REASON_MISSING_COVERAGE,
    REASON_REDUNDANT,
    REASON_OTHER,
)


# ---- Severity mapping -----------------------------------------------------
#
# The generation_quality_signals.severity column exists (migration 033) but
# was previously always set to the "medium" default. Phase 7 starts using
# it properly so `recent_for_tenant(min_severity="medium")` filters out the
# low-impact signals that would dilute the prompt.

_SIGNAL_SEVERITY = {
    # Machine signals keep their existing default handling.
    SIGNAL_VALIDATION_CRITICAL: "high",
    SIGNAL_VALIDATION_WARNING: "low",
    SIGNAL_REGENERATED_SOON: "medium",
    SIGNAL_EXECUTION_FAILED: "high",

    # Human signals: BA is the highest-weight; explicit thumbs-down follows
    # the reason; implicit edits are medium.
    SIGNAL_BA_REJECTED: "high",
    SIGNAL_USER_EDITED: "medium",
    SIGNAL_USER_THUMBS_UP: "low",    # n/a for prompt but kept in dashboard
    # USER_THUMBS_DOWN is reason-dependent (see _thumbs_down_severity)
}

_REASON_SEVERITY = {
    REASON_WRONG_OBJECT_OR_FIELD: "high",
    REASON_INVALID_STEPS: "high",
    REASON_MISSING_COVERAGE: "medium",
    REASON_REDUNDANT: "low",
    REASON_OTHER: "medium",
}


def _severity_for(signal_type: str, reason: Optional[str] = None) -> str:
    """Resolve severity by signal type + (for thumbs-down) reason.

    Used by both capture() (default) and capture_user_feedback() (explicit
    reason-aware). Keeps the mapping in one place.
    """
    if signal_type == SIGNAL_USER_THUMBS_DOWN:
        return _REASON_SEVERITY.get(reason or REASON_OTHER, "medium")
    return _SIGNAL_SEVERITY.get(signal_type, "medium")


# ---- Core capture ---------------------------------------------------------

def capture(
    *,
    tenant_id: int,
    signal_type: str,
    detail: Dict[str, Any],
    severity: Optional[str] = None,
    generation_batch_id: Optional[int] = None,
    test_case_id: Optional[int] = None,
    test_case_version_id: Optional[int] = None,
    ttl_days: Optional[int] = None,
    dedup_window_minutes: Optional[int] = None,
) -> bool:
    """Write one signal row.

    Returns True if a row was written, False if skipped (dedup hit or
    exception). Never raises — feedback is best-effort and should not
    break the user action that produced it.

    Severity defaults to the type's canonical severity from
    `_SIGNAL_SEVERITY`; pass explicit `severity` to override.

    `dedup_window_minutes`: if set, skip the insert when a signal with
    the same (tenant_id, signal_type, test_case_id) was already captured
    within the window. Used for `user_edited` to suppress keystroke-
    level noise while still allowing a second edit later in the day.
    """
    try:
        from sqlalchemy.orm import Session
        from primeqa.db import engine
        from primeqa.intelligence.models import GenerationQualitySignal

        sev = severity or _severity_for(signal_type, (detail or {}).get("reason"))

        sess = Session(bind=engine)
        try:
            if dedup_window_minutes and test_case_id is not None:
                since = datetime.now(timezone.utc) - timedelta(
                    minutes=dedup_window_minutes,
                )
                exists = sess.query(GenerationQualitySignal.id).filter(
                    GenerationQualitySignal.tenant_id == tenant_id,
                    GenerationQualitySignal.signal_type == signal_type,
                    GenerationQualitySignal.test_case_id == test_case_id,
                    GenerationQualitySignal.captured_at >= since,
                ).first()
                if exists:
                    return False

            expires = None
            if ttl_days:
                expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
            row = GenerationQualitySignal(
                tenant_id=tenant_id,
                generation_batch_id=generation_batch_id,
                test_case_id=test_case_id,
                test_case_version_id=test_case_version_id,
                signal_type=signal_type,
                severity=sev,
                detail=detail or {},
                expires_at=expires,
            )
            sess.add(row)
            sess.commit()
            return True
        finally:
            sess.close()
    except Exception as e:
        log.warning("feedback.capture failed tenant=%s type=%s: %s",
                    tenant_id, signal_type, e)
        return False


# ---- User feedback capture (Phase 7) --------------------------------------

USER_FEEDBACK_RATE_LIMIT_PER_DAY = 5


class FeedbackRateLimited(Exception):
    """Raised internally when the per-user/per-TC daily rate limit is hit.

    The API route catches this and returns 200 with `throttled: true`
    rather than a 429 so spammers don't get a signal that spam is being
    rejected.
    """


def capture_user_feedback(
    *,
    tenant_id: int,
    user_id: int,
    test_case_id: int,
    verdict: str,              # "up" | "down"
    reason: Optional[str] = None,
    reason_text: Optional[str] = None,
    coverage_type: Optional[str] = None,
    test_case_version_id: Optional[int] = None,
    generation_batch_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Capture an explicit user thumbs signal, with rate limit + severity.

    Returns a status dict: `{ok: True, throttled: bool, signal_type,
    severity}`. The endpoint maps this to a 200 response. We never 429
    — a silently-swallowed 6th click is better UX than a visible error
    that tells a spammer "spam is being rejected, keep trying."

    The reason is accepted from a closed vocabulary (see ALL_REASONS)
    for thumbs-down; thumbs-up carries no reason. `reason_text` is free
    text that accompanies any reason (required for `other`, optional
    otherwise).
    """
    verdict = (verdict or "").strip().lower()
    if verdict not in ("up", "down"):
        raise ValueError(f"verdict must be 'up' or 'down', got {verdict!r}")

    if verdict == "down":
        if reason and reason not in ALL_REASONS:
            raise ValueError(f"unknown reason {reason!r}; allowed: {ALL_REASONS}")
        if reason == REASON_OTHER and not (reason_text or "").strip():
            raise ValueError("reason_text is required when reason='other'")

    signal_type = (SIGNAL_USER_THUMBS_UP if verdict == "up"
                   else SIGNAL_USER_THUMBS_DOWN)

    # Rate limit: count this user's feedback signals on this TC today (UTC).
    if _rate_limit_exceeded(tenant_id=tenant_id, user_id=user_id,
                            test_case_id=test_case_id):
        return {
            "ok": True, "throttled": True,
            "signal_type": signal_type,
            "severity": None,
        }

    severity = _severity_for(signal_type, reason)
    detail = {
        "tc_id": test_case_id,
        "user_id": user_id,
        "source": SOURCE_EXPLICIT,
    }
    if coverage_type:
        detail["coverage_type"] = coverage_type
    if reason:
        detail["reason"] = reason
    if reason_text:
        detail["reason_text"] = (reason_text or "")[:500]

    written = capture(
        tenant_id=tenant_id,
        signal_type=signal_type,
        detail=detail,
        severity=severity,
        test_case_id=test_case_id,
        test_case_version_id=test_case_version_id,
        generation_batch_id=generation_batch_id,
    )
    return {
        "ok": True, "throttled": False, "written": written,
        "signal_type": signal_type, "severity": severity,
    }


def _rate_limit_exceeded(*, tenant_id: int, user_id: int,
                         test_case_id: int) -> bool:
    """Has this user already submitted `USER_FEEDBACK_RATE_LIMIT_PER_DAY`
    feedback signals on this TC in the last 24h? Pre-insert check."""
    try:
        from sqlalchemy.orm import Session
        from sqlalchemy import func, cast, Integer
        from primeqa.db import engine
        from primeqa.intelligence.models import GenerationQualitySignal

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        sess = Session(bind=engine)
        try:
            n = sess.query(func.count(GenerationQualitySignal.id)).filter(
                GenerationQualitySignal.tenant_id == tenant_id,
                GenerationQualitySignal.test_case_id == test_case_id,
                GenerationQualitySignal.captured_at >= since,
                GenerationQualitySignal.signal_type.in_([
                    SIGNAL_USER_THUMBS_UP, SIGNAL_USER_THUMBS_DOWN,
                ]),
                # user_id lives in the JSONB detail — cast to int for equality.
                cast(
                    GenerationQualitySignal.detail["user_id"].astext,
                    Integer,
                ) == user_id,
            ).scalar()
            return int(n or 0) >= USER_FEEDBACK_RATE_LIMIT_PER_DAY
        finally:
            sess.close()
    except Exception as e:
        log.warning("rate-limit check failed tenant=%s user=%s tc=%s: %s",
                    tenant_id, user_id, test_case_id, e)
        # Fail-open: on query error we let the signal through rather than
        # blocking legitimate feedback. Signals are best-effort anyway.
        return False


# ---- Prompt-context reader ------------------------------------------------

def recent_for_tenant(
    tenant_id: int,
    *,
    limit: int = 5,
    window_days: int = 7,
    min_severity: str = "medium",
    exclude_positive: bool = True,
    db=None,
) -> List[Dict[str, Any]]:
    """Return up to `limit` recent quality signals for a tenant, newest
    first, filtered by severity. Used by feedback_rules.build_rules_block()
    to produce the "recent misses" block injected into generation prompts.

    Deduped by (signal_type, rule/object/field/reason) to avoid flooding
    the prompt with the same hallucinated field on every generation.

    Positive signals (thumbs_up) are excluded by default — "what the AI
    got right" is noise in a "don't do this" prompt context.

    Optional `db` (audit U2, 2026-04-19): reuse the caller's session to
    amortise Railway RTT across a chain of dashboard queries.
    """
    try:
        from sqlalchemy.orm import Session
        from primeqa.db import engine
        from primeqa.intelligence.models import GenerationQualitySignal

        severity_rank = {"low": 0, "medium": 1, "high": 2}
        min_rank = severity_rank.get(min_severity, 1)

        window_start = datetime.now(timezone.utc) - timedelta(days=window_days)
        now = datetime.now(timezone.utc)

        owns_session = db is None
        sess = db if db is not None else Session(bind=engine)
        try:
            q = sess.query(GenerationQualitySignal).filter(
                GenerationQualitySignal.tenant_id == tenant_id,
                GenerationQualitySignal.captured_at >= window_start,
            )
            if exclude_positive:
                q = q.filter(~GenerationQualitySignal.signal_type.in_(
                    list(_POSITIVE_SIGNALS),
                ))
            rows = q.order_by(
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
                # Dedup key: type + rule/object/field/reason/error combo
                detail = row.detail or {}
                dk = (row.signal_type,
                      detail.get("rule"),
                      detail.get("object"),
                      detail.get("field"),
                      detail.get("reason"),
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
            if owns_session:
                sess.close()
    except Exception as e:
        log.warning("feedback.recent_for_tenant failed tenant=%s: %s",
                    tenant_id, e)
        return []

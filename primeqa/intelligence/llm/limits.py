"""Per-tenant LLM rate limits, backed by llm_usage_log.

Three windows are checked on every `llm_call()`:

  1. calls in the last 60 seconds vs. llm_max_calls_per_minute
  2. calls in the last 3600 seconds vs. llm_max_calls_per_hour
  3. spend today (UTC) vs. llm_max_spend_per_day_usd

When any limit is exceeded the gateway raises LLMError("rate_limited")
and records a zero-token row in llm_usage_log with status='rate_limited'
so the dashboard attributes blocked calls correctly. NULL on the column
= no limit (the default \u2014 gentle onboarding, superadmin sets caps
per tenant later).

Queries use the idx_llm_usage_tenant_ts index; even at millions of rows
they return in well under 10ms.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from primeqa.intelligence.llm.router import TenantPolicy

log = logging.getLogger(__name__)


@dataclass
class TenantLimits:
    max_per_minute: Optional[int] = None
    max_per_hour: Optional[int] = None
    max_spend_per_day_usd: Optional[float] = None


@dataclass
class LimitCheckResult:
    allowed: bool
    reason: Optional[str] = None   # "minute_limit" | "hour_limit" | "daily_spend"
    message: Optional[str] = None


def load_tenant_config(tenant_id: int):
    """Return (TenantLimits, TenantPolicy) from tenant_agent_settings,
    or defaults if no row exists."""
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    from primeqa.core.models import TenantAgentSettings

    sess = Session(bind=engine)
    try:
        row = sess.query(TenantAgentSettings).filter(
            TenantAgentSettings.tenant_id == tenant_id,
        ).first()
        if not row:
            return TenantLimits(), TenantPolicy()
        return (
            TenantLimits(
                max_per_minute=row.llm_max_calls_per_minute,
                max_per_hour=row.llm_max_calls_per_hour,
                max_spend_per_day_usd=(
                    float(row.llm_max_spend_per_day_usd)
                    if row.llm_max_spend_per_day_usd is not None else None
                ),
            ),
            TenantPolicy(
                always_use_opus=bool(row.llm_always_use_opus),
                allow_haiku=bool(row.llm_allow_haiku),
            ),
        )
    finally:
        sess.close()


def check(tenant_id: int, limits: TenantLimits) -> LimitCheckResult:
    """Return LimitCheckResult; .allowed=False if any window is over cap."""
    if not limits.max_per_minute and not limits.max_per_hour and not limits.max_spend_per_day_usd:
        return LimitCheckResult(allowed=True)

    from datetime import datetime, timezone, timedelta
    from sqlalchemy.orm import Session
    from sqlalchemy import func as sf
    from primeqa.db import engine
    from primeqa.intelligence.models import LLMUsageLog

    now = datetime.now(timezone.utc)
    sess = Session(bind=engine)
    try:
        if limits.max_per_minute:
            window_start = now - timedelta(seconds=60)
            count = sess.query(sf.count(LLMUsageLog.id)).filter(
                LLMUsageLog.tenant_id == tenant_id,
                LLMUsageLog.ts >= window_start,
                LLMUsageLog.status == "ok",
            ).scalar()
            if count >= limits.max_per_minute:
                return LimitCheckResult(
                    allowed=False, reason="minute_limit",
                    message=f"Tenant limit: {limits.max_per_minute} LLM calls per minute reached.",
                )

        if limits.max_per_hour:
            window_start = now - timedelta(seconds=3600)
            count = sess.query(sf.count(LLMUsageLog.id)).filter(
                LLMUsageLog.tenant_id == tenant_id,
                LLMUsageLog.ts >= window_start,
                LLMUsageLog.status == "ok",
            ).scalar()
            if count >= limits.max_per_hour:
                return LimitCheckResult(
                    allowed=False, reason="hour_limit",
                    message=f"Tenant limit: {limits.max_per_hour} LLM calls per hour reached.",
                )

        if limits.max_spend_per_day_usd:
            # Day boundary is UTC midnight (tenant-local would need a tz
            # column; defer to Phase 6).
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            spend = sess.query(sf.coalesce(sf.sum(LLMUsageLog.cost_usd), 0)).filter(
                LLMUsageLog.tenant_id == tenant_id,
                LLMUsageLog.ts >= day_start,
                LLMUsageLog.status == "ok",
            ).scalar()
            if float(spend or 0) >= limits.max_spend_per_day_usd:
                return LimitCheckResult(
                    allowed=False, reason="daily_spend",
                    message=(
                        f"Tenant daily spend cap reached: "
                        f"${float(spend):.4f} of ${limits.max_spend_per_day_usd:.2f}"
                    ),
                )
    finally:
        sess.close()

    return LimitCheckResult(allowed=True)

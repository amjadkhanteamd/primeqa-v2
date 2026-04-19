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


@dataclass
class UsageSnapshot:
    """Point-in-time window counts + caps. Used by the UI to draw
    progress bars and warn at ~80% without re-running the three queries
    in template context."""
    calls_last_minute: int
    calls_last_hour: int
    spend_today_usd: float
    cap_per_minute: Optional[int]
    cap_per_hour: Optional[int]
    cap_spend_per_day_usd: Optional[float]

    def pct(self, used: float, cap: Optional[float]) -> Optional[float]:
        """Return usage as 0.0–1.0+ against a cap, or None if uncapped."""
        if cap is None or cap <= 0:
            return None
        return float(used) / float(cap)

    @property
    def pct_per_minute(self) -> Optional[float]:
        return self.pct(self.calls_last_minute, self.cap_per_minute)

    @property
    def pct_per_hour(self) -> Optional[float]:
        return self.pct(self.calls_last_hour, self.cap_per_hour)

    @property
    def pct_spend_today(self) -> Optional[float]:
        return self.pct(self.spend_today_usd, self.cap_spend_per_day_usd)

    @property
    def warn(self) -> bool:
        """True if ANY bar is >= 80% — UI shows the soft-cap banner."""
        for p in (self.pct_per_minute, self.pct_per_hour, self.pct_spend_today):
            if p is not None and p >= 0.80:
                return True
        return False

    @property
    def blocked(self) -> bool:
        """True if ANY bar is fully saturated — next call will 429."""
        for p in (self.pct_per_minute, self.pct_per_hour, self.pct_spend_today):
            if p is not None and p >= 1.0:
                return True
        return False


def load_tenant_config(tenant_id: int):
    """Return (TenantLimits, TenantPolicy) from tenant_agent_settings,
    or defaults if no row exists.

    Tier resolution (migration 034): start from the tier preset, then
    let any non-NULL raw column override that slot. This is the only
    place tier logic touches the hot path — the router + gateway stay
    tier-agnostic (they only see the final TenantLimits + TenantPolicy).
    """
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    from primeqa.core.models import TenantAgentSettings
    from primeqa.intelligence.llm import tiers

    sess = Session(bind=engine)
    try:
        row = sess.query(TenantAgentSettings).filter(
            TenantAgentSettings.tenant_id == tenant_id,
        ).first()
        if not row:
            # No settings row → starter tier, no overrides.
            resolved = tiers.resolve_limits(tiers.TIER_STARTER)
            return (
                TenantLimits(
                    max_per_minute=resolved["max_per_minute"],
                    max_per_hour=resolved["max_per_hour"],
                    max_spend_per_day_usd=resolved["max_spend_per_day_usd"],
                ),
                TenantPolicy(),
            )

        tier = getattr(row, "llm_tier", None) or tiers.TIER_STARTER
        resolved = tiers.resolve_limits(
            tier,
            override_per_minute=row.llm_max_calls_per_minute,
            override_per_hour=row.llm_max_calls_per_hour,
            override_spend_per_day=(
                float(row.llm_max_spend_per_day_usd)
                if row.llm_max_spend_per_day_usd is not None else None
            ),
        )
        return (
            TenantLimits(
                max_per_minute=resolved["max_per_minute"],
                max_per_hour=resolved["max_per_hour"],
                max_spend_per_day_usd=resolved["max_spend_per_day_usd"],
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


def current_usage(tenant_id: int, limits: TenantLimits) -> UsageSnapshot:
    """Return a UsageSnapshot for the three limit windows.

    One round-trip — three CASE aggregates over the same index scan.
    Cheap enough that pages can call it on every render without caching.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy.orm import Session
    from sqlalchemy import func as sf, case
    from primeqa.db import engine
    from primeqa.intelligence.models import LLMUsageLog

    now = datetime.now(timezone.utc)
    minute_start = now - timedelta(seconds=60)
    hour_start = now - timedelta(seconds=3600)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Take the widest of (day_start, hour_start) so we catch both.
    window_start = day_start if day_start < hour_start else hour_start

    sess = Session(bind=engine)
    try:
        row = sess.query(
            sf.sum(case(
                (LLMUsageLog.ts >= minute_start, 1), else_=0,
            )).label("calls_minute"),
            sf.sum(case(
                (LLMUsageLog.ts >= hour_start, 1), else_=0,
            )).label("calls_hour"),
            sf.coalesce(sf.sum(case(
                (LLMUsageLog.ts >= day_start, LLMUsageLog.cost_usd), else_=0,
            )), 0).label("spend_day"),
        ).filter(
            LLMUsageLog.tenant_id == tenant_id,
            LLMUsageLog.ts >= window_start,
            LLMUsageLog.status == "ok",
        ).one()

        return UsageSnapshot(
            calls_last_minute=int(row.calls_minute or 0),
            calls_last_hour=int(row.calls_hour or 0),
            spend_today_usd=float(row.spend_day or 0),
            cap_per_minute=limits.max_per_minute,
            cap_per_hour=limits.max_per_hour,
            cap_spend_per_day_usd=limits.max_spend_per_day_usd,
        )
    finally:
        sess.close()

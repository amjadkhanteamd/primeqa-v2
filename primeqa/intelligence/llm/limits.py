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

Embedding calls (\u00a723 enrichment worker): embeddings go through the
Voyage API, not the message gateway / `llm_call()`. To bring them
under the SAME per-tenant windows, the enrichment worker calls
`check()` before an embedding batch and `record_embedding_usage()`
after \u2014 the latter writes one llm_usage_log row per batch with
task=EMBEDDING_TASK, so `check()`'s call-count windows count it with
zero change to `check()` itself. The daily-spend window is naturally
a no-op for embeddings (cost_usd=0.0; Voyage cost accounting is a
later concern).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.router import TenantPolicy

log = logging.getLogger(__name__)

# Task name embedding calls log under in llm_usage_log. Shared between
# record_embedding_usage() (below) and the dashboard so both agree on
# the row's identity. Distinct from any message-gateway task name.
EMBEDDING_TASK = "embedding_generation"


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


def load_tenant_config(tenant_id: int, *, db=None, return_row: bool = False):
    """Return (TenantLimits, TenantPolicy) from tenant_agent_settings,
    or defaults if no row exists.

    Tier resolution (migration 034): start from the tier preset, then
    let any non-NULL raw column override that slot. This is the only
    place tier logic touches the hot path — the router + gateway stay
    tier-agnostic (they only see the final TenantLimits + TenantPolicy).

    Optional `db` parameter (audit U2, 2026-04-19): when provided, reuse
    the caller's session instead of opening our own. Dashboards call
    multiple helpers in a row over Railway's ~650ms RTT; passing the
    same session through cuts dashboard latency significantly without
    changing the contract for ad-hoc callers (worker, API routes) that
    don't have a session handy.

    `return_row=True` additionally returns the raw `TenantAgentSettings`
    ORM row (or None) so the caller can read fields like `llm_tier`
    without a second round-trip. Used by the my-llm-usage view.
    """
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    from primeqa.core.models import TenantAgentSettings
    from primeqa.intelligence.llm import tiers

    def _starter_defaults():
        """Starter-tier limits + default policy — the value returned
        both for a tenant with no settings row AND (fail-open) when
        the settings table can't be read at all."""
        resolved = tiers.resolve_limits(tiers.TIER_STARTER)
        result = (
            TenantLimits(
                max_per_minute=resolved["max_per_minute"],
                max_per_hour=resolved["max_per_hour"],
                max_spend_per_day_usd=resolved["max_spend_per_day_usd"],
            ),
            TenantPolicy(),
        )
        return (*result, None) if return_row else result

    owns_session = db is None
    sess = db if db is not None else Session(bind=engine)
    try:
        row = sess.query(TenantAgentSettings).filter(
            TenantAgentSettings.tenant_id == tenant_id,
        ).first()
        if not row:
            # No settings row → starter tier, no overrides.
            return _starter_defaults()

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
        result = (
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
        return (*result, row) if return_row else result
    except Exception as e:
        # Fail OPEN: the rate-limit/tier layer is governance, not the
        # work itself — if tenant_agent_settings can't be read (table
        # absent, no engine bound, transient DB error) the LLM call
        # should still proceed under conservative starter limits
        # rather than crash. Mirrors usage.record's "never raise into
        # the caller" discipline. Relevant for the substrate-1
        # enrichment worker, whose tenant DB carries the entities but
        # not the primeqa-app gateway tables.
        log.warning(
            "load_tenant_config: read failed (tenant=%s): %s — "
            "falling back to starter defaults", tenant_id, e,
        )
        return _starter_defaults()
    finally:
        if owns_session:
            try:
                sess.close()
            except Exception:
                pass


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
    except Exception as e:
        # Fail OPEN — same discipline as load_tenant_config above. The
        # rate-limit windows are governance, not the work itself; if
        # llm_usage_log can't be read (table absent, no engine bound,
        # transient DB error) the call proceeds rather than crashing.
        # The substrate-1 enrichment worker runs against a tenant DB
        # that carries the entities but not the primeqa-app gateway
        # tables.
        log.warning(
            "limits.check: read failed (tenant=%s): %s — allowing call",
            tenant_id, e,
        )
        return LimitCheckResult(allowed=True)
    finally:
        try:
            sess.close()
        except Exception:
            pass

    return LimitCheckResult(allowed=True)


def record_embedding_usage(
    tenant_id: int,
    *,
    batch_size: int,
    model: str,
    tokens: Optional[int] = None,
    sync_run_id: Optional[str] = None,
    latency_ms: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Log one embedding batch to llm_usage_log so `check()`'s
    per-tenant call-count windows count it AND its real Voyage cost
    rolls up per sync_run (migration 058, 1d cost-telemetry).

    Embeddings don't flow through `llm_call()` — they hit the Voyage
    API directly from the enrichment worker — so without this they'd
    be invisible to the rate-limit windows. One row per BATCH (not
    per text): a batch of up to 128 texts is one Voyage API call and
    counts as one call against the minute/hour windows.

    `tokens`: the Voyage input-token count for the batch (from
    `embed_batch_with_usage`). When given, `cost_usd` is computed from
    `embeddings.voyage_embedding_cost_usd` (the per-1M-token rate). When
    `None`, the response carried NO usable token count — recorded as a
    call (the rate-limit windows still need it) but with input_tokens=0,
    cost_usd=0.0, and a loud `tokens_missing` marker + ERROR log so the
    zero is NEVER mistaken for a measured value (no silent fabrication).

    `sync_run_id`: soft no-FK reference to the S1 sync_run this batch was
    embedded for, so get_sync_run_cost can sum it (NULL when not in a sync).

    Thin wrapper over `usage.record` (the same writer the gateway
    uses) — lives in limits.py because "make embedding calls show up
    in the rate-limit count" is a rate-limit concern. Fire-and-forget:
    never raises into the worker; returns the row id or None.
    """
    from primeqa.intelligence.llm import usage
    from primeqa.intelligence.embeddings import (
        voyage_embedding_cost_usd, VOYAGE_3_RATE_CONFIRMED,
    )

    ctx: Dict[str, Any] = {"batch_size": int(batch_size)}
    if context:
        ctx.update(context)

    if tokens is None:
        # No silent fabricated zero: a missing token count is logged loud and
        # marked, NOT written as if it were a measured 0-token / $0 batch.
        log.error(
            "record_embedding_usage: no token count for an embedding batch "
            "(tenant=%s model=%s batch_size=%s) — recording the call with "
            "tokens/cost UNMEASURED (tokens_missing), not a fabricated zero",
            tenant_id, model, batch_size,
        )
        ctx["tokens_missing"] = True
        input_tokens = 0
        cost_usd = 0.0
    else:
        input_tokens = int(tokens)
        cost_usd = voyage_embedding_cost_usd(input_tokens)
        if not VOYAGE_3_RATE_CONFIRMED:
            # In-band honesty: this cost came from a PLACEHOLDER rate. Mirror the
            # tokens_missing marker so neither the row nor get_sync_run_cost ever
            # passes a provisional price off as confirmed.
            ctx["rate_provisional"] = True

    return usage.record(
        tenant_id=tenant_id,
        task=EMBEDDING_TASK,
        model=model,
        prompt_version=model,  # embeddings have no prompt — tag = model
        input_tokens=input_tokens,
        output_tokens=0,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        status="ok",
        sync_run_id=sync_run_id,
        context=ctx,
    )


def current_usage(tenant_id: int, limits: TenantLimits, *, db=None) -> UsageSnapshot:
    """Return a UsageSnapshot for the three limit windows.

    One round-trip — three CASE aggregates over the same index scan.
    Cheap enough that pages can call it on every render without caching.

    Optional `db` (audit U2, 2026-04-19): reuse the caller's session.
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

    owns_session = db is None
    sess = db if db is not None else Session(bind=engine)
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
        if owns_session:
            sess.close()

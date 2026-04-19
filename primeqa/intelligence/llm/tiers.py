"""Tenant LLM tiers — product-level bundles that pin numeric caps.

Rather than ask "is the admin happy hand-setting three numeric caps and
two policy flags?", the product answer is: pick a tier. Each tier is a
named bundle of sensible defaults for:

    - llm_max_calls_per_minute
    - llm_max_calls_per_hour
    - llm_max_spend_per_day_usd
    - llm_always_use_opus
    - llm_allow_haiku

When a tenant's tier is set (via `tenant_agent_settings.llm_tier`), the
resolved policy uses the tier preset EXCEPT where the tenant has a non-
NULL override in one of the raw columns. That override-wins behaviour
lets a superadmin pin "Pro tenant, but 1000/day spend" without dropping
the whole tier.

Tiers:
    starter     default for new tenants — generous for trial usage
    pro         paid tier — 3x caps, no Opus escalation
    enterprise  premium — unlimited caps + always-Opus available
    custom      IGNORE the tier presets, use only the raw columns

Keep this file pure (no DB imports) so it's trivially testable and the
tier schema is self-evident at a glance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


TIER_STARTER = "starter"
TIER_PRO = "pro"
TIER_ENTERPRISE = "enterprise"
TIER_CUSTOM = "custom"

ALL_TIERS = (TIER_STARTER, TIER_PRO, TIER_ENTERPRISE, TIER_CUSTOM)


@dataclass(frozen=True)
class TierPreset:
    """Defaults a tier ships with.

    A tier preset value of None means "no cap / follow model default";
    an explicit numeric value in the tenant row overrides the tier preset.
    """
    label: str
    description: str
    max_calls_per_minute: Optional[int]
    max_calls_per_hour: Optional[int]
    max_spend_per_day_usd: Optional[float]
    always_use_opus: bool
    allow_haiku: bool


# Preset values are deliberately generous for starter — the goal at
# trial-sign-up is to *not* hit the cap, not to squeeze. Rate-limit
# hits on starter are almost always a runaway script, not legitimate.
_PRESETS: Dict[str, TierPreset] = {
    TIER_STARTER: TierPreset(
        label="Starter",
        description=(
            "Free / trial tier. Generous caps — meant to never be hit "
            "by legitimate usage. Haiku allowed, no Opus escalation."
        ),
        max_calls_per_minute=30,
        max_calls_per_hour=500,
        max_spend_per_day_usd=5.00,
        always_use_opus=False,
        allow_haiku=True,
    ),
    TIER_PRO: TierPreset(
        label="Pro",
        description=(
            "Paid tier. 3x Starter caps plus Opus escalation on the "
            "test-plan generator when complexity is high."
        ),
        max_calls_per_minute=100,
        max_calls_per_hour=2000,
        max_spend_per_day_usd=25.00,
        always_use_opus=False,
        allow_haiku=True,
    ),
    TIER_ENTERPRISE: TierPreset(
        label="Enterprise",
        description=(
            "Premium tier. Effectively unlimited caps (None = no limit) "
            "and the premium-model flag unlocked. Individual overrides "
            "still apply on top."
        ),
        max_calls_per_minute=None,
        max_calls_per_hour=None,
        max_spend_per_day_usd=None,
        always_use_opus=False,    # opt-in — a button in tenant settings
        allow_haiku=True,
    ),
    TIER_CUSTOM: TierPreset(
        label="Custom",
        description=(
            "Tier presets IGNORED. Only the raw llm_max_* columns on "
            "the tenant row are consulted. Use when the superadmin "
            "hand-tunes every cap."
        ),
        max_calls_per_minute=None,
        max_calls_per_hour=None,
        max_spend_per_day_usd=None,
        always_use_opus=False,
        allow_haiku=True,
    ),
}


def get_preset(tier: Optional[str]) -> TierPreset:
    """Return the preset for this tier name. Unknown / None → starter."""
    if not tier:
        return _PRESETS[TIER_STARTER]
    return _PRESETS.get(tier, _PRESETS[TIER_STARTER])


def all_presets() -> Dict[str, TierPreset]:
    """Expose the full dict for UI rendering (admin picker etc.)."""
    return dict(_PRESETS)


def resolve_limits(
    tier: Optional[str],
    *,
    override_per_minute: Optional[int] = None,
    override_per_hour: Optional[int] = None,
    override_spend_per_day: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """Combine a tier preset with per-tenant overrides.

    Override-wins semantics: any non-None override slot is used verbatim;
    NULL slots fall through to the tier preset. Returns a dict keyed by
    the column name so callers (limits.load_tenant_config) can drop it
    straight into a TenantLimits.

    `custom` tier skips the preset entirely — its slots come out None
    unless the caller passes an override.
    """
    preset = get_preset(tier)

    def pick(override, preset_value):
        if override is not None:
            return override
        if tier == TIER_CUSTOM:
            return None
        return preset_value

    return {
        "max_per_minute": pick(override_per_minute, preset.max_calls_per_minute),
        "max_per_hour": pick(override_per_hour, preset.max_calls_per_hour),
        "max_spend_per_day_usd": pick(override_spend_per_day, preset.max_spend_per_day_usd),
    }

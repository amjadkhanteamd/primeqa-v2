"""Model Router \u2014 picks which Anthropic model to use for each task.

Policy lives in one place so swapping Sonnet \u2192 Opus for complex
generations (or Haiku \u2192 Sonnet for a tenant that wants quality) is
a config change, not a scavenger hunt across five call sites.

Chains:
- For tasks with escalation: [primary, fallback]. Gateway retries with
  the fallback once when the primary returns low-confidence output or
  invalid JSON.
- For tasks without escalation: [only].

Tenant overrides come from `tenant_agent_settings` and can force specific
behaviours, e.g. "always Opus" for a premium tenant.

When in doubt, bias low: Sonnet for reasoning, Haiku for classification,
Opus only when Sonnet has been shown to fall short for this task-
complexity combo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


# ---- Complexity buckets ---------------------------------------------------
# Detected by PromptBuilder from the context; kept as strings so the router
# can route without knowing how the bucket was computed.
COMPLEXITY_LOW = "low"
COMPLEXITY_MEDIUM = "medium"
COMPLEXITY_HIGH = "high"


# ---- Canonical model ids --------------------------------------------------
# Verified against /v1/models + live 5-token probes on 2026-04-20 using
# the tenant's actual API key (scripts/probe_llm_models.py). Picks:
#   OPUS   \u2014 Opus 4 works; deprecated 6/15/2026 but has runway. Opus
#            4.5 / 4.6 / 4.7 are available in the catalog and are the
#            migration target before the EOL.
#   SONNET \u2014 UPGRADED from Sonnet 4 to Sonnet 4.5: newer, same price
#            tier, better reasoning. Sonnet 4 also works but is
#            deprecated on the same schedule as Opus 4.
#   HAIKU  \u2014 FIXED from dead claude-3-5-haiku-20241022 (EOL 2/19/2026,
#            past) to Haiku 4.5. Was 404'ing every failure_summary /
#            classification / connection_test call.
OPUS = "claude-opus-4-20250514"
SONNET = "claude-sonnet-4-5-20250929"
HAIKU = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class TenantPolicy:
    """Per-tenant overrides, loaded from tenant_agent_settings."""
    always_use_opus: bool = False       # premium tier: best model everywhere
    allow_haiku: bool = True            # some tenants disable cheapest tier
    force_model: Optional[str] = None   # hard override, e.g. "opus-4"


# ---- Routing table --------------------------------------------------------
#
# {task: {complexity_or_default: [primary, fallback]}}
#
# For tasks keyed by "default", complexity is ignored.

_CHAINS: Dict[str, Dict[str, List[str]]] = {
    # Test plan generation: the whale, escalates on complexity and on
    # low-confidence retry.
    "test_plan_generation": {
        COMPLEXITY_LOW:    [SONNET],
        COMPLEXITY_MEDIUM: [SONNET, OPUS],
        COMPLEXITY_HIGH:   [OPUS],
    },

    # Agent fix proposal: Sonnet default, escalate on low confidence.
    "agent_fix": {
        "default": [SONNET, OPUS],
    },

    # Failure root-cause analysis: prefer Sonnet; fall back to Opus if
    # the tenant's API key doesn't serve Sonnet (we've seen 404s on
    # 3.5-Haiku + 4-Sonnet for keys restricted to the 4-Opus endpoint).
    "failure_analysis": {
        "default": [SONNET, OPUS],
    },

    # Failure summary panel: cheap-tier Haiku for summarisation, with
    # Opus as fallback so "Summarise failures" always works even when
    # the cheaper tiers aren't available to this key.
    "failure_summary": {
        "default": [HAIKU, OPUS],
    },

    # Lightweight classification (taxonomy fallback, AC extraction).
    "classification": {
        "default": [HAIKU, OPUS],
    },

    # Connection ping \u2014 10 tokens. Fallback to Opus is almost free
    # in absolute terms and keeps the ping green on restricted keys.
    "connection_test": {
        "default": [HAIKU, OPUS],
    },
}


def select_chain(
    task: str,
    complexity: str = "default",
    tenant_policy: Optional[TenantPolicy] = None,
) -> List[str]:
    """Return the model chain for this (task, complexity, tenant).

    The chain is [primary, optional fallback]. Gateway calls index 0 by
    default; index 1 is used for the single-hop escalation on retry.
    """
    policy = tenant_policy or TenantPolicy()

    # Hard override wins over everything (superadmin / testing).
    if policy.force_model:
        return [policy.force_model]

    # "Always Opus" premium tier: take whatever the chain would have been
    # and replace with Opus-only, no escalation needed (already at top).
    if policy.always_use_opus:
        return [OPUS]

    task_chains = _CHAINS.get(task)
    if not task_chains:
        # Unknown task \u2014 fall back to Sonnet, no escalation. Better
        # than raising because the Gateway can still do the call and
        # the call site registers as "unknown task" in usage log for
        # diagnosis.
        return [SONNET]

    chain = task_chains.get(complexity) or task_chains.get("default")
    if not chain:
        # Task registered but complexity bucket missing: best-effort pick
        # the first available chain so calls don't fail silently.
        chain = next(iter(task_chains.values()))

    # Honor allow_haiku: strip Haiku from the chain if disabled.
    # Guarantee at least one element remains.
    if not policy.allow_haiku:
        filtered = [m for m in chain if "haiku" not in m.lower()]
        if filtered:
            chain = filtered

    return list(chain)  # defensive copy

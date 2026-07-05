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

import os
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
#   OPUS   \u2014 UPGRADED to 4.7 (newest available, no deprecation window).
#   SONNET \u2014 UPGRADED from Sonnet 4 to Sonnet 4.5: newer, same price
#            tier, better reasoning. Sonnet 4 also works but is
#            deprecated on the same schedule as Opus 4.
#   HAIKU  \u2014 FIXED from dead claude-3-5-haiku-20241022 (EOL 2/19/2026,
#            past) to Haiku 4.5. Was 404'ing every failure_summary /
#            classification / connection_test call.
OPUS = "claude-opus-4-7"
SONNET = "claude-sonnet-4-5-20250929"
HAIKU = "claude-haiku-4-5-20251001"
# Sonnet 5 — the newest Sonnet (Claude 5 family). S3 generation routes here by
# default as of 2026-07-02 (AK; generation/routing.py), ahead of the formal
# Sonnet-vs-Opus A/B. Its own constant so bumping generation's model does NOT
# disturb the _CHAINS tasks (agent_fix / repair_proposal / failure_analysis /
# test_plan_generation) that still bind the older SONNET.
# NOTE: unlike the ids above (probe-verified 2026-04-20), this id was set from
# the model catalog and NOT yet probed in-repo — confirm it resolves for the
# deployed key (scripts/probe_llm_models.py) before relying on it; a wrong id
# 404s and fails generation loud (content_error, non-retryable).
SONNET_5 = "claude-sonnet-5"


# ---- Configurable summary model (Phase-1 close-out #5) ---------------------
# The entity-summary step (Flow / ValidationRule plain-English summaries) is a
# cheap, cosmetic enrichment whose model is a PLATFORM-level cost lever: set the
# SUMMARY_MODEL env var to swap models without a deploy. Scoped to the summary
# tasks ONLY — every other task/chain in _CHAINS is untouched.
SUMMARY_MODEL_ENV = "SUMMARY_MODEL"
_DEFAULT_SUMMARY_MODEL = HAIKU                 # unset → today's behaviour (Haiku 4.5)
KNOWN_SUMMARY_MODELS = frozenset({HAIKU, SONNET, OPUS})  # the router's known model ids

# The summary tasks routed by SUMMARY_MODEL. These resolve to a single-element
# chain (no escalation), exactly as before — only the model id is now config.
_SUMMARY_TASKS = frozenset({"entity_summary_flow", "entity_summary_validation_rule"})


class SummaryModelConfigError(RuntimeError):
    """SUMMARY_MODEL is set to a value the router does not know how to route.

    Fail loud (no silent fallback to the default): a typo'd or unsupported model
    id would otherwise silently break enrichment in any environment."""


def summary_model() -> str:
    """The model id the entity-summary step uses, from the SUMMARY_MODEL env var.

    Unset / empty → the default (Haiku 4.5), i.e. no behaviour change. A set value
    MUST be one of ``KNOWN_SUMMARY_MODELS`` — anything else raises
    ``SummaryModelConfigError`` (no silent fallback). This is the SINGLE SOURCE OF
    TRUTH for the summary model: both the router (which routes the summary tasks)
    and the enrichment gate (which folds the model id into the summary hash) call
    it, so the hashed model id can never drift from the model the worker calls."""
    raw = os.getenv(SUMMARY_MODEL_ENV)
    if not raw or not raw.strip():
        return _DEFAULT_SUMMARY_MODEL
    model = raw.strip()
    if model not in KNOWN_SUMMARY_MODELS:
        raise SummaryModelConfigError(
            f"{SUMMARY_MODEL_ENV}={model!r} is not a known model id; allowed: "
            f"{sorted(KNOWN_SUMMARY_MODELS)} (unset → {_DEFAULT_SUMMARY_MODEL})"
        )
    return model


def validate_summary_model() -> None:
    """Boot gate — ALWAYS-ON (not prod-only): a bad SUMMARY_MODEL breaks
    enrichment in every environment, and the safe default means there is nothing
    environment-specific to guard. Raises ``SummaryModelConfigError`` on an
    unknown id; a no-op when unset or valid. Wired into the same boot points as
    the secrets validator (app + worker + scheduler)."""
    summary_model()


@dataclass(frozen=True)
class TenantPolicy:
    """Per-tenant overrides, loaded from tenant_agent_settings."""
    always_use_opus: bool = False       # premium tier: best model everywhere
    allow_haiku: bool = True            # some tenants disable cheapest tier


# ---- Routing table --------------------------------------------------------
#
# {task: {complexity_or_default: [primary, fallback]}}
#
# For tasks keyed by "default", complexity is ignored.

_CHAINS: Dict[str, Dict[str, List[str]]] = {
    # Test plan generation: the whale, escalates on complexity and on
    # low-confidence retry.
    #
    # Chain length note: the gateway treats every non-first entry as an
    # ESCALATION TARGET (retried on low-confidence success). Adding a
    # same-tier-or-lower model as a "safety net" therefore burns money
    # on pointless escalations (we saw HIGH requirements spend ~$0.60
    # by escalating Opus 4.7 \u2192 Opus 4.5). Keep chains to genuine
    # upgrades only: cheap primary \u2192 better fallback. Single-entry
    # chains are fine \u2014 they fail loud rather than escalating
    # laterally.
    "test_plan_generation": {
        COMPLEXITY_LOW:    [SONNET],
        COMPLEXITY_MEDIUM: [SONNET, OPUS],
        COMPLEXITY_HIGH:   [OPUS],
    },

    # Agent fix proposal: Sonnet default, escalate to Opus on low confidence.
    "agent_fix": {
        "default": [SONNET, OPUS],
    },

    # D-236 (theme #6) — the substrate auto-fix agent's recipe_edit proposal:
    # Sonnet default, escalate once to Opus on low confidence.
    "repair_proposal": {
        "default": [SONNET, OPUS],
    },

    # Failure root-cause analysis: Sonnet default, escalate to Opus.
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

    # Story-view enrichment (migration 048) — summarises a mechanical
    # test case into BA-readable title/description/preconditions/outcome.
    # Haiku is plenty for summarisation. NO fallback: if Haiku fails or
    # returns malformed JSON, the renderer transparently falls back to
    # the mechanical step view — not worth escalating to Opus for a
    # cosmetic feature.
    "story_view_generation": {
        COMPLEXITY_LOW: [HAIKU],
        "default":      [HAIKU],
    },

    # Interpretation phrasing (D-117) — restates S6's deterministic
    # interpretation into QA prose. Haiku-class, NO fallback: a failed phrasing
    # caches nothing and the caller falls back to the deterministic attribution.
    "interpretation_phrasing_generation": {
        COMPLEXITY_LOW: [HAIKU],
        "default":      [HAIKU],
    },

    # §23 enrichment worker — per-entity-type plain-English summaries
    # (Flow, ValidationRule). Haiku-class summarisation, like
    # story_view. NO fallback: a failed summary is re-queued by the
    # enrichment worker (failed_retryable), not escalated to Opus.
    "entity_summary_flow": {
        COMPLEXITY_LOW: [HAIKU],
        "default":      [HAIKU],
    },
    "entity_summary_validation_rule": {
        COMPLEXITY_LOW: [HAIKU],
        "default":      [HAIKU],
    },

    # S7 grounded answering (D-163.3) — phrase a bounded substrate-evidence block
    # into an answer (invent-nothing). Haiku-class, NO fallback: a failed phrasing
    # degrades to a refusal (the grounding exists; only the prose failed).
    "grounded_answer_generation": {
        COMPLEXITY_LOW: [HAIKU],
        "default":      [HAIKU],
    },

    # Readable-body phrasing — restate the deterministic Stage-1 readable-body
    # skeleton into BA/QA prose. Haiku-class, NO fallback: an ungrounded or
    # failed phrasing falls back to the deterministic Stage-1 baseline.
    "readable_body_phrasing_generation": {
        COMPLEXITY_LOW: [HAIKU],
        "default":      [HAIKU],
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

    # Summary tasks are routed by the platform-level SUMMARY_MODEL config (default
    # Haiku), NOT by tenant policy. This is deliberate and DELIBERATELY overrides
    # always_use_opus for summaries: the enrichment gate folds the model id into
    # the summary hash via the same summary_model() resolver, and the gate cannot
    # see tenant policy — so routing summaries by anything tenant-dependent would
    # let the hashed model drift from the model the worker actually calls. Keeping
    # summaries env-only guarantees gate-model == worker-model. (Net effect vs
    # today: a premium always_use_opus tenant's summaries follow SUMMARY_MODEL —
    # Haiku by default — instead of Opus; set SUMMARY_MODEL to change it.)
    if task in _SUMMARY_TASKS:
        return [summary_model()]

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

"""Cost forecast for runs \u2014 tokens, USD, SF API calls.

Only surfaced in the UI to Super Admins (Q decision). Per-model pricing is
held in a dict here for v1 (Super Admin can override via tenant settings in
a later phase).
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# Anthropic pricing as of 2025-Q2 (USD per 1M tokens). Kept inline to avoid
# a config file for v1; Super Admin override lives in tenant_agent_settings
# (room to grow).
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # Family: claude-opus-4-*
    "claude-opus-4-20250514":     {"in": 15.00, "out": 75.00},
    # Family: claude-sonnet-4-*
    "claude-sonnet-4-20250514":   {"in":  3.00, "out": 15.00},
    # Older families still supported (no Claude-4 Haiku yet; cheap tier
    # is 3.5 Haiku \u2014 see llm/router.HAIKU).
    "claude-3-7-sonnet-20250219": {"in":  3.00, "out": 15.00},
    "claude-3-5-haiku-20241022":  {"in":  0.80, "out":  4.00},
}
DEFAULT_MODEL = "claude-sonnet-4-20250514"


def estimate_run_cost(test_count: int, *, model: Optional[str] = None,
                      run_type: str = "execute_only") -> Dict[str, Any]:
    """Upper-bound cost forecast.

    Assumptions (explicit so super admins can reason about them):
      - execute_only runs: 0 LLM tokens (no agent regeneration triggered)
      - full / generate_only: ~2K input + ~1K output tokens per test
      - SF API: ~8 calls per test (create/update/query/verify)
    """
    if run_type in ("execute_only",):
        return {
            "test_count": test_count,
            "tokens_in": 0, "tokens_out": 0,
            "usd_estimate": 0.00,
            "model": None,
            "sf_api_calls_estimate": 8 * test_count,
            "note": "Executor-only run \u2014 no LLM tokens consumed unless the agent triages a failure.",
        }

    model = model or DEFAULT_MODEL
    pricing = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    tokens_in = 2_000 * test_count
    tokens_out = 1_000 * test_count
    usd = round((tokens_in / 1_000_000) * pricing["in"]
                + (tokens_out / 1_000_000) * pricing["out"], 4)
    return {
        "test_count": test_count,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "usd_estimate": usd,
        "model": model,
        "sf_api_calls_estimate": 8 * test_count,
        "note": "Upper bound \u2014 cached prompts and skipped tests reduce actuals.",
    }

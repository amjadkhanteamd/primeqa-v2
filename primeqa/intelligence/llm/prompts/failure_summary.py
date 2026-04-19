"""Failure summary prompt \u2014 Haiku-level summarization.

Input: list of failed step lines (TC id, step, action, object, error).
Output: 3-6 sentences grouping failures by root cause.

Small prompt, single block, not worth caching. Haiku is plenty for this.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "failure_summary@v1"
MAX_TOKENS = 600
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


def detect_complexity(context: Dict[str, Any]) -> Optional[str]:
    return None  # no complexity routing


def build(
    context: Dict[str, Any],
    *,
    tenant_id: int,
    recent_misses: Optional[list] = None,
) -> PromptSpec:
    failure_lines = context.get("failure_lines") or []
    body = "\n".join(failure_lines[:50])
    prompt = (
        "Summarise why these Salesforce test steps failed. Group by root "
        "cause when possible. Keep it to 3-6 sentences. Be specific: "
        "mention field / object / validation names. No preamble.\n\n"
        + body
    )
    return PromptSpec(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS,
        parse=lambda resp: (resp.raw_text or "").strip(),
        context_for_log={
            "failure_count": len(failure_lines),
            "run_id": context.get("run_id"),
        },
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    return False  # Haiku, single call, no escalation

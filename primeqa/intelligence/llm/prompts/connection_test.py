"""Connection test ping \u2014 cheapest possible call to confirm the key works.

Routed to Haiku (tier floor). 10-output-token call. Used by Settings \u2192
Connections \u2192 Test Connection button.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "connection_test@v1"
MAX_TOKENS = 10
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


def detect_complexity(context: Dict[str, Any]) -> Optional[str]:
    return None


def build(
    context: Dict[str, Any],
    *,
    tenant_id: int,
    recent_misses: Optional[list] = None,
) -> PromptSpec:
    return PromptSpec(
        messages=[{"role": "user", "content": "Reply with one word: pong"}],
        max_tokens=MAX_TOKENS,
        parse=lambda resp: (resp.raw_text or "").strip(),
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    return False

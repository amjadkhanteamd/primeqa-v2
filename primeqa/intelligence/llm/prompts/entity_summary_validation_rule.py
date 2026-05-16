"""Entity-summary prompt for ValidationRule — Haiku-level summarisation.

Turns a synced ValidationRule's facts (parent object, error message,
the formula that triggers the error, the field the error displays on)
into a 2-4 sentence plain-English summary: what the rule enforces and
when it blocks a save.

The summary lands in validation_rule_details.summary_text (+ its
embedding in validation_rule_details.summary_embedding). Best-effort
enrichment, run by the background worker — Haiku-class, no cache
blocks, no escalation.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "entity_summary_validation_rule@v1"
MAX_TOKENS = 400
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


SYSTEM = (
    "You summarise Salesforce validation-rule metadata for business "
    "analysts and release reviewers.\n\n"
    "Given a validation rule's facts, write a 2-4 sentence plain-"
    "English summary: what the rule enforces, on which object, and "
    "the condition under which it blocks a save.\n\n"
    "Rules:\n"
    "- Lead with what it enforces, not \"This validation rule...\".\n"
    "- Name the parent object.\n"
    "- Translate the formula into the business condition it "
    "expresses — don't just restate the formula syntax. If the "
    "formula is too opaque to interpret confidently, describe what "
    "is known (object, error message) and stop.\n"
    "- No preamble, no bullet points — just the summary sentences."
)


def detect_complexity(context: Dict[str, Any]) -> Optional[str]:
    """Always low — entity summarisation runs on Haiku."""
    return "low"


def build(
    context: Dict[str, Any],
    *,
    tenant_id: int,
    recent_misses: Optional[list] = None,
) -> PromptSpec:
    """Build a PromptSpec from a ValidationRule's facts.

    Expected `context` keys (all optional — the prompt degrades
    gracefully on sparse input):
      name                 — validation rule name
      object               — parent object API name
      error_message        — the error shown to the user on a
                             blocked save
      formula_text         — the errorConditionFormula
      error_display_field  — the field the error displays on (None
                             for top-of-page errors)
    """
    facts: Dict[str, Any] = {
        "name": context.get("name"),
        "object": context.get("object"),
        "error_message": context.get("error_message"),
        "formula_text": context.get("formula_text"),
        "error_display_field": context.get("error_display_field"),
    }
    user_msg = json.dumps(facts, indent=2, ensure_ascii=False)

    return PromptSpec(
        messages=[{"role": "user", "content": user_msg}],
        system=[{"type": "text", "text": SYSTEM}],
        parse=lambda resp: (resp.raw_text or "").strip(),
        max_tokens=MAX_TOKENS,
        context_for_log={
            "entity_type": "ValidationRule",
            "rule_name": facts["name"],
            "object": facts["object"],
        },
        has_cache_blocks=False,
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    """No escalation — Haiku single shot; a failure is re-queued by
    the enrichment worker, not escalated to a pricier model."""
    return False

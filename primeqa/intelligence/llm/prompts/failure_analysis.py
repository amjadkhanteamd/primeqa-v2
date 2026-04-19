"""Failure root-cause analysis prompt.

Called when regex / pattern-matching taxonomy can't classify a failure.
Produces a short structured diagnosis: category, likely cause, suggested
action. Sonnet-default, no escalation.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "failure_analysis@v1"
MAX_TOKENS = 1024
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


SCHEMA = {
    "category": "validation_error | field_error | data_error | metadata_drift | flaky | unknown",
    "root_cause": "one sentence",
    "suggested_action": "one sentence",
    "confidence": "0.0 - 1.0",
}


def detect_complexity(context: Dict[str, Any]) -> Optional[str]:
    return None


def build(
    context: Dict[str, Any],
    *,
    tenant_id: int,
    recent_misses: Optional[list] = None,
) -> PromptSpec:
    error_text = context.get("error_text", "")
    step_context = context.get("step_context", "")

    prompt = (
        "Diagnose this Salesforce test step failure. Be specific and "
        "actionable. Respond ONLY with JSON matching the schema.\n\n"
        f"Step: {step_context}\n\n"
        f"Error:\n{error_text}\n\n"
        f"Schema:\n{json.dumps(SCHEMA, indent=2)}"
    )

    def _parse(resp):
        text = (resp.raw_text or "").strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:-1])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"category": "unknown", "root_cause": text[:200],
                    "suggested_action": "", "confidence": 0.3}

    return PromptSpec(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS,
        parse=_parse,
        context_for_log={
            "run_step_result_id": context.get("run_step_result_id"),
            "test_case_id": context.get("test_case_id"),
        },
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    return False

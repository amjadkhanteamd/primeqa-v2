"""Agent fix proposal prompt.

Called by AgentOrchestrator when a step fails and we have enough
context (failure class + metadata) to propose a corrective edit.
Must return valid PrimeQA step JSON that can replace or insert into
the original test case.

Sonnet default; escalates once to Opus on low confidence or parse fail.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "agent_fix@v1"
MAX_TOKENS = 1024
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = True


SCHEMA = {
    "fix_type": "replace_step | insert_step | replace_field_value | add_precondition",
    "target_step_order": "int, 1-indexed",
    "patch": "object matching the step grammar (same shape as generation output)",
    "rationale": "one sentence",
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
    failure = context.get("failure_summary", "")
    original_step = context.get("original_step", {})
    full_test_case = context.get("full_test_case", [])

    prompt = (
        "A PrimeQA test step failed. Propose a minimal fix that satisfies "
        "the step grammar and respects the test's intent. Respond ONLY "
        "with JSON matching the schema.\n\n"
        f"Failing step:\n{json.dumps(original_step, indent=2)}\n\n"
        f"Failure:\n{failure}\n\n"
        f"Full test case context:\n{json.dumps(full_test_case, indent=2)[:2000]}\n\n"
        f"Schema:\n{json.dumps(SCHEMA, indent=2)}"
    )

    def _parse(resp):
        text = (resp.raw_text or "").strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:-1])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_parse_error": True, "_raw": text[:500]}

    return PromptSpec(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS,
        parse=_parse,
        context_for_log={
            "run_id": context.get("run_id"),
            "test_case_id": context.get("test_case_id"),
            "agent_fix_attempt_id": context.get("agent_fix_attempt_id"),
        },
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    if isinstance(parsed, dict) and parsed.get("_parse_error"):
        return True
    if isinstance(parsed, dict) and float(parsed.get("confidence", 1.0)) < 0.7:
        return True
    return False

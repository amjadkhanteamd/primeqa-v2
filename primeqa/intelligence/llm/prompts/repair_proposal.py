"""Repair-proposal prompt (theme #6, D-236) — the auto-fix agent's LLM layer.

Called by the repair agent when a substrate (S4) run fails in one of the three
RECIPE-OWNER classes (the test itself is buggy): `platform_constraint`,
`field_not_createable`, `automation_effect_absent`. It proposes a CONCRETE edit
to the recipe's subject create — a `field_changes` map where a value REPLACES /
ADDS a field and the sentinel ``__REMOVE__`` DROPS it — plus a confidence score
and a one-line rationale.

Anthropic `tool_use` (`submit_recipe_fix`, strict JSON schema) so a parse failure
is impossible. Sonnet→Opus with one escalation on low confidence (mirrors the v1
`agent_fix` posture). This NEVER touches FINDINGS or org-owner causes — the agent
only routes the recipe-owner classes here.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec

VERSION = "repair_proposal@v1"
MAX_TOKENS = 800
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = True

# The sentinel value the agent's apply path reads as "drop this field from the
# subject create" (vs any other value = set/replace it).
REMOVE_SENTINEL = "__REMOVE__"

_TOOL_SCHEMA = {
    "name": "submit_recipe_fix",
    "description": (
        "Propose a minimal edit to the test recipe's subject-create field values "
        "that fixes the recorded failure while preserving the test's intent."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "confidence": {
                "type": "number",
                "description": "0.0–1.0 — how confident the edit fixes the failure.",
            },
            "rationale": {
                "type": "string",
                "description": "One sentence: why this edit fixes the failure.",
            },
            "field_changes": {
                "type": "object",
                "description": (
                    "Map of bare field name -> new value to SET on the create, or "
                    f"the literal string '{REMOVE_SENTINEL}' to DROP that field. "
                    "Only the fields that change; omit everything else."
                ),
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["confidence", "rationale", "field_changes"],
    },
}


def detect_complexity(context: Dict[str, Any]) -> Optional[str]:
    return None


def build(context: Dict[str, Any], *, tenant_id: int,
          recent_misses: Optional[list] = None) -> PromptSpec:
    verdict = context.get("verdict", "")
    cause = context.get("cause_kind", "")
    sobject = context.get("sobject", "the object")
    field_values = context.get("current_field_values") or {}
    error_code = context.get("error_code") or ""
    error_message = context.get("error_message") or ""
    error_fields = context.get("error_fields") or []

    prompt = (
        "A PrimeQA substrate test failed because the TEST RECIPE is buggy (not the "
        "org and not a real product defect). Propose the smallest field-value edit "
        "to the subject create that makes the test valid again, preserving its "
        "intent. Call submit_recipe_fix.\n\n"
        f"SObject: {sobject}\n"
        f"S6 verdict: {verdict}\n"
        f"S6 cause: {cause}\n"
        f"Salesforce error code: {error_code}\n"
        f"Error message: {error_message}\n"
        f"Fields named in the error: {', '.join(error_fields) if error_fields else '(none)'}\n\n"
        f"The create's current field values:\n{json.dumps(field_values, indent=2)[:1500]}\n\n"
        "Guidance:\n"
        "- field_not_createable / platform_constraint: the named field is read-only "
        f"or blocked — DROP it (set it to '{REMOVE_SENTINEL}').\n"
        "- automation_effect_absent: the create did not satisfy the automation's "
        "entry condition — ADD or change the field(s) it needs.\n"
        "- Change as FEW fields as possible. If you cannot fix it from the recipe "
        "alone, return low confidence."
    )

    def _parse(resp):
        if resp.tool_input is not None:
            return resp.tool_input            # pre-parsed dict from the tool call
        text = (resp.raw_text or "").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_parse_error": True, "_raw": text[:500]}

    return PromptSpec(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS,
        parse=_parse,
        tools=[_TOOL_SCHEMA],
        force_tool_name="submit_recipe_fix",
        context_for_log={
            "run_id": context.get("run_id"),
            "claim_test_id": context.get("claim_test_id"),
            "verdict": verdict,
            "cause_kind": cause,
        },
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    """Escalate Sonnet→Opus once on a parse failure or low confidence (< 0.7)."""
    if isinstance(parsed, dict) and parsed.get("_parse_error"):
        return True
    try:
        if isinstance(parsed, dict) and float(parsed.get("confidence", 1.0)) < 0.7:
            return True
    except (TypeError, ValueError):
        return True
    return False

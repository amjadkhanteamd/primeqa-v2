"""Story-view enrichment prompt — Haiku-level summarisation.

Turns a mechanical test case (create/update/verify steps with SF
field payloads) into four human-readable fields a BA or stakeholder
can scan in 30 seconds:

  - title: intent-level, specific (what's verified, under what condition)
  - description: one short paragraph on the business behaviour tested
  - preconditions_narrative: one short paragraph on the starting state
  - expected_outcome: one short paragraph on what should happen

The prompt is short + Haiku-class; no cache blocks and no escalation.
If it fails, the caller falls back to the mechanical step view.

Feature-gated via `tenant_agent_settings.llm_enable_story_enrichment`
(migration 048). Default off.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "story_view@v1"
MAX_TOKENS = 800
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


SYSTEM = (
    "You translate Salesforce test cases into clear, human-readable stories.\n\n"
    "Given a mechanical test case (steps with CREATE/UPDATE/VERIFY actions and "
    "field payloads), produce a story-view that a Business Analyst or stakeholder "
    "can understand in 30 seconds.\n\n"
    "Output JSON with exactly these four fields:\n"
    "- title: Specific, intent-level. Names what's being verified and under what "
    "condition. Max 100 chars. NOT \"Test Opportunity validation\". YES \"Prevent "
    "Closed Won when Amount is blank\".\n"
    "- description: One paragraph (2-4 sentences). What business behaviour this "
    "test is proving. Written in plain English, no Salesforce jargon unless "
    "essential.\n"
    "- preconditions_narrative: One short paragraph. What state the system needs "
    "to be in before the test runs. Derived from the initial CREATE steps, but "
    "phrased as setup context, not mechanical instructions.\n"
    "- expected_outcome: One paragraph. What should happen when the test runs. "
    "Plain English. If it's a negative test, say what SHOULDN'T happen and what "
    "error/rejection is expected.\n\n"
    "Rules:\n"
    "- Focus on business intent, not UI mechanics.\n"
    "- Use the Jira ticket context to frame why the test matters.\n"
    "- Every word earns its place. No filler like \"This test will...\" or "
    "\"The purpose of this test is to...\".\n"
    "- Output must be valid JSON, nothing else."
)


def detect_complexity(context: Dict[str, Any]) -> Optional[str]:
    """Always low — summarisation runs on Haiku."""
    return "low"


def _extract_json(text: str) -> Optional[dict]:
    """Defensive JSON extraction. Handles fenced code blocks + stray prose."""
    if not text:
        return None
    t = text.strip()
    # Strip markdown fencing
    if t.startswith("```"):
        # Remove ```[lang]\n ... \n```
        t = re.sub(r"^```[a-zA-Z0-9]*\s*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
        t = t.strip()
    # Direct parse first
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Last-ditch: find first {...} block in the text
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _parse(resp) -> Optional[dict]:
    """Gateway parser hook. Returns the parsed dict or None on any failure.

    The enricher validates the required fields afterwards, so this just
    has to produce a dict or None. Defensive against fenced code blocks
    and stray narration before/after the JSON.
    """
    raw = getattr(resp, "raw_text", None) or ""
    return _extract_json(raw)


def build(context: Dict[str, Any], *,
          tenant_id: int,
          recent_misses: Optional[list] = None) -> PromptSpec:
    """Build a PromptSpec from the enrichment context.

    Expected `context` keys:
      - plan_tc: the mechanical TC dict (steps / expected_results /
        preconditions / coverage_type / confidence_score)
      - tc_title: the title on the TestCase row (post-prefix)
      - requirement: optional {jira_key, summary, description}
    """
    plan_tc = context.get("plan_tc") or {}
    tc_title = context.get("tc_title") or ""
    requirement = context.get("requirement") or None

    payload: Dict[str, Any] = {
        "test_case_title": tc_title,
        "coverage_type": plan_tc.get("coverage_type"),
        "steps": plan_tc.get("steps") or [],
        "expected_results": plan_tc.get("expected_results") or [],
        "preconditions": plan_tc.get("preconditions") or [],
    }
    if requirement:
        # Keep description bounded — Jira descriptions are sometimes long
        desc = requirement.get("description") or ""
        payload["jira_ticket"] = {
            "key": requirement.get("jira_key"),
            "summary": requirement.get("summary") or "",
            "description": (desc[:2000] if desc else ""),
        }

    user_msg = json.dumps(payload, indent=2, ensure_ascii=False)

    return PromptSpec(
        messages=[
            {"role": "user", "content": user_msg},
        ],
        system=[{"type": "text", "text": SYSTEM}],
        parse=_parse,
        max_tokens=MAX_TOKENS,
        context_for_log={
            "coverage_type": plan_tc.get("coverage_type"),
            "step_count": len(payload["steps"]),
            "has_requirement_context": requirement is not None,
        },
        has_cache_blocks=False,
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    """No escalation — Haiku is the single shot; failure falls back
    to the mechanical view."""
    return False

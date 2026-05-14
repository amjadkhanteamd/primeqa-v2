"""Entity-summary prompt for Flow — Haiku-level summarisation.

Turns a synced Flow's structural facts (process type, trigger, the
object it acts on, its decisions / action calls) into a 2-4 sentence
plain-English summary a BA or release reviewer can scan: what this
automation does, when it fires, and what it touches.

The summary lands in flow_details.summary_text (+ its embedding in
flow_details.summary_embedding). Best-effort enrichment, run by the
background worker — Haiku-class, no cache blocks, no escalation. A
failure is re-queued by the worker, not escalated.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "entity_summary_flow@v1"
MAX_TOKENS = 400
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


SYSTEM = (
    "You summarise Salesforce automation metadata for business "
    "analysts and release reviewers.\n\n"
    "Given a Flow's structural facts, write a 2-4 sentence plain-"
    "English summary: what the automation does, when it runs, and "
    "which object it acts on.\n\n"
    "Rules:\n"
    "- Lead with what it does, not \"This flow...\".\n"
    "- Name the triggering object and trigger type when present.\n"
    "- Be concrete about the decisions / actions when the data "
    "shows them; don't invent behaviour the facts don't state.\n"
    "- No preamble, no bullet points — just the summary sentences.\n"
    "- If the facts are sparse, say what's known plainly and stop."
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
    """Build a PromptSpec from a Flow's structural facts.

    Expected `context` keys (all optional — the prompt degrades
    gracefully on sparse input):
      name                 — Flow API name / label
      process_type         — e.g. 'AutoLaunchedFlow', 'Flow'
      is_active            — bool
      triggers_on_object   — object API name the Flow is record-
                             triggered on (None for non-record flows)
      trigger_type         — 'BeforeSave' / 'AfterSave' / ... (None
                             for autolaunched / screen flows)
      description          — Flow description, if any
      decisions            — list of decision/branch labels
      action_calls         — list of action-call labels/types
    """
    facts: Dict[str, Any] = {
        "name": context.get("name"),
        "process_type": context.get("process_type"),
        "is_active": context.get("is_active"),
        "triggers_on_object": context.get("triggers_on_object"),
        "trigger_type": context.get("trigger_type"),
        "description": context.get("description"),
        "decisions": context.get("decisions") or [],
        "action_calls": context.get("action_calls") or [],
    }
    user_msg = json.dumps(facts, indent=2, ensure_ascii=False)

    return PromptSpec(
        messages=[{"role": "user", "content": user_msg}],
        system=[{"type": "text", "text": SYSTEM}],
        parse=lambda resp: (resp.raw_text or "").strip(),
        max_tokens=MAX_TOKENS,
        context_for_log={
            "entity_type": "Flow",
            "flow_name": facts["name"],
            "has_trigger": facts["triggers_on_object"] is not None,
        },
        has_cache_blocks=False,
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    """No escalation — Haiku single shot; a failure is re-queued by
    the enrichment worker, not escalated to a pricier model."""
    return False

"""Readable-body phrasing prompt — Haiku-level restatement (readable test case).

Turns the DETERMINISTIC Stage-1 readable-body skeleton (headline, "what this
checks", preconditions, test data, conceptual steps, expected result, boundary
probes — all facts already grounded in the claim + its recipes) into two
BA/QA-friendly fields:

  - plain_terms: one short paragraph ("in plain terms …") a business analyst can
    read at a glance;
  - step_narration: one plain sentence per given step, in the same order.

The prompt is short + Haiku-class; no cache blocks, no escalation. **Invent
nothing** — every field name, value, number, record and rule in the output MUST
already appear in the skeleton it is given. A deterministic grounding validator
(``readable_body_phrasing._validate_grounding``) rejects any output that names a
value/field/entity absent from the skeleton, and the reader falls back to the
deterministic Stage-1 baseline. Feature-gated, default OFF.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "readable_body_phrasing@v1"
MAX_TOKENS = 700
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


SYSTEM = (
    "You rewrite a software test case into plain English for a business analyst "
    "or QA reviewer.\n\n"
    "You are given a STRUCTURED, already-verified test-case skeleton — a "
    "headline, what it checks, preconditions, test data (field -> value), a list "
    "of conceptual steps, an expected result, and optionally boundary cases. "
    "These facts are the ground truth. Your ONLY job is to phrase them clearly.\n\n"
    "Output JSON with exactly two fields:\n"
    "- plain_terms: one short paragraph (2-4 sentences) that explains, in plain "
    "business language, what this test verifies and why — readable at a glance.\n"
    "- step_narration: an array of short plain-English sentences, ONE per step "
    "in the given 'steps' list, in the same order (empty array if no steps).\n\n"
    "Hard rules:\n"
    "- INVENT NOTHING. Use ONLY the field names, values, numbers, records, "
    "entities and rules that appear in the skeleton. Do NOT introduce any field, "
    "value, number, threshold, object or outcome the skeleton does not state.\n"
    "- Use the business labels exactly as given (e.g. 'Credit Score', not "
    "'Credit_Score__c'). Never introduce an API name, flow name, or schema term.\n"
    "- Do not add causes, fixes, severity, or speculation the skeleton does not "
    "state. Restate the expected result faithfully.\n"
    "- No filler ('This test...', 'The purpose is...'). Plain, direct English.\n"
    "- Output must be valid JSON, nothing else."
)


def detect_complexity(context: Dict[str, Any]) -> Optional[str]:
    """Always low — restatement runs on Haiku."""
    return "low"


def _extract_json(text: str) -> Optional[dict]:
    """Defensive JSON extraction. Handles fenced code blocks + stray prose."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\s*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _parse(resp) -> Optional[dict]:
    """Gateway parser hook. Returns the parsed dict or None on any failure; the
    enricher validates the required fields + grounding afterwards."""
    raw = getattr(resp, "raw_text", None) or ""
    return _extract_json(raw)


def build(context: Dict[str, Any], *,
          tenant_id: int,
          recent_misses: Optional[list] = None) -> PromptSpec:
    """Build a PromptSpec from the skeleton facts. Only the skeleton reaches the
    model (nothing else), so it cannot source new facts.

    Expected ``context`` keys (all from the Stage-1 skeleton):
      headline, checks, preconditions, test_data, steps, expected_result, probes.
    """
    payload: Dict[str, Any] = {
        "headline": context.get("headline"),
        "checks": context.get("checks"),
        "preconditions": context.get("preconditions") or [],
        "test_data": context.get("test_data") or [],
        "steps": context.get("steps") or [],
        "expected_result": context.get("expected_result"),
        "probes": context.get("probes") or [],
    }
    user_msg = json.dumps(payload, indent=2, ensure_ascii=False)

    return PromptSpec(
        messages=[{"role": "user", "content": user_msg}],
        system=[{"type": "text", "text": SYSTEM}],
        parse=_parse,
        max_tokens=MAX_TOKENS,
        context_for_log={
            "kind": context.get("kind"),
            "n_steps": len(payload["steps"]),
        },
        has_cache_blocks=False,
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    """No escalation — Haiku is the single shot; a failed/ungrounded phrasing
    falls back to the deterministic Stage-1 baseline."""
    return False

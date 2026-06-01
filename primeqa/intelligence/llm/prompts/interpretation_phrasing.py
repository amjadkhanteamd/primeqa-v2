"""Interpretation-phrasing prompt — Haiku-level restatement (D-117).

Turns S6's deterministic structured `Interpretation` (outcome / verdict /
attribution / cause) into two QA-readable fields a reviewer can scan in seconds:

  - headline: one line naming what happened (the verdict in plain words)
  - explanation: 2-3 sentences explaining the outcome and (if a cause is given) why

The prompt is short + Haiku-class; no cache blocks and no escalation. **Invent
nothing** — it restates the deterministic facts it is given and never produces the
attribution itself (the S6 deterministic core is the source of truth, S6-Q-002).
If it fails, the caller (the v1 enricher) caches nothing and falls back to the
deterministic attribution.

Feature-gated via `tenant_agent_settings.llm_enable_interpretation_phrasing`
(migration 050). Default off.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "interpretation_phrasing@v1"
MAX_TOKENS = 500
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


SYSTEM = (
    "You restate a software-test interpretation in plain English for a QA "
    "reviewer.\n\n"
    "You are given a DETERMINISTIC interpretation of one test run — the run "
    "outcome, a semantic verdict, an evidence-derived attribution sentence, and "
    "(optionally) a structured cause. These facts are the ground truth. Your "
    "ONLY job is to rephrase them clearly.\n\n"
    "Output JSON with exactly two fields:\n"
    "- headline: one short line (max ~100 chars) naming what happened — the "
    "verdict in plain words.\n"
    "- explanation: 2-3 sentences of plain English explaining the outcome and, "
    "if a cause is given, why — so a reviewer understands it at a glance.\n\n"
    "Hard rules:\n"
    "- INVENT NOTHING. Use only the facts given (outcome, verdict, attribution, "
    "cause). Do NOT add causes, fixes, severity, blame, or any speculation the "
    "input does not state.\n"
    "- Do not contradict or soften the verdict / attribution — restate it "
    "faithfully.\n"
    "- No filler (\"This test...\", \"The purpose is...\"). Plain, direct English.\n"
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
    enricher validates the required fields afterwards."""
    raw = getattr(resp, "raw_text", None) or ""
    return _extract_json(raw)


def build(context: Dict[str, Any], *,
          tenant_id: int,
          recent_misses: Optional[list] = None) -> PromptSpec:
    """Build a PromptSpec from the interpretation facts.

    Expected `context` keys (all the deterministic facts — nothing else reaches
    the model, so it cannot source new facts):
      - outcome: "passed" / "failed" / "errored"
      - verdict: the semantic Verdict string
      - attribution: the deterministic attribution sentence(s)
      - cause: {cause_kind, vr_name, detail} or None
    """
    payload: Dict[str, Any] = {
        "outcome": context.get("outcome"),
        "verdict": context.get("verdict"),
        "attribution": context.get("attribution"),
        "cause": context.get("cause"),
    }
    user_msg = json.dumps(payload, indent=2, ensure_ascii=False)

    return PromptSpec(
        messages=[{"role": "user", "content": user_msg}],
        system=[{"type": "text", "text": SYSTEM}],
        parse=_parse,
        max_tokens=MAX_TOKENS,
        context_for_log={
            "verdict": context.get("verdict"),
            "has_cause": context.get("cause") is not None,
        },
        has_cache_blocks=False,
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    """No escalation — Haiku is the single shot; failure caches nothing and the
    caller falls back to the deterministic attribution."""
    return False

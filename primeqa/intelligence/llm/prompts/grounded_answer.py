"""Grounded-answer phrasing prompt — Substrate 7 (D-163.3).

Phrases a bounded EVIDENCE block (retrieved deterministically by S7's recipes) into
a natural-language answer to the user's question. The keystone: the model answers
**only** from the evidence and never invents a fact not present in it — the same
deterministic-first discipline S6's `interpretation_phrasing` holds ("the LLM
phrases, never invents"). S7's `conversation/` package stays LLM-free; this task
lives in v1 and is invoked through the gateway by the slice-4 bridge.

Haiku-class, single shot: no cache blocks, no escalation. A failure degrades to a
refusal (the substrate already has the grounding; only the prose failed).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec

VERSION = "grounded_answer@v1"
MAX_TOKENS = 800
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


SYSTEM = (
    "You answer questions about a Salesforce release-testing system using ONLY the "
    "EVIDENCE provided.\n\n"
    "The evidence is a list of items, each with an id (E1, E2, ...), a source "
    "substrate, a kind, and data. It was retrieved deterministically for this "
    "question; it is the ONLY ground truth you may use.\n\n"
    "Rules:\n"
    "- Answer ONLY from the evidence. Never state a fact that is not present in it.\n"
    "- If the evidence does not contain the answer, say plainly that you cannot "
    "answer from the available data — do NOT guess or use outside knowledge.\n"
    "- Cite the evidence items you used by their ids.\n"
    "- Be concise (2-5 sentences). Every word earns its place.\n\n"
    "Output valid JSON with exactly these fields, nothing else:\n"
    "- answer: string. The grounded answer, or the cannot-answer statement.\n"
    "- cited_ids: array of strings. The evidence ids your answer relied on "
    "(empty if you could not answer)."
)


def detect_complexity(context: Dict[str, Any]) -> Optional[str]:
    """Always low — grounded phrasing runs on Haiku."""
    return "low"


def _extract_json(text: str) -> Optional[dict]:
    """Defensive JSON extraction (fenced blocks + stray prose)."""
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
    """Gateway parser hook → the parsed `{answer, cited_ids}` dict or None. The
    answerer treats a missing/blank `answer` as a refusal, so this only needs to
    produce a dict or None."""
    raw = getattr(resp, "raw_text", None) or ""
    return _extract_json(raw)


def build(context: Dict[str, Any], *,
          tenant_id: int,
          recent_misses: Optional[list] = None) -> PromptSpec:
    """Build a PromptSpec from the answering context.

    Expected `context` keys:
      - question: the user's NL question (str)
      - intent: the classified intent kind (str)
      - evidence: list of {id, source, kind, data} — the bounded evidence block
    """
    question = context.get("question") or ""
    intent = context.get("intent") or ""
    evidence = context.get("evidence") or []

    payload = {"question": question, "intent": intent, "evidence": evidence}
    user_msg = json.dumps(payload, indent=2, ensure_ascii=False, default=str)

    return PromptSpec(
        messages=[{"role": "user", "content": user_msg}],
        system=[{"type": "text", "text": SYSTEM}],
        parse=_parse,
        max_tokens=MAX_TOKENS,
        context_for_log={"intent": intent, "evidence_count": len(evidence)},
        has_cache_blocks=False,
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    """No escalation — Haiku is the single shot; a failure degrades to a refusal."""
    return False

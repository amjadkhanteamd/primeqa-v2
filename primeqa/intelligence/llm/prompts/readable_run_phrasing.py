"""Readable-run phrasing prompt — Haiku-level restatement (run RESULT).

Turns the DETERMINISTIC readable-run skeleton (what one completed S4 run did:
the staged test data, the value-bearing steps, expected vs actual, the recorded
result — all facts already grounded in the run's captured evidence + the
claim's assertion) into two QA-friendly fields:

  - plain_terms: one short past-tense paragraph a QA reviewer reads at a
    glance ("The test created an Opportunity with Loan Amount 5,000,000 … the
    system set Loan-to-Value (%) to 50, exactly as expected.");
  - step_narration: one plain sentence per given step, in the same order.

Same contract as the test-case phrasing prompt: short + Haiku-class, **invent
nothing**, and — critically for a RESULT — **never re-judge the outcome**: the
skeleton's recorded matched/did-not-match/result sentence is the verdict and
must be restated faithfully, never contradicted or softened. The deterministic
grounding validator (``readable_body_phrasing._validate_grounding``) rejects
any output that names a value/field/entity absent from the skeleton; the reader
falls back to the deterministic baseline. Feature-gated, default OFF.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec
from primeqa.intelligence.llm.prompts.readable_body_phrasing import (
    _extract_json,
)

VERSION = "readable_run_phrasing@v1"
MAX_TOKENS = 700
SUPPORTS_CACHE = False
SUPPORTS_ESCALATION = False


SYSTEM = (
    "You rewrite the RESULT of one completed software test run into plain "
    "English for a QA reviewer.\n\n"
    "You are given a STRUCTURED, already-verified run skeleton — the run's "
    "outcome, the test data it staged (field -> value), the steps it performed "
    "with the values it recorded, the expected result, and the recorded "
    "comparison (matched / did not match). These facts are the ground truth. "
    "Your ONLY job is to phrase them clearly, in the PAST tense (this already "
    "happened).\n\n"
    "Output JSON with exactly two fields:\n"
    "- plain_terms: one short paragraph (2-4 sentences) that tells, in plain "
    "business language, what the run did and what the result was.\n"
    "- step_narration: an array of short plain-English sentences, ONE per step "
    "in the given 'steps' list, in the same order (empty array if no steps).\n\n"
    "Hard rules:\n"
    "- INVENT NOTHING. Use ONLY the field names, values, numbers, records, "
    "entities and outcomes that appear in the skeleton. Do NOT introduce any "
    "field, value, number, threshold, object or cause the skeleton does not "
    "state.\n"
    "- NEVER re-judge the outcome. The skeleton's result (matched / did not "
    "match / the outcome word) is the verdict — restate it faithfully; do not "
    "soften, hedge, or contradict it, and do not speculate about why.\n"
    "- Use the business labels exactly as given (e.g. 'Loan Amount', not "
    "'Loan_Amount__c'). Never introduce an API name, flow name, or schema term.\n"
    "- No filler. Plain, direct, past-tense English.\n"
    "- Output must be valid JSON, nothing else."
)


def detect_complexity(context: Dict[str, Any]) -> Optional[str]:
    """Always low — restatement runs on Haiku."""
    return "low"


def _parse(resp) -> Optional[dict]:
    """Gateway parser hook (shared defensive JSON extraction)."""
    raw = getattr(resp, "raw_text", None) or ""
    return _extract_json(raw)


def build(context: Dict[str, Any], *,
          tenant_id: int,
          recent_misses: Optional[list] = None) -> PromptSpec:
    """Build a PromptSpec from the run-skeleton facts. Only the skeleton
    reaches the model, so it cannot source new facts.

    Expected ``context`` keys (from the readable-run skeleton): outcome,
    headline, narrative, test_data, supporting_field_count, steps, expected,
    result_sentence.
    """
    payload: Dict[str, Any] = {
        "outcome": context.get("outcome"),
        "headline": context.get("headline"),
        "test_data": context.get("test_data") or [],
        "supporting_field_count": context.get("supporting_field_count") or 0,
        "steps": context.get("steps") or [],
        "expected": context.get("expected"),
        "result_sentence": context.get("result_sentence"),
        "result_reading": context.get("narrative"),
    }
    user_msg = json.dumps(payload, indent=2, ensure_ascii=False)

    return PromptSpec(
        messages=[{"role": "user", "content": user_msg}],
        system=[{"type": "text", "text": SYSTEM}],
        parse=_parse,
        max_tokens=MAX_TOKENS,
        context_for_log={
            "outcome": context.get("outcome"),
            "n_steps": len(payload["steps"]),
        },
        has_cache_blocks=False,
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    """No escalation — a failed/ungrounded phrasing falls back to the
    deterministic baseline."""
    return False

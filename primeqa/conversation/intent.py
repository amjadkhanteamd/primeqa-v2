"""Deterministic intent classification (D-163.1, S7 slice 1).

`classify_intent` maps a natural-language question to one of the three fixed
phase-1 intents, or `None` (→ a deterministic clarify-refusal downstream). It is
**keyword, not LLM** (SPEC §3): the classifier picks *which fixed retrieval recipe*
runs — it never authors an arbitrary query. Letting the model choose retrieval is
letting it author its own grounding (the inversion the platform refuses).

Matching reuses the shared `primeqa.knowledge._text` word-boundary matcher (the same
inflection-aware matcher S5 domain-pack selection + S3 `detect_complexity` use) — so
S7 doesn't reinvent matching semantics. The import pulls in zero `primeqa.intelligence`
(the LLM-free package guard, D-163); S7→S5 is within the dependency law.
"""
from __future__ import annotations

from typing import Optional

from primeqa.conversation.model import Intent, IntentKind, QuestionContext
from primeqa.knowledge._text import matched_keywords

# Per-intent keyword sets — word-boundary + inflection-aware (so "failed",
# "drifting", "affects" match). Small + explicit; the intent set is OPEN (SPEC §3).
_INTENT_KEYWORDS: dict[IntentKind, tuple[str, ...]] = {
    "failure_cause": ("fail", "failure", "cause", "why", "error", "broke"),
    "grounding_drift": ("drift", "stale", "broken", "outdated", "risk", "still valid"),
    "impact": ("affect", "impact", "depend", "downstream", "what uses", "blast radius"),
}

# Deterministic tie-break when ≥2 intents match the same number of keywords
# (SPEC §3). Listing order is the priority.
_PRIORITY: tuple[IntentKind, ...] = ("failure_cause", "grounding_drift", "impact")
_PRIORITY_INDEX = {k: i for i, k in enumerate(_PRIORITY)}


def classify_intent(question_text: str,
                    context: QuestionContext) -> Optional[Intent]:
    """Classify `question_text` → an `Intent`, or `None` when no intent matches.

    Deterministic: count the distinct matched keywords per intent; the highest
    count wins; ties break by `_PRIORITY`. `None` (no match) drives a clarify-refusal
    in the answer stage. `impact`'s target rides the bounded `QuestionContext` (the
    picker), so classification is keyword-only — no NL entity extraction.
    """
    text = question_text or ""
    scored: list[tuple[int, IntentKind, tuple[str, ...]]] = []
    for kind in _PRIORITY:
        matched = matched_keywords(text, _INTENT_KEYWORDS[kind])
        if matched:
            scored.append((len(matched), kind, tuple(matched)))
    if not scored:
        return None
    # highest distinct-match count first; ties by fixed priority order.
    scored.sort(key=lambda s: (-s[0], _PRIORITY_INDEX[s[1]]))
    _, kind, matched = scored[0]
    return Intent(kind=kind, matched_keywords=matched)

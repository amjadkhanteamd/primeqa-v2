"""Substrate 7 — Conversation and Control.

The natural-language **answering** layer over the substrate spine: a user asks the
system about itself ("did yesterday's failures share a cause?", "what's drifted?",
"what's affected by this object?") and gets an answer **grounded in what the other
substrates recorded** — or a refusal when nothing grounds it.

This package is **Substrate 7** — see
`docs/architecture/substrate_7_conversation/SPEC.md` (opened D-163). It is the one
substrate that is primarily a **consumer**: it reads every other substrate's public
read API and **writes nothing**; it owns **no table** in phase 1.

**Keystone — grounded-or-refuse.** Every answer is grounded in substrate evidence
retrieved deterministically; empty evidence ⇒ a refusal produced *before* any LLM
call. The pipeline is deterministic-first; the LLM only phrases handed evidence.

**The package is LLM-free** (the S6/S8 invariant): the phrase step is an injected
callable; the real `llm_call` lives in v1. `import primeqa.conversation` pulls in
zero `primeqa.intelligence`.

Pipeline (stages land across D-163.1–D-163.4):

    classify_intent → retrieve_<intent> → assemble_evidence → build_answer

Public API (the opened contract — D-163):
    from primeqa.conversation import QuestionContext, Intent, Evidence, Answer
"""
from primeqa.conversation.intent import classify_intent
from primeqa.conversation.model import (
    Answer,
    AnswerStatus,
    Citation,
    Evidence,
    EvidenceItem,
    Intent,
    IntentKind,
    QuestionContext,
)

__all__ = [
    "QuestionContext",
    "Intent",
    "IntentKind",
    "EvidenceItem",
    "Evidence",
    "Citation",
    "Answer",
    "AnswerStatus",
    "classify_intent",
]

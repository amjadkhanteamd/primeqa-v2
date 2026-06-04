"""Grounded-or-refuse answer construction (D-163.3, S7 slice 3).

`build_answer` turns bounded `Evidence` into an `Answer`. The keystone: **empty
evidence ⇒ refused, deterministically, before any phrasing** — the model never sees
an empty block, so refusal is a substrate decision, never the model's (D-073). When
evidence is present, the **injected** `phrase_fn` phrases over only that evidence,
and S7 returns the **evidence's** citations (never citations the model claims).

The phrase step is injected (a callable) so this module — and the whole
`conversation/` package — stays LLM-free; the real `llm_call` is wired in the
slice-4 bridge (v1). Best-effort: a phrasing failure degrades to a refusal, never
raises.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from primeqa.conversation.model import Answer, Citation, Evidence

log = logging.getLogger(__name__)

# (question, evidence) -> {"answer": str, "cited_ids": [str]} | None
PhraseFn = Callable[[str, Evidence], Optional[dict]]

_REFUSAL_NO_EVIDENCE = "no_grounding_evidence"
_REFUSAL_PHRASING_FAILED = "phrasing_unavailable"

_NO_EVIDENCE_TEXT = (
    "I can't answer that from the available data — no grounding evidence was found "
    "for your question. Try narrowing it to a specific release, environment, or "
    "requirement.")
_PHRASING_FAILED_TEXT = (
    "I found grounding evidence but couldn't compose an answer right now. "
    "Please try again.")

# Keys probed (in order) for a compact, audit-readable citation ref.
_REF_KEYS = ("run_id", "test_id", "sf_api_name", "vr_name", "cause_kind", "overall")


def _ref(item) -> str:
    data = item.data or {}
    for key in _REF_KEYS:
        if data.get(key):
            return f"{key}={data[key]}"
    return item.kind


def _citations(evidence: Evidence) -> tuple[Citation, ...]:
    return tuple(
        Citation(citation_id=it.citation_id, source=it.source, kind=it.kind,
                 ref=_ref(it))
        for it in evidence.items)


def build_answer(evidence: Evidence, *, question: str,
                 phrase_fn: PhraseFn) -> Answer:
    """Build the `Answer` for a question from its bounded `Evidence`.

    Grounded-or-refuse: empty evidence ⇒ `refused` (no phrasing call). Otherwise the
    injected `phrase_fn` phrases over only this evidence; S7 returns the evidence's
    `E{n}` citations. A `phrase_fn` that returns no usable answer (None / blank /
    raises) degrades to a `refused` carrying those citations.
    """
    if evidence.is_empty:
        return Answer(status="refused", text=_NO_EVIDENCE_TEXT,
                      refusal_reason=_REFUSAL_NO_EVIDENCE)

    citations = _citations(evidence)
    parsed: Any = None
    try:
        parsed = phrase_fn(question, evidence)
    except Exception as exc:                    # best-effort — never raise to caller
        log.warning("grounded_answer phrasing failed: %s", exc)

    text = parsed.get("answer") if isinstance(parsed, dict) else None
    if not text or not str(text).strip():
        return Answer(status="refused", text=_PHRASING_FAILED_TEXT,
                      citations=citations, refusal_reason=_REFUSAL_PHRASING_FAILED)

    # Soft citation back-check (S7-Q-001): log a no-citation answer; do NOT downgrade
    # in phase 1. S7 always returns the EVIDENCE's citations, not the model's claims.
    cited = parsed.get("cited_ids") if isinstance(parsed, dict) else None
    if not cited:
        log.info("grounded_answer returned no cited_ids (soft-logged, not downgraded)")

    return Answer(status="answered", text=str(text).strip(), citations=citations)

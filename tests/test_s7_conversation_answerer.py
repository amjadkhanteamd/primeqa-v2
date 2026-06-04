"""S7 grounded-or-refuse answerer + the grounded_answer prompt task (D-163.3).

No DB. The answerer is exercised with a STUBBED phrase_fn (the LLM is never really
called) — the grounded-or-refuse keystone, the evidence-citations invariant, and
the graceful-degrade paths. The prompt-task half checks the v1 registration +
build/parse shape (the conversation/ package stays LLM-free).
"""
from __future__ import annotations

import pytest

from primeqa.conversation import Evidence, EvidenceItem, build_answer

pytestmark = pytest.mark.unit


def _ev(n=1, intent="failure_cause"):
    items = tuple(
        EvidenceItem(citation_id=f"E{i + 1}", source="S6", kind="interpretation",
                     data={"run_id": f"r{i}"})
        for i in range(n))
    return Evidence(intent=intent, items=items)


class _Recorder:
    """A stub phrase_fn that records calls + returns a fixed value."""
    def __init__(self, ret):
        self.ret = ret
        self.calls = []

    def __call__(self, question, evidence):
        self.calls.append((question, evidence))
        return self.ret


# --- the grounded-or-refuse keystone ----------------------------------------

def test_empty_evidence_refuses_without_calling_phrase_fn():
    rec = _Recorder({"answer": "must not be used"})
    ans = build_answer(Evidence(intent="failure_cause"), question="why?", phrase_fn=rec)
    assert ans.status == "refused" and ans.refusal_reason == "no_grounding_evidence"
    assert ans.citations == ()
    assert rec.calls == []                      # the LLM is NEVER invoked on empty evidence


def test_answered_returns_evidence_citations_not_model_claims():
    rec = _Recorder({"answer": "Two runs failed on VR_A.", "cited_ids": ["E99", "Exyz"]})
    ans = build_answer(_ev(2), question="why did they fail?", phrase_fn=rec)
    assert ans.status == "answered" and ans.text == "Two runs failed on VR_A."
    # citations are the EVIDENCE's E1/E2 — never the model's claimed E99/Exyz.
    assert [c.citation_id for c in ans.citations] == ["E1", "E2"]
    assert rec.calls and rec.calls[0][0] == "why did they fail?"


# --- graceful degrade (best-effort) -----------------------------------------

def test_phrase_fn_none_degrades_to_refused_with_citations():
    ans = build_answer(_ev(1), question="q", phrase_fn=lambda q, e: None)
    assert ans.status == "refused" and ans.refusal_reason == "phrasing_unavailable"
    assert [c.citation_id for c in ans.citations] == ["E1"]


def test_phrase_fn_blank_answer_degrades_to_refused():
    ans = build_answer(_ev(1), question="q", phrase_fn=lambda q, e: {"answer": "  "})
    assert ans.status == "refused" and ans.refusal_reason == "phrasing_unavailable"


def test_phrase_fn_raises_degrades_gracefully():
    def boom(q, e):
        raise RuntimeError("gateway down")
    ans = build_answer(_ev(1), question="q", phrase_fn=boom)
    assert ans.status == "refused" and ans.refusal_reason == "phrasing_unavailable"


def test_citation_ref_is_audit_readable():
    ev = Evidence(intent="grounding_drift", items=(EvidenceItem(
        citation_id="E1", source="S8", kind="grounding_validity",
        data={"test_id": "t-123", "overall": "drifted"}),))
    ans = build_answer(ev, question="q",
                       phrase_fn=lambda q, e: {"answer": "x", "cited_ids": ["E1"]})
    assert ans.citations[0].ref == "test_id=t-123"


# --- the grounded_answer prompt task (v1) -----------------------------------

def test_grounded_answer_registered_and_routed():
    from primeqa.intelligence.llm import router
    from primeqa.intelligence.llm.prompts import registry
    mod = registry.get("grounded_answer_generation")
    assert mod.VERSION == "grounded_answer@v1"
    assert mod.SUPPORTS_ESCALATION is False and mod.SUPPORTS_CACHE is False
    assert "grounded_answer_generation" in router._CHAINS


def test_grounded_answer_build_and_parse():
    from primeqa.intelligence.llm.prompts import grounded_answer as ga
    from primeqa.intelligence.llm.prompts.base import PromptSpec
    spec = ga.build(
        {"question": "why?", "intent": "failure_cause",
         "evidence": [{"id": "E1", "source": "S6", "kind": "interpretation", "data": {}}]},
        tenant_id=1)
    assert isinstance(spec, PromptSpec)
    assert spec.has_cache_blocks is False and spec.max_tokens == 800
    assert spec.messages and spec.system
    assert ga.detect_complexity({}) == "low" and ga.should_escalate(None, None) is False

    class _R:
        raw_text = '```json\n{"answer": "hi", "cited_ids": ["E1"]}\n```'
    assert ga._parse(_R()) == {"answer": "hi", "cited_ids": ["E1"]}

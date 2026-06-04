"""S7 conversation bridge — governance on the semantic conn+seed harness (D-163.4).

Drives the pure inner ``_answer(s1, session, ...)`` end-to-end over seeded substrate
rows with a STUBBED phrase_fn (no real LLM): the answered path, the empty-store
refusal, the no-intent clarify-refusal, the impact read-through, and the
null-phrase degrade. Plus the best-effort ``answer_question`` wrapper.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from primeqa.conversation import QuestionContext
from primeqa.intelligence.conversation_bridge import _answer, answer_question
from primeqa.interpretation.model import Cause, Interpretation
from primeqa.interpretation.result_store import persist_interpretation
from primeqa.semantic.query import SemanticOrgModel

_CTX = QuestionContext(tenant_id=1)


def _stub_phrase(ret="Two runs failed on VR_A."):
    return lambda q, ev: {"answer": ret, "cited_ids": [ev.items[0].citation_id]}


def _interp(rid):
    return Interpretation(
        run_id=uuid4(), recipe_id=rid, claim_test_id=uuid4(), outcome="failed",
        verdict="prohibition_not_enforced", attribution="seeded",
        cause=Cause(cause_kind="enforcement_gap", vr_name="VR_A"))


def test_answered_failure_cause_over_seeded_s6(conn, seed):
    session = Session(bind=conn)
    rid = uuid4()
    for _ in range(2):
        persist_interpretation(session, _interp(rid))
    session.flush()
    out = _answer(None, session, question="why did these tests fail?",
                  ctx=_CTX, phrase_fn=_stub_phrase())
    assert out["available"] is True and out["status"] == "answered"
    assert out["text"] == "Two runs failed on VR_A."
    assert out["citations"] and out["citations"][0]["id"] == "E1"


def test_refused_on_empty_store(conn, seed):
    session = Session(bind=conn)
    out = _answer(None, session, question="why did these tests fail?",
                  ctx=_CTX, phrase_fn=_stub_phrase())
    assert out["status"] == "refused" and out["refusal_reason"] == "no_grounding_evidence"
    assert out["citations"] == []


def test_clarify_refusal_on_no_intent(conn, seed):
    session = Session(bind=conn)
    out = _answer(None, session, question="what's the weather today?",
                  ctx=_CTX, phrase_fn=_stub_phrase())
    assert out["status"] == "refused" and out["refusal_reason"] == "no_intent_match"
    assert out["citations"] == []


def test_impact_over_seeded_object(conn, seed):
    session = Session(bind=conn)
    v1 = seed.version()
    obj = seed.entity("Object", "Account", v1)
    fld = seed.entity("Field", "Account.Name", v1)
    seed.edge(fld, obj, "BELONGS_TO", "STRUCTURAL", v1)
    s1 = SemanticOrgModel(conn)
    out = _answer(s1, session, question="what is affected by this object?",
                  ctx=QuestionContext(tenant_id=1, object_api_name="Account"),
                  phrase_fn=_stub_phrase("Account has one field."))
    assert out["status"] == "answered"
    assert any(c["source"] == "S1" for c in out["citations"])


def test_null_phrase_degrades_to_refused_with_citations(conn, seed):
    session = Session(bind=conn)
    rid = uuid4()
    for _ in range(2):
        persist_interpretation(session, _interp(rid))
    session.flush()
    out = _answer(None, session, question="why did these fail?",
                  ctx=_CTX, phrase_fn=lambda q, ev: None)
    assert out["status"] == "refused" and out["refusal_reason"] == "phrasing_unavailable"
    assert out["citations"]                     # grounding present, just unphrased


def test_answer_question_best_effort_on_bad_tenant():
    # tenant -1 has no schema → get_tenant_connection fails → available=False, no raise.
    out = answer_question(-1, "why did these fail?")
    assert out["available"] is False and out["status"] == "refused"

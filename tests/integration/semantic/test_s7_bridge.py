"""S7 conversation bridge — governance on the semantic conn+seed harness (D-163.4).

Drives the pure inner ``_answer(s1, session, ...)`` end-to-end over seeded substrate
rows with a STUBBED phrase_fn (no real LLM): the answered path, the empty-store
refusal, the no-intent clarify-refusal, the impact read-through, and the
null-phrase degrade. Plus the best-effort ``answer_question`` wrapper.
"""
from __future__ import annotations

import contextlib
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
    # (env defaults None; the connection fails at __enter__ — before the D-292 env
    # check inside the with-body — so a bad tenant still yields the service-error
    # refusal, not the env-required one.)
    out = answer_question(-1, "why did these fail?")
    assert out["available"] is False and out["status"] == "refused"
    assert out["refusal_reason"] == "unavailable"   # the service-error refusal, NOT env-required


# ── D-292: org-scoping the S1 reader through the shared D-286 resolver ──────────
# Hermetic wrapper tests (monkeypatch only — no conn/seed/DB): they exercise the
# org-resolution branch in `answer_question` itself, not the pure inner `_answer`.

@contextlib.contextmanager
def _fake_conn(_tenant_id):
    yield object()                                    # a sentinel conn — never used


def test_answer_question_org_scopes_reader_to_resolved_org(monkeypatch):
    """D-292: env-selected → the reader is built org-scoped (ONE org via the shared
    resolver), not org-less — so a name-resolving read can't blend across orgs."""
    import primeqa.intelligence.conversation_bridge as bridge
    import primeqa.semantic.connection as conn_mod
    import primeqa.semantic.query as query_mod
    import primeqa.sync.credentials as cred_mod

    seen = {}

    class _FakeS1:
        def __init__(self, conn, connected_org_id=None, **kw):
            seen["org"] = connected_org_id            # capture the org binding

    def _fake_resolve(conn, environment_id):
        seen["env"] = environment_id
        return "ORG-ABC"

    def _capture_answer(s1, session, *, question, ctx, phrase_fn):
        seen["s1"] = s1                               # the org-scoped reader reaches _answer
        return {"available": True, "status": "answered", "text": "ok",
                "refusal_reason": None, "citations": []}

    monkeypatch.setattr(conn_mod, "get_tenant_connection", _fake_conn)
    monkeypatch.setattr(query_mod, "SemanticOrgModel", _FakeS1)
    monkeypatch.setattr(cred_mod, "resolve_connected_org_or_raise", _fake_resolve)
    monkeypatch.setattr(bridge, "_answer", _capture_answer)

    out = answer_question(1, "what is affected by Account?", environment_id=42,
                          object_api_name="Account")
    assert out["status"] == "answered"
    assert seen["env"] == 42                          # resolved through the SHARED resolver
    assert seen["org"] == "ORG-ABC"                   # reader bound to ONE org, not None
    assert isinstance(seen.get("s1"), _FakeS1)        # that org-scoped reader reached _answer


def test_answer_question_refuses_when_env_is_none(monkeypatch):
    """D-292: environment_id=None → graceful in-band refusal (available=True,
    refusal_reason=environment_required); never available=False, never a reader."""
    import primeqa.semantic.connection as conn_mod
    import primeqa.semantic.query as query_mod

    built = {"s1": False}

    class _FakeS1:
        def __init__(self, *a, **k):
            built["s1"] = True                        # must NOT be reached

    monkeypatch.setattr(conn_mod, "get_tenant_connection", _fake_conn)
    monkeypatch.setattr(query_mod, "SemanticOrgModel", _FakeS1)

    out = answer_question(1, "why did these fail?", environment_id=None)
    assert out["available"] is True and out["status"] == "refused"
    assert out["refusal_reason"] == "environment_required"
    assert out["citations"] == []
    assert built["s1"] is False                       # no org-less reader was constructed


def test_answer_question_refuses_when_resolver_raises(monkeypatch):
    """D-292: env present but unprovisioned → resolver raises OrgResolutionError →
    the SAME graceful refusal (available=True, environment_required) — NOT
    _unavailable() (available=False) and NOT an uncaught 500."""
    import primeqa.semantic.connection as conn_mod
    import primeqa.semantic.query as query_mod
    import primeqa.sync.credentials as cred_mod

    built = {"s1": False}

    class _FakeS1:
        def __init__(self, *a, **k):
            built["s1"] = True                        # must NOT be reached

    def _raise(conn, environment_id):
        raise cred_mod.OrgResolutionError("no connected_org for env")

    monkeypatch.setattr(conn_mod, "get_tenant_connection", _fake_conn)
    monkeypatch.setattr(query_mod, "SemanticOrgModel", _FakeS1)
    monkeypatch.setattr(cred_mod, "resolve_connected_org_or_raise", _raise)

    out = answer_question(1, "why did these fail?", environment_id=7)
    assert out["available"] is True and out["status"] == "refused"
    assert out["refusal_reason"] == "environment_required"
    assert built["s1"] is False                       # refused before building a reader


def test_answer_question_unavailable_when_resolver_db_errors(monkeypatch):
    """D-292.1: a resolver failure that is NOT OrgResolutionError (e.g. a transient
    DB fault) is NOT the env-required refusal — it falls to the outer except ->
    _unavailable() (available=False, refusal_reason=unavailable). No 500, and the
    env-required refusal is reserved for a genuinely missing/unresolvable org."""
    import primeqa.semantic.connection as conn_mod
    import primeqa.semantic.query as query_mod
    import primeqa.sync.credentials as cred_mod

    built = {"s1": False}

    class _FakeS1:
        def __init__(self, *a, **k):
            built["s1"] = True                        # must NOT be reached

    def _db_error(conn, environment_id):
        raise RuntimeError("transient DB fault during org resolution")

    monkeypatch.setattr(conn_mod, "get_tenant_connection", _fake_conn)
    monkeypatch.setattr(query_mod, "SemanticOrgModel", _FakeS1)
    monkeypatch.setattr(cred_mod, "resolve_connected_org_or_raise", _db_error)

    out = answer_question(1, "why did these fail?", environment_id=7)
    assert out["available"] is False and out["status"] == "refused"
    assert out["refusal_reason"] == "unavailable"     # service condition, NOT environment_required
    assert built["s1"] is False

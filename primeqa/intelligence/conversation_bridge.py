"""S7 conversation bridge (D-163.4) — v1's best-effort `answer_question`.

v1 owns the bridge (the allowed v1→substrate direction, like `substrate_insights` +
`interpretation_phrasing`). It opens one tenant connection and derives **both** an S1
reader (`SemanticOrgModel`) and an ORM `Session` from it (the verified
`evolution/recompute.py` dual-derivation), then runs the whole S7 faculty —
classify → retrieve → bounded-assemble → grounded-or-refuse — phrasing through the
gateway when an LLM `api_key` is available.

Best-effort: any failure (absent tenant schema, read error) returns
`available=False`; never raises (the `get_substrate_insights` contract). The
`conversation/` package stays LLM-free — the phrase step is built here, in v1.
"""
from __future__ import annotations

import logging

from primeqa.conversation import (
    QuestionContext,
    assemble_evidence,
    build_answer,
    classify_intent,
    retrieve,
)

log = logging.getLogger(__name__)

# A clarify-refusal for a question that matched no intent (classify → None).
_CLARIFY_TEXT = (
    "I can answer about failure causes, grounding drift, or impact — "
    "could you rephrase your question?")


def _answer_dict(answer) -> dict:
    return {
        "available": True,
        "status": answer.status,
        "text": answer.text,
        "refusal_reason": answer.refusal_reason,
        "citations": [
            {"id": c.citation_id, "source": c.source, "kind": c.kind, "ref": c.ref}
            for c in answer.citations],
    }


def _unavailable() -> dict:
    return {"available": False, "status": "refused",
            "text": "The answering service is temporarily unavailable.",
            "refusal_reason": "unavailable", "citations": []}


# D-292: a question with no usable environment to ground against — the caller
# passed no env (or one they can't access; views.py drops it, D-245), or the env
# has no connected org yet (the resolver raises). A GRACEFUL in-band refusal: the
# `available: True` shape (like `no_intent_match`), NOT `_unavailable()` (which
# means the service itself is down) and never a raised 500. We refuse rather than
# fall back org-agnostic — the silent fallback would re-introduce the cross-org
# blend the org-scoping removes.
_NO_ORG_TEXT = (
    "I can only answer about a specific connected environment's org. Select an "
    "environment you can access that's been connected to an org, then ask again.")


def _no_org() -> dict:
    return {"available": True, "status": "refused", "text": _NO_ORG_TEXT,
            "refusal_reason": "environment_required", "citations": []}


def _make_phrase_fn(tenant_id, api_key, model):
    """The injected phrase step. With no api_key, a null phrase_fn → `build_answer`
    degrades to a refused-with-citations (the page works without an LLM)."""
    if not api_key:
        return lambda question, evidence: None
    from primeqa.intelligence.llm.gateway import llm_call

    def phrase_fn(question, evidence):
        resp = llm_call(
            task="grounded_answer_generation", tenant_id=tenant_id, api_key=api_key,
            model_override=model,
            context={
                "question": question, "intent": evidence.intent,
                "evidence": [{"id": it.citation_id, "source": it.source,
                              "kind": it.kind, "data": it.data}
                             for it in evidence.items]})
        return resp.parsed_content

    return phrase_fn


def _answer(s1, session, *, question, ctx, phrase_fn) -> dict:
    """Pure inner: the whole faculty over already-opened readers. Directly testable
    on a tenant-scoped session + s1 (the `_assemble_insights` pattern)."""
    intent = classify_intent(question, ctx)
    if intent is None:
        return {"available": True, "status": "refused", "text": _CLARIFY_TEXT,
                "refusal_reason": "no_intent_match", "citations": []}
    items = retrieve(s1, session, intent.kind, ctx)
    evidence = assemble_evidence(intent.kind, items)
    answer = build_answer(evidence, question=question, phrase_fn=phrase_fn)
    return _answer_dict(answer)


def answer_question(tenant_id, question_text, *, environment_id=None,
                    requirement_key=None, object_api_name=None,
                    api_key=None, model=None) -> dict:
    """Best-effort grounded answer for the `/ask` page. Returns a JSON-safe dict;
    never raises. `available=False` on any failure."""
    try:
        from sqlalchemy.orm import Session

        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.semantic.query import SemanticOrgModel
        from primeqa.sync.credentials import (OrgResolutionError,
                                              resolve_connected_org_or_raise)

        ctx = QuestionContext(
            tenant_id=tenant_id, environment_id=environment_id,
            requirement_key=requirement_key, object_api_name=object_api_name)
        phrase_fn = _make_phrase_fn(tenant_id, api_key, model)
        with get_tenant_connection(tenant_id) as conn:
            # D-292: org-scope the S1 reader to the question's environment via the
            # shared D-286 resolver, so the ONE name-resolving S1 read
            # (retrieve_impact's object branch) scopes to that org instead of
            # blending all orgs on a multi-org tenant (the req-283 break class).
            # D-292.1 (product decision, after the adversarial review): /ask is
            # STRICTLY environment-scoped — every answer is grounded in one
            # connected org. So a None environment_id (the caller lacks env access,
            # views.py drops it per D-245; or no env was supplied) REFUSES in-band —
            # not just for the blend-prone object lookup but for ALL intents,
            # including the env-independent S6 failure-cause / S8 grounding-drift
            # faculties, by deliberate choice (the env selector is now `required`,
            # so this is the no-access / programmatic backstop, not the happy path).
            # An env present-but-unprovisioned makes the resolver raise
            # OrgResolutionError → the SAME refusal (never a 500); any OTHER resolver
            # error (e.g. a transient DB fault) falls to the outer except →
            # _unavailable(), a service condition. [Isolation: to instead answer the
            # env-independent intents org-blind, pass `s1 = None` here when env is
            # None — the object branch declines on its own `s1 is not None` guard.]
            if ctx.environment_id is None:
                return _no_org()
            try:
                org_id = resolve_connected_org_or_raise(conn, ctx.environment_id)
            except OrgResolutionError:
                return _no_org()
            s1 = SemanticOrgModel(conn, connected_org_id=org_id)
            session = Session(bind=conn)
            return _answer(s1, session, question=question_text, ctx=ctx,
                           phrase_fn=phrase_fn)
    except Exception as exc:                 # absent schema / read error / LLM error
        log.warning("conversation answer unavailable for tenant %s: %s",
                    tenant_id, exc)
        return _unavailable()

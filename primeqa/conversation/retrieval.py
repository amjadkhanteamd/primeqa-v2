"""Deterministic retrieval recipes (D-163.2, S7 slice 2).

One recipe per intent — a pure function over **injected** substrate readers (an ORM
``session`` + a ``SemanticOrgModel`` ``s1``), returning the flattened
``EvidenceItem``s for that question. No LLM, no connection-management (the slice-4
bridge injects the readers + owns best-effort). Each recipe reads only the
substrate's public API (S7→{S6,S8,S2,S1} — the dependency law); these imports pull
in zero ``primeqa.intelligence`` (the LLM-free package guard, D-163).

Uniform signature ``retrieve_<intent>(s1, session, ctx)`` so the answerer/bridge can
dispatch by ``Intent.kind`` without special-casing.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from primeqa.conversation.model import EvidenceItem, IntentKind, QuestionContext
from primeqa.evolution import list_grounding_validity
from primeqa.interpretation import (
    cluster_by_vr,
    cluster_recurring_causes,
    list_interpretations,
)
from primeqa.semantic.query import SemanticOrgModel, VersionNotFoundError
from primeqa.test_representation import SemanticTransactionCoordinator

# Per-recipe read bounds (the substrate list-bound convention; the assembler
# applies the final evidence bound).
_INTERP_LIMIT = 50
_GV_LIMIT = 50

# The object's "impact surface": inbound edges name the things that touch it —
# its fields + VRs (BELONGS_TO / APPLIES_TO), the fields that reference it
# (HAS_RELATIONSHIP_TO), and the VR field-refs (REFERENCES). The verified S1 edge
# taxonomy (semantic/derivation.py).
_IMPACT_EDGES = ["BELONGS_TO", "APPLIES_TO", "HAS_RELATIONSHIP_TO", "REFERENCES"]


def _as_uuid(value) -> Optional[UUID]:
    if not value:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


# --- flatteners: substrate read DTOs -> EvidenceItem (natural ref in data) ----

def _interp_item(r) -> EvidenceItem:
    return EvidenceItem(
        citation_id=f"S6:interp:{r.run_id}", source="S6", kind="interpretation",
        data={"run_id": str(r.run_id), "recipe_id": str(r.recipe_id),
              "claim_test_id": str(r.claim_test_id), "outcome": r.outcome,
              "verdict": r.verdict,
              "cause_kind": (r.cause.cause_kind if r.cause else None),
              "vr_name": (r.cause.vr_name if r.cause else None)})


def _cause_cluster_item(c) -> EvidenceItem:
    return EvidenceItem(
        citation_id=f"S6:cause:{c.cause_kind}", source="S6", kind="cause_cluster",
        data={"cause_kind": c.cause_kind, "count": c.count,
              "run_ids": [str(x) for x in c.run_ids]})


def _vr_cluster_item(c) -> EvidenceItem:
    return EvidenceItem(
        citation_id=f"S6:vr:{c.vr_name}", source="S6", kind="vr_cluster",
        data={"vr_name": c.vr_name, "count": c.count, "outcomes": list(c.outcomes),
              "run_ids": [str(x) for x in c.run_ids]})


def _grounding_item(r) -> EvidenceItem:
    return EvidenceItem(
        citation_id=f"S8:{r.test_id}:v{r.version_seq}", source="S8",
        kind="grounding_validity",
        data={"test_id": str(r.test_id), "version_seq": r.version_seq,
              "overall": r.overall, "claim_verdict": r.claim_verdict,
              "evaluated_at_version_seq": r.evaluated_at_version_seq,
              # per-org grounding (3f): which org the verdict grounds against
              "connected_org_id": (str(r.connected_org_id)
                                   if r.connected_org_id else None)})


def _entity_item(e) -> EvidenceItem:
    return EvidenceItem(
        citation_id=f"S1:{e.entity_type}:{e.sf_api_name}", source="S1", kind="entity",
        data={"entity_type": e.entity_type, "sf_api_name": e.sf_api_name,
              "display_name": e.display_name})


def _related_item(rel) -> EvidenceItem:
    fe = rel.entity
    return EvidenceItem(
        citation_id=f"S1:edge:{rel.edge_type}:{fe.sf_api_name}", source="S1",
        kind="related_entity",
        data={"edge_type": rel.edge_type, "edge_category": rel.edge_category,
              "direction": rel.direction, "entity_type": fe.entity_type,
              "sf_api_name": fe.sf_api_name})


def _req_match_item(m) -> EvidenceItem:
    return EvidenceItem(
        citation_id=f"S2:test:{m.test_id}", source="S2", kind="linked_test",
        data={"test_id": str(m.test_id), "link_kind": m.link_kind,
              "external_version": m.external_version})


# --- the recipes (uniform (s1, session, ctx); pure over injected readers) ------

def retrieve_failure_cause(s1, session, ctx: QuestionContext) -> tuple[EvidenceItem, ...]:
    """S6: recorded interpretations + the recurring-cause / by-VR clusters. Phrases
    the deterministic attribution S6 already recorded (S7 adds no judgment)."""
    rid = _as_uuid(ctx.recipe_id)
    items: list[EvidenceItem] = []
    items += [_interp_item(r)
              for r in list_interpretations(session, recipe_id=rid, limit=_INTERP_LIMIT)]
    items += [_cause_cluster_item(c)
              for c in cluster_recurring_causes(session, recipe_id=rid, min_runs=2)]
    items += [_vr_cluster_item(c)
              for c in cluster_by_vr(session, recipe_id=rid, min_runs=2)]
    return tuple(items)


def retrieve_grounding_drift(s1, session, ctx: QuestionContext) -> tuple[EvidenceItem, ...]:
    """S8: the recorded grounding-validity verdicts for the at-risk states
    (``drifted`` / ``broken``) — the ``overall``-indexed standing-verdict query."""
    tid = _as_uuid(ctx.test_id)
    items: list[EvidenceItem] = []
    for overall in ("drifted", "broken"):
        items += [_grounding_item(r) for r in list_grounding_validity(
            session, test_id=tid, overall=overall, limit=_GV_LIMIT)]
    return tuple(items)


def retrieve_impact(s1: SemanticOrgModel, session,
                    ctx: QuestionContext) -> tuple[EvidenceItem, ...]:
    """S1 (+ S2): what touches the target. Object branch — resolve the Object and
    walk its inbound edges (its impact surface). Requirement branch — the tests
    linked to the requirement. Target comes from the bounded context (the picker)."""
    items: list[EvidenceItem] = []
    if ctx.object_api_name and s1 is not None:
        try:
            seq = s1.current_version_seq()
        except VersionNotFoundError:
            seq = None
        if seq is not None:
            for obj in s1.get_entities(
                    "Object", at_seq=seq,
                    filters={"sf_api_name": ctx.object_api_name}):
                items.append(_entity_item(obj))
                items += [_related_item(rel) for rel in s1.get_related(
                    obj.id, edge_types=_IMPACT_EDGES, direction="inbound", at_seq=seq)]
    if ctx.requirement_key and session is not None:
        coord = SemanticTransactionCoordinator()
        items += [_req_match_item(m) for m in coord.list_tests_by_requirement(
            session, external_system="jira", external_key=ctx.requirement_key)]
    return tuple(items)


# Dispatch table — keyed by Intent.kind (the classifier's output).
_RECIPES = {
    "failure_cause": retrieve_failure_cause,
    "grounding_drift": retrieve_grounding_drift,
    "impact": retrieve_impact,
}


def retrieve(s1, session, intent_kind: IntentKind,
             ctx: QuestionContext) -> tuple[EvidenceItem, ...]:
    """Dispatch to the recipe for ``intent_kind``. Returns ``()`` for an unknown
    kind (defensive; the classifier only emits the three registered kinds)."""
    recipe = _RECIPES.get(intent_kind)
    if recipe is None:
        return ()
    return recipe(s1, session, ctx)

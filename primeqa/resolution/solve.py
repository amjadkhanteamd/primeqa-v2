"""Phase B — the joint solve: rank each entity node's candidates by how much
of the node's OWN sub-graph they structurally bind, then grade by dominance.

Ranking is lexicographic dominance, never a weighted sum (weighted blends are
where ranking bugs breed — the D-364 class):

    (structural coverage, exact label, context affinity, lexical score)

A winner exists only when its key tuple STRICTLY exceeds the runner-up's;
equal tuples are a tie and ties are never picked (``AMBIGUOUS``). The solver
is a VERIFIER/GATE by contract (D-376): callers compare its dominant winner
against a name they already hold — it never substitutes silently.
"""
from __future__ import annotations

from typing import Optional

from primeqa.resolution import candidates as cand
from primeqa.resolution.graph import BusinessGraph, GraphNode
from primeqa.resolution.resolved import (
    AMBIGUOUS, BOUND_UNIQUE, BOUND_WEAK, UNRESOLVED, Binding,
    CandidateEvidence, ResolvedGraph)
from primeqa.resolution.symbols import ObjectSymbol, SymbolTable

# Disclosure cap: how many ranked candidates ride on an AMBIGUOUS/UNRESOLVED
# binding (the B0 idiom: a short ranked set, never a directory).
DISCLOSE_LIMIT = 3


def _coverage(graph: BusinessGraph, entity: GraphNode, obj: ObjectSymbol
              ) -> tuple[int, int, dict[str, Optional[str]]]:
    """(bound, total, per-node resolution) of ``entity``'s attribute + state
    sub-mentions against ``obj``'s own inventory."""
    bound = 0
    total = 0
    resolved: dict[str, Optional[str]] = {}
    for attr in graph.attributes_of(entity.node_id):
        total += 1
        fld = cand.resolve_field(obj, attr.term)
        resolved[attr.node_id] = fld.api_name if fld else None
        if fld:
            bound += 1
        for st in graph.states_of(attr.node_id):
            total += 1
            value = cand.resolve_state(fld, st.term)
            resolved[st.node_id] = value
            if value:
                bound += 1
    return bound, total, resolved


def _key(coverage_bound: int, ev: CandidateEvidence) -> tuple:
    f = ev.features
    return (coverage_bound, bool(f.get("exact_label")) or bool(f.get("exact_api")),
            int(f.get("context") or 0), float(f.get("lexical") or 0.0))


def resolve(graph: BusinessGraph, table: SymbolTable, *,
            requirement_text: Optional[str] = None) -> ResolvedGraph:
    """Resolve every entity node (and, through the winner, its attribute and
    state nodes). A malformed graph resolves to all-UNRESOLVED rather than
    raising — resolution refuses, it does not guess."""
    bindings: dict[str, Binding] = {}
    primary_cov = (0, 0)
    if graph.validate():
        for n in graph.nodes:
            bindings[n.node_id] = Binding(node_id=n.node_id, grade=UNRESOLVED)
        return ResolvedGraph(bindings=bindings, structural_coverage=(0, 0),
                             s1_version_seq=table.at_seq,
                             connected_org_id=table.connected_org_id)

    entity_ids = {n.node_id for n in graph.entity_nodes()}
    for i, entity in enumerate(graph.entity_nodes()):
        pool = cand.object_candidates(entity.term, table, requirement_text)
        scored = []
        for obj, ev in pool:
            bound, total, resolved = _coverage(graph, entity, obj)
            ev = CandidateEvidence(
                sf_api_name=ev.sf_api_name, entity_type=ev.entity_type,
                features={**ev.features, "coverage": [bound, total]})
            scored.append((_key(bound, ev), obj, ev, (bound, total), resolved))
        scored.sort(key=lambda t: (-t[0][0], -int(t[0][1]), -t[0][2],
                                   -t[0][3], t[1].api_name))

        if not scored:
            bindings[entity.node_id] = Binding(node_id=entity.node_id,
                                               grade=UNRESOLVED)
            cov = (0, 0)
        else:
            key0, obj0, ev0, cov, resolved0 = scored[0]
            tied = len(scored) > 1 and scored[1][0] == key0
            if tied:
                bindings[entity.node_id] = Binding(
                    node_id=entity.node_id, grade=AMBIGUOUS,
                    structural_coverage=cov,
                    candidates=tuple(ev for _, _, ev, _, _ in
                                     scored[:DISCLOSE_LIMIT]))
            else:
                f = ev0.features
                structural = cov[0] >= 1
                nominal = bool(f.get("exact_api")) or bool(f.get("exact_label"))
                grade = BOUND_UNIQUE if (structural or nominal) else BOUND_WEAK
                matched_via = ("exact_api" if f.get("exact_api")
                               else "exact_label" if f.get("exact_label")
                               else "joint" if structural else "lexical")
                bindings[entity.node_id] = Binding(
                    node_id=entity.node_id, grade=grade, entity_type="Object",
                    sf_api_name=obj0.api_name, entity_id=obj0.entity_id,
                    matched_via=matched_via, structural_coverage=cov,
                    candidates=tuple(ev for _, _, ev, _, _ in
                                     scored[:DISCLOSE_LIMIT]))
                # bind the dependent nodes through the winner
                for node_id, api in resolved0.items():
                    node = graph.node(node_id)
                    kind = "Field" if node and node.kind == "attribute" else "PicklistValue"
                    bindings[node_id] = Binding(
                        node_id=node_id,
                        grade=BOUND_UNIQUE if api else UNRESOLVED,
                        entity_type=kind if api else None,
                        sf_api_name=api, matched_via="ladder" if api else None)
        if i == 0:
            primary_cov = cov if scored else (0, 0)

    # any node not yet bound (attributes of an unresolved/ambiguous entity)
    for n in graph.nodes:
        if n.node_id not in bindings and n.node_id not in entity_ids:
            bindings[n.node_id] = Binding(node_id=n.node_id, grade=UNRESOLVED)

    return ResolvedGraph(bindings=bindings, structural_coverage=primary_cov,
                         s1_version_seq=table.at_seq,
                         connected_org_id=table.connected_org_id)


def dominant_entity(resolved: ResolvedGraph, node_id: str) -> Optional[Binding]:
    """The entity node's strictly-dominant winner, or ``None`` when resolution
    refused to pick (AMBIGUOUS) or found nothing (UNRESOLVED)."""
    b = resolved.binding(node_id)
    if b is None or b.grade not in (BOUND_UNIQUE, BOUND_WEAK):
        return None
    return b

"""BusinessGraph — the generation-agnostic business-vocabulary artifact.

A BusinessGraph carries business language ONLY: node terms are whatever the
author (a model, an adapter, a human) said in business words — never resolved
implementation identifiers. Resolution (``solve.resolve``) is the sole step
that maps terms onto org metadata, and it does so with evidence.

The contract is deliberately generic (no generation-specific fields): S3
consumes it through its own adapter (``primeqa.generation.shadow_resolution``);
future consumers (impact, documentation) build their own graphs against the
same shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

NODE_KINDS = ("entity", "attribute", "state", "actor")
EDGE_KINDS = ("attribute_of", "state_of", "related_to", "effect_on")


@dataclass(frozen=True)
class GraphNode:
    """One business mention: ``term`` is the verbatim business word(s);
    ``excerpt`` anchors it to source text (Guardrail-3 style); ``gloss`` is an
    optional normalized phrase. No implementation identifiers — an API-shaped
    string may appear as a *term* (an adapter passing through what a model
    said) but the graph assigns it no implementation meaning."""

    node_id: str
    kind: str
    term: str
    excerpt: str = ""
    gloss: str = ""


@dataclass(frozen=True)
class GraphEdge:
    """A typed relation between two nodes (``src``/``dst`` are node_ids):
    ``attribute_of`` (attribute → entity), ``state_of`` (state → attribute),
    ``related_to`` (entity → entity), ``effect_on`` (entity → entity)."""

    kind: str
    src: str
    dst: str


@dataclass(frozen=True)
class BusinessGraph:
    """An immutable node/edge set. ``validate()`` returns problems (empty =
    well-formed); resolution refuses malformed graphs rather than guessing."""

    nodes: tuple[GraphNode, ...] = field(default=())
    edges: tuple[GraphEdge, ...] = field(default=())

    def validate(self) -> list[str]:
        problems: list[str] = []
        seen: set[str] = set()
        for n in self.nodes:
            if not n.node_id:
                problems.append("node with empty node_id")
            elif n.node_id in seen:
                problems.append(f"duplicate node_id {n.node_id!r}")
            seen.add(n.node_id)
            if n.kind not in NODE_KINDS:
                problems.append(f"node {n.node_id!r}: unknown kind {n.kind!r}")
            if not (n.term or "").strip():
                problems.append(f"node {n.node_id!r}: empty term")
        for e in self.edges:
            if e.kind not in EDGE_KINDS:
                problems.append(f"edge {e.src!r}->{e.dst!r}: unknown kind {e.kind!r}")
            for end in (e.src, e.dst):
                if end not in seen:
                    problems.append(f"edge references unknown node {end!r}")
        return problems

    def node(self, node_id: str) -> Optional[GraphNode]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def entity_nodes(self) -> tuple[GraphNode, ...]:
        return tuple(n for n in self.nodes if n.kind == "entity")

    def attributes_of(self, entity_id: str) -> tuple[GraphNode, ...]:
        """Attribute nodes linked to ``entity_id`` via ``attribute_of``,
        in graph order (deterministic)."""
        ids = {e.src for e in self.edges
               if e.kind == "attribute_of" and e.dst == entity_id}
        return tuple(n for n in self.nodes if n.node_id in ids)

    def states_of(self, attribute_id: str) -> tuple[GraphNode, ...]:
        """State nodes linked to ``attribute_id`` via ``state_of``."""
        ids = {e.src for e in self.edges
               if e.kind == "state_of" and e.dst == attribute_id}
        return tuple(n for n in self.nodes if n.node_id in ids)

"""Semantic resolution — deterministic binding of business-vocabulary graphs
onto an org's semantic model (D-376).

Cross-cutting package (peer of ``intelligence``/``shared``): it consumes S1
only through ``SemanticOrgModel`` behind the ``KnowledgeSource`` protocol and
imports nothing from any substrate package. Substrates import THIS.

The joint machinery here is a VERIFIER/GATE, never a decider: it vets a named
subject against the structural evidence the intent itself carries (which
fields, which values) and reports grades — it never silently substitutes an
entity for the one the caller named.
"""
from primeqa.resolution.graph import BusinessGraph, GraphEdge, GraphNode
from primeqa.resolution.knowledge import KnowledgeSource, S1KnowledgeSource
from primeqa.resolution.resolved import (
    AMBIGUOUS, BOUND_UNIQUE, BOUND_WEAK, UNRESOLVED, Binding,
    CandidateEvidence, ResolvedGraph)
from primeqa.resolution.solve import dominant_entity, resolve
from primeqa.resolution.symbols import (
    FieldSymbol, ObjectSymbol, SymbolTable, hydrate_symbol_table)

__all__ = [
    "AMBIGUOUS", "BOUND_UNIQUE", "BOUND_WEAK", "UNRESOLVED",
    "Binding", "BusinessGraph", "CandidateEvidence", "FieldSymbol",
    "GraphEdge", "GraphNode", "KnowledgeSource", "ObjectSymbol",
    "ResolvedGraph", "S1KnowledgeSource", "SymbolTable",
    "dominant_entity", "hydrate_symbol_table", "resolve",
]

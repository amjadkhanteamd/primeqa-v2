"""KnowledgeSource — the seam between resolution and whatever system of
record supplies the symbols.

Exactly one implementation exists today (``S1KnowledgeSource`` over the S1
``SemanticOrgModel``). The protocol is the multi-source seam (D-376): future
sources contribute their own symbol projections without the solver changing.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from primeqa.resolution.symbols import SymbolTable, hydrate_symbol_table


@runtime_checkable
class KnowledgeSource(Protocol):
    def symbol_table(self, at_seq: int) -> SymbolTable:
        """A version-pinned symbol table. Implementations may raise; callers
        that must never fail (shadow observation) catch and degrade."""
        ...

    @property
    def connected_org_id(self) -> Optional[UUID]:
        ...


class S1KnowledgeSource:
    """The sole production source: wraps a tenant+org-scoped
    ``SemanticOrgModel`` (consumed only through its public read API)."""

    def __init__(self, model):
        self._model = model

    def symbol_table(self, at_seq: int) -> SymbolTable:
        return hydrate_symbol_table(self._model, at_seq)

    @property
    def connected_org_id(self) -> Optional[UUID]:
        return getattr(self._model, "connected_org_id", None)

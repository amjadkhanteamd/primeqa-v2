"""Bounded evidence assembly (D-163.2, S7 slice 2).

`assemble_evidence` turns the raw `EvidenceItem`s a recipe produced into a
**bounded** `Evidence`: an item-count cap AND a char/token budget on the serialized
evidence (the D-095.4 "explicit and bounded" requirement + the substrate list-bound
convention), with sequential stable citation ids (`E1..En`) assigned here — so the
assembler is the sole authority on the canonical citation, and `Answer.citations`
are always its ids. Pure + deterministic: preserves recipe order, always admits at
least one item (a single large item is never dropped to empty), then stops at the
cap or budget.
"""
from __future__ import annotations

import json

from primeqa.conversation.model import Evidence, EvidenceItem, IntentKind

# The evidence bound. ~40 rows / ~8000 chars (≈2000 tokens) keeps the phrasing
# prompt's cost flat regardless of how much a tenant has recorded.
_MAX_ITEMS = 40
_CHAR_BUDGET = 8000


def _size(item: EvidenceItem) -> int:
    return len(json.dumps(item.data, sort_keys=True, default=str))


def assemble_evidence(intent: IntentKind, items, *,
                      max_items: int = _MAX_ITEMS,
                      char_budget: int = _CHAR_BUDGET) -> Evidence:
    """Bound `items` and assign sequential `E1..En` citation ids → `Evidence`.

    Deterministic: walks `items` in order; stops at `max_items`, or when adding an
    item would exceed `char_budget` (but always admits the first item). Each kept
    item is re-minted with its canonical `E{n}` citation id.
    """
    bounded: list[EvidenceItem] = []
    used = 0
    for raw in items:
        if len(bounded) >= max_items:
            break
        size = _size(raw)
        if bounded and used + size > char_budget:
            break
        bounded.append(EvidenceItem(
            citation_id=f"E{len(bounded) + 1}",
            source=raw.source, kind=raw.kind, data=raw.data))
        used += size
    return Evidence(intent=intent, items=tuple(bounded))

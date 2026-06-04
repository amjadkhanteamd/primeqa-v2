"""S7 bounded assembler — pure-unit (D-163.2). No DB, no LLM.

Pins the evidence bound: sequential E1..En citation ids, the item-cap, the
char-budget early-stop with a ≥1-item floor, and order/source/kind/data preservation.
"""
from __future__ import annotations

import pytest

from primeqa.conversation import EvidenceItem, assemble_evidence

pytestmark = pytest.mark.unit


def _items(n, *, data=None):
    return [EvidenceItem(citation_id=f"nat{i}", source="S6", kind="x",
                         data=(data or {"i": i})) for i in range(n)]


def test_assigns_sequential_citation_ids():
    ev = assemble_evidence("failure_cause", _items(3))
    assert [it.citation_id for it in ev.items] == ["E1", "E2", "E3"]
    assert ev.intent == "failure_cause"


def test_item_cap_truncates():
    ev = assemble_evidence("failure_cause", _items(10), max_items=4)
    assert [it.citation_id for it in ev.items] == ["E1", "E2", "E3", "E4"]


def test_char_budget_stops_but_admits_at_least_one():
    big = {"blob": "x" * 1000}
    ev = assemble_evidence("failure_cause", _items(5, data=big), char_budget=10)
    # the first item is always admitted even though it alone blows the budget.
    assert len(ev.items) == 1 and ev.items[0].citation_id == "E1"


def test_empty_items_yields_empty_evidence():
    ev = assemble_evidence("impact", [])
    assert ev.is_empty is True and ev.items == ()


def test_preserves_source_kind_data():
    raw = [EvidenceItem(citation_id="natA", source="S8", kind="grounding_validity",
                        data={"overall": "drifted"})]
    ev = assemble_evidence("grounding_drift", raw)
    assert ev.items[0].source == "S8" and ev.items[0].kind == "grounding_validity"
    assert ev.items[0].data == {"overall": "drifted"}

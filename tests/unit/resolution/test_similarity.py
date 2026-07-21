"""Pure lexical engine — determinism and the SF-suffix semantics."""
from __future__ import annotations

from primeqa.resolution import similarity as sim


def test_tokenize_strips_suffixes_and_splits():
    assert sim.tokenize("PLS_FB_Order__c") == ("pls", "fb", "order")
    assert sim.tokenize("PLS_FB_Order__c.PLS_FB_Priority__c") == (
        "pls", "fb", "order", "pls", "fb", "priority")
    assert sim.tokenize("WorkOrder") == ("work", "order")
    assert sim.tokenize(None) == ()


def test_similarity_is_deterministic_and_bounded():
    a = sim.similarity("Order__c", "PLS_FB_Order__c", "PLS FB Order")
    assert a == sim.similarity("Order__c", "PLS_FB_Order__c", "PLS FB Order")
    assert 0.0 < a <= 1.0
    assert sim.similarity("Order__c", "Order", "Order") > a  # closer name


def test_context_overlap_counts_candidate_tokens_in_context():
    ctx = frozenset(sim.tokenize(
        "When a PLS FB Order is submitted the fulfilment task escalates"))
    assert sim.context_overlap("PLS_FB_Order__c", "PLS FB Order", ctx) == 3
    assert sim.context_overlap("Order", "Order", ctx) == 1
    assert sim.context_overlap("Case", "Case", frozenset()) == 0


def test_norm_label_is_case_space_suffix_insensitive():
    assert sim.norm_label("PLS FB Order") == "pls fb order"
    assert sim.norm_label("PLS_FB_Order__c") == "pls fb order"
    assert sim.norm_label(None) == ""

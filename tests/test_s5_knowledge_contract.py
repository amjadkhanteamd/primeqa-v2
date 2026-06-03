"""Substrate-5 Knowledge — contract / drift-guard (D-134 ratification).

Offline, no-DB. Pins the S5 boundary the SPEC ratifies, so a future change that
silently breaks the provider-port contract, the assembler invariants, the
domain-pack selection contract, or the unified public API trips here.

This is the ratification artifact (cf. S2's D-121 taxonomy drift-guard): it does
not re-test every behaviour (the channels have their own suites — see
test_knowledge_architecture.py / test_domain_packs.py); it asserts the *contract*
the SPEC names as the substrate boundary.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass

import pytest

import primeqa.intelligence.knowledge as s5
from primeqa.intelligence.knowledge import (
    DomainPack,
    DomainPackLibrary,
    DomainPackProvider,
    DomainPackSelector,
    KnowledgeAssembler,
    KnowledgeProvider,
    QueryContext,
    Rule,
    SystemPromptRulesProvider,
    LearnedRulesProvider,
)

pytestmark = pytest.mark.unit


def _rule(**kw):
    base = dict(id="R", object_name=None, field_name=None, category="operation",
                rule_text="do the thing", source="system", confidence=1.0, scope="global")
    base.update(kw)
    return Rule(**base)


class _FakeProvider:
    def __init__(self, rules):
        self._rules = list(rules)

    def get_rules(self, ctx):
        return list(self._rules)


class _BrokenProvider:
    def get_rules(self, ctx):
        raise RuntimeError("provider boom")


# ---------------------------------------------------------------------------
# The unified public API surface (the substrate boundary)
# ---------------------------------------------------------------------------

def test_public_api_exports_the_ratified_surface():
    expected = {
        # provider port (proscriptive rules)
        "Rule", "QueryContext", "KnowledgeProvider", "KnowledgeAssembler",
        "SystemPromptRulesProvider", "LearnedRulesProvider",
        # Domain Packs channel (prescriptive patterns)
        "DomainPackProvider", "DomainPack", "DomainPackLibrary", "DomainPackSelector",
    }
    assert expected <= set(s5.__all__)
    for name in expected:
        assert hasattr(s5, name), f"{name} missing from the S5 package surface"


# ---------------------------------------------------------------------------
# The provider-port data contract (Rule / QueryContext)
# ---------------------------------------------------------------------------

def test_rule_shape_and_defaults():
    assert is_dataclass(Rule) and Rule.__dataclass_params__.frozen
    names = {f.name for f in fields(Rule)}
    assert names == {"id", "object_name", "field_name", "category",
                     "rule_text", "source", "confidence", "scope"}
    r = _rule()
    assert (r.source, r.confidence, r.scope) == ("system", 1.0, "global")


def test_query_context_shape():
    assert is_dataclass(QueryContext) and QueryContext.__dataclass_params__.frozen
    names = {f.name for f in fields(QueryContext)}
    assert names == {"tenant_id", "environment_id", "objects", "fields"}
    assert QueryContext().objects == () and QueryContext().fields == ()


def test_knowledge_provider_is_a_protocol_naming_get_rules():
    # the port is a structural Protocol declaring get_rules; a duck-typed object
    # with get_rules flows through the assembler — no inheritance required.
    assert getattr(KnowledgeProvider, "_is_protocol", False)
    assert "get_rules" in dir(KnowledgeProvider)
    out = KnowledgeAssembler([_FakeProvider([_rule(rule_text="duck")])]).assemble(QueryContext())
    assert "duck" in out


# ---------------------------------------------------------------------------
# The assembler invariants (dedup, precedence, determinism, cap, no-crash)
# ---------------------------------------------------------------------------

def test_assembler_dedups_by_id():
    out = KnowledgeAssembler([_FakeProvider([
        _rule(id="X", rule_text="first"), _rule(id="X", rule_text="second")])]
    ).assemble(QueryContext())
    assert out.count("- ") == 1


def test_assembler_precedence_learned_beats_system():
    sys_p = _FakeProvider([_rule(id="X", source="system", rule_text="SYSTEM_TEXT")])
    learned_p = _FakeProvider([_rule(id="X", source="learned", rule_text="LEARNED_TEXT")])
    out = KnowledgeAssembler([sys_p, learned_p]).assemble(QueryContext())
    assert "LEARNED_TEXT" in out and "SYSTEM_TEXT" not in out
    # order-independent (precedence is by source rank, not provider order)
    out2 = KnowledgeAssembler([learned_p, sys_p]).assemble(QueryContext())
    assert "LEARNED_TEXT" in out2 and "SYSTEM_TEXT" not in out2


def test_assembler_render_is_deterministic_byte_identical():
    # the cache-stability invariant: same (providers, ctx) -> identical bytes.
    rules = [_rule(id="A", category="operation"), _rule(id="B", category="field_behaviour"),
             _rule(id="C", category="assertion")]
    a = KnowledgeAssembler([_FakeProvider(rules)])
    assert a.assemble(QueryContext()) == a.assemble(QueryContext())


def test_assembler_token_cap_drops_lowest_confidence_first():
    high = _rule(id="HIGH", confidence=0.99, rule_text="keep me " + "x" * 40)
    low = _rule(id="LOW", confidence=0.10, rule_text="drop me " + "y" * 40)
    # a cap that admits roughly one rule's worth of text.
    out = KnowledgeAssembler([_FakeProvider([high, low])], token_cap=40).assemble(QueryContext())
    assert "keep me" in out and "drop me" not in out


def test_assembler_tolerates_a_broken_provider():
    # a provider that raises is logged + skipped — never crashes prompt build.
    out = KnowledgeAssembler([_BrokenProvider(), _FakeProvider([_rule(id="OK", rule_text="survives")])]
    ).assemble(QueryContext())
    assert "survives" in out


# ---------------------------------------------------------------------------
# The Domain Packs channel contract (selection + attribution)
# ---------------------------------------------------------------------------

_PACK = """---
id: widget_flows
title: Widget Flows
keywords: [widget, gadget]
objects: [Widget__c]
token_budget: 500
version: v1
---
# Widget Flows

How widgets escalate and route. Use Widget__c.Status for the state machine.
"""


def _packs_dir(tmp_path):
    (tmp_path / "widget_flows.md").write_text(_PACK, encoding="utf-8")
    return str(tmp_path)


def test_domain_pack_selection_matches_on_keyword(tmp_path):
    sel = DomainPackSelector(DomainPackLibrary(_packs_dir(tmp_path)))
    matches = sel.select(requirement_text="the widget must escalate", max_tokens=4000)
    assert [m.pack.id for m in matches] == ["widget_flows"]
    # a requirement with no matching keyword selects nothing.
    assert sel.select(requirement_text="unrelated invoice flow", max_tokens=4000) == []


def test_domain_pack_provider_attribution_shape(tmp_path):
    packs, attribution = DomainPackProvider(packs_dir=_packs_dir(tmp_path)).get_packs(
        requirement_text="widget gadget", max_tokens=4000)
    assert [p.id for p in packs] == ["widget_flows"]
    assert attribution == [{"id": "widget_flows", "version": "v1"}]
    assert isinstance(packs[0], DomainPack)


def test_domain_pack_token_budget_caps_selection(tmp_path):
    # a max_tokens below the pack's measured cost selects nothing (budget honored).
    sel = DomainPackSelector(DomainPackLibrary(_packs_dir(tmp_path)))
    assert sel.select(requirement_text="widget", max_tokens=1) == []

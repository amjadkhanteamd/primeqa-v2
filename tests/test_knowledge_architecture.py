"""Unit tests for the knowledge injection architecture.

Offline + no-DB: these test the provider protocol, assembler merging /
precedence / dedup / token cap / rendering. Integration with the actual
LLM gateway + generator is covered separately by
test_llm_architecture.py (unchanged) + a smoke check below that confirms
the existing feedback_rules callers still work.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primeqa.intelligence.knowledge import (
    Rule, QueryContext, KnowledgeAssembler,
    SystemPromptRulesProvider, LearnedRulesProvider,
)


# ---- Test helpers ---------------------------------------------------------

def _rule(**kw):
    defaults = dict(
        id="R_DEFAULT", object_name=None, field_name=None,
        category="operation", rule_text="test rule",
        source="system", confidence=1.0, scope="global",
    )
    defaults.update(kw)
    return Rule(**defaults)


class _FakeProvider:
    def __init__(self, rules):
        self._rules = rules
    def get_rules(self, ctx):
        return list(self._rules)


def _run(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        return False


# ---- Assembler: basic render ----------------------------------------------

def test_empty_assembler_returns_empty_string():
    a = KnowledgeAssembler([])
    assert a.assemble(QueryContext()) == ""

def test_no_rules_returns_empty_string():
    a = KnowledgeAssembler([_FakeProvider([])])
    assert a.assemble(QueryContext()) == ""

def test_single_provider_single_rule_renders():
    a = KnowledgeAssembler([_FakeProvider([_rule(id="X", rule_text="no-id-in-create")])])
    out = a.assemble(QueryContext())
    assert "Salesforce rules" in out
    assert "Operations" in out  # default category is "operation"
    assert "no-id-in-create" in out

def test_multi_category_renders_in_order():
    # field_behaviour first, then operation, then assertion
    rules = [
        _rule(id="A", category="assertion", rule_text="assert-thing"),
        _rule(id="O", category="operation", rule_text="op-thing"),
        _rule(id="F", category="field_behaviour", rule_text="field-thing"),
    ]
    out = KnowledgeAssembler([_FakeProvider(rules)]).assemble(QueryContext())
    f_pos = out.index("field-thing")
    o_pos = out.index("op-thing")
    a_pos = out.index("assert-thing")
    assert f_pos < o_pos < a_pos, f"order wrong: {out}"


# ---- Assembler: dedup + precedence ---------------------------------------

def test_dedup_same_id_keeps_one():
    rules = [_rule(id="DUP"), _rule(id="DUP"), _rule(id="DUP")]
    out = KnowledgeAssembler([_FakeProvider(rules)]).assemble(QueryContext())
    # Count occurrences of "test rule" — should appear exactly once
    assert out.count("test rule") == 1

def test_precedence_learned_beats_system():
    """Same rule id: source='learned' wins over source='system'."""
    rules_from_system = [_rule(id="R1", rule_text="old system text", source="system")]
    rules_from_learned = [_rule(
        id="R1", rule_text="fresher learned text", source="learned",
        category="operation", confidence=0.9,
    )]
    # Register system first, learned second — order shouldn't matter for precedence
    a = KnowledgeAssembler([
        _FakeProvider(rules_from_system),
        _FakeProvider(rules_from_learned),
    ])
    out = a.assemble(QueryContext())
    assert "fresher learned text" in out
    assert "old system text" not in out

def test_precedence_independent_of_provider_order():
    """Same test as above but with providers registered in reverse."""
    rules_from_learned = [_rule(id="R1", rule_text="fresher", source="learned")]
    rules_from_system = [_rule(id="R1", rule_text="stale",    source="system")]
    a = KnowledgeAssembler([
        _FakeProvider(rules_from_learned),
        _FakeProvider(rules_from_system),
    ])
    out = a.assemble(QueryContext())
    assert "fresher" in out
    assert "stale" not in out


# ---- Assembler: token cap -------------------------------------------------

def test_token_cap_drops_lowest_confidence_first():
    # Build 100 rules, each ~30 chars, confidence descending
    rules = []
    for i in range(100):
        rules.append(_rule(
            id=f"R{i:03d}",
            rule_text=f"rule {i:03d} with some moderate length text xxxxxxxxxxxxxxxxxxxxx",
            confidence=1.0 - (i * 0.01),  # 1.00 down to 0.01
        ))
    a = KnowledgeAssembler([_FakeProvider(rules)], token_cap=200)
    out = a.assemble(QueryContext())
    # Highest-confidence rules should survive
    assert "rule 000" in out
    # Lowest-confidence should not
    assert "rule 099" not in out

def test_token_cap_respects_budget():
    rules = [_rule(id=f"R{i}", rule_text="x" * 400) for i in range(10)]
    a = KnowledgeAssembler([_FakeProvider(rules)], token_cap=100)
    out = a.assemble(QueryContext())
    # Rough: 100 tokens * 3.5 chars/token = 350 chars. Header adds some.
    # Assert we didn't blow past 2× the budget.
    assert len(out) < 100 * 3.5 * 2, f"output {len(out)} chars exceeds 2x cap"

def test_determinism_same_inputs_same_output():
    rules = [
        _rule(id="A", rule_text="aaa", confidence=0.8),
        _rule(id="B", rule_text="bbb", confidence=0.8),
        _rule(id="C", rule_text="ccc", confidence=0.8),
    ]
    a = KnowledgeAssembler([_FakeProvider(rules)])
    out1 = a.assemble(QueryContext())
    out2 = a.assemble(QueryContext())
    assert out1 == out2, "assembler not deterministic"


# ---- SystemPromptRulesProvider --------------------------------------------

def test_system_rules_loads_from_default_path():
    """The shipped JSON loads + parses."""
    p = SystemPromptRulesProvider()
    rules = p.get_rules(QueryContext())
    assert len(rules) >= 10, f"expected 10+ rules, got {len(rules)}"
    ids = {r.id for r in rules}
    # Sanity: core rules are present
    assert "NO_ID_IN_CREATE" in ids
    assert "FORMULA_FIELDS_READ_ONLY" in ids

def test_system_rules_filters_by_object():
    p = SystemPromptRulesProvider()
    # Opportunity-only context: Opportunity-tagged + global rules should come back
    opp_only = p.get_rules(QueryContext(objects=("Opportunity",)))
    ids = {r.id for r in opp_only}
    # Case-specific rules should be filtered out
    assert "CASE_NAME_NOT_WRITABLE" not in ids
    assert "LEAD_NAME_NOT_WRITABLE" not in ids
    # Global rules should remain
    assert "NO_ID_IN_CREATE" in ids
    # Opportunity rules should be there
    assert "OPPORTUNITY_NAME_REQUIRED" in ids

def test_system_rules_missing_file_returns_empty():
    p = SystemPromptRulesProvider(rules_path="/tmp/does/not/exist.json")
    assert p.get_rules(QueryContext()) == []

def test_system_rules_handles_malformed_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ this is not valid json")
        path = f.name
    try:
        p = SystemPromptRulesProvider(rules_path=path)
        assert p.get_rules(QueryContext()) == []
    finally:
        os.unlink(path)


# ---- LearnedRulesProvider adapter -----------------------------------------

def test_learned_rules_returns_empty_without_tenant():
    p = LearnedRulesProvider()
    assert p.get_rules(QueryContext()) == []  # no tenant_id

def test_learned_rules_gracefully_handles_db_errors(monkeypatch_ok=True):
    """If build_rules_block raises, we log + return [] instead of propagating."""
    p = LearnedRulesProvider()
    # Force feedback_rules to crash
    import primeqa.intelligence.llm.feedback_rules as fr
    orig = fr.build_rules_block
    try:
        fr.build_rules_block = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        rules = p.get_rules(QueryContext(tenant_id=1))
        assert rules == []
    finally:
        fr.build_rules_block = orig


# ---- End-to-end: all real providers --------------------------------------

def test_system_plus_learned_composable():
    """Assembler can hold both providers without either breaking."""
    a = KnowledgeAssembler([
        SystemPromptRulesProvider(),
        LearnedRulesProvider(),
    ])
    # tenant_id=None -> learned returns []; system returns full set
    out = a.assemble(QueryContext(tenant_id=None))
    assert "Salesforce rules" in out
    assert "NO_ID_IN_CREATE" not in out  # id doesn't appear, but rule_text does
    # A known system-rule phrase
    assert "Never include 'Id'" in out


# ---- Runner ---------------------------------------------------------------

if __name__ == "__main__":
    print("\n=== Knowledge Architecture Tests ===\n")
    tests = [
        ("empty assembler returns empty string",           test_empty_assembler_returns_empty_string),
        ("no-rules returns empty string",                  test_no_rules_returns_empty_string),
        ("single provider + single rule renders",          test_single_provider_single_rule_renders),
        ("multi-category renders in canonical order",      test_multi_category_renders_in_order),
        ("dedup by id keeps one",                          test_dedup_same_id_keeps_one),
        ("precedence: learned beats system",               test_precedence_learned_beats_system),
        ("precedence independent of provider order",       test_precedence_independent_of_provider_order),
        ("token cap drops lowest confidence first",        test_token_cap_drops_lowest_confidence_first),
        ("token cap respects budget",                      test_token_cap_respects_budget),
        ("assembler output is deterministic",              test_determinism_same_inputs_same_output),
        ("SystemPromptRulesProvider loads shipped JSON",   test_system_rules_loads_from_default_path),
        ("SystemPromptRulesProvider filters by object",    test_system_rules_filters_by_object),
        ("SystemPromptRulesProvider missing file \u2192 []",   test_system_rules_missing_file_returns_empty),
        ("SystemPromptRulesProvider malformed json \u2192 []", test_system_rules_handles_malformed_json),
        ("LearnedRulesProvider empty without tenant",      test_learned_rules_returns_empty_without_tenant),
        ("LearnedRulesProvider swallows DB errors",        test_learned_rules_gracefully_handles_db_errors),
        ("system + learned providers composable",          test_system_plus_learned_composable),
    ]
    passed = sum(1 for n, f in tests if _run(n, f))
    total = len(tests)
    print(f"\n{'='*44}")
    print(f"Results: {passed}/{total} passed")
    if passed != total:
        sys.exit(1)

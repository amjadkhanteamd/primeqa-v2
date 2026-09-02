"""Phase 5 Part 2 — the F8 grammar (LLD §e, D-471). Pure: no DB.

The ceiling as tests: ten ratified forms validate; every prohibited
construction refuses WITH ITS CLASS; an LLM draft carrying a connective
dies before any human sees it as a rule."""
from __future__ import annotations

import pytest

from primeqa.knowledge import cust_grammar as G

pytestmark = pytest.mark.unit


def _draft(**over):
    d = {
        "name": "Primary buttons use palette colours",
        "guideline_thread_id": "GT-001",
        "selector": [{"term": "role_is", "value": "button"}],
        "predicate": {"form": "member_of", "fact": "style:background-color",
                      "token_set": {"key": "brand-palette", "version": 1}},
        "population": "every button rendered on the surface",
        "criterion": {"profile": "Brand/Colour", "binds_wcag_sc": "1.4.3"},
    }
    d.update(over)
    return d


def _refusal(**over) -> G.Refusal:
    rule, refusal = G.validate(_draft(**over))
    assert rule is None, "expected a refusal"
    return refusal


# --- the vocabulary census -------------------------------------------------

def test_the_ratified_vocabulary_is_exactly_the_d471_set():
    assert len(G.PREDICATE_TOKENS) == 11          # ten forms; equals/not_equals one pair
    assert set(G.PREDICATE_TOKENS) == {
        "member_of", "not_member_of", "equals", "not_equals",
        "present", "absent", "at_least", "at_most",
        "count_at_least", "count_at_most", "count_equals"}
    assert G.RESERVED_EXTENSION == "idref_resolves_to_role"
    assert G.RESERVED_EXTENSION not in G.PREDICATE_TOKENS
    assert len(G.SELECTOR_TERMS) == 6 and G.MAX_SELECTOR_TERMS == 4
    assert set(G.GATE_TERMS) == {"surface_contains", "surface_lacks"}
    assert set(G.REFUSAL_CLASSES) == {
        "needs_prohibited_operator", "needs_capability_not_captured",
        "needs_interaction", "not_observable",
        "belongs_to_public_catalogue", "ambiguous_guideline"}


def test_a_valid_rule_validates_renders_one_sentence_and_hashes_stably():
    rule, refusal = G.validate(_draft())
    assert refusal is None
    assert rule.token_set_pins == [{"token_set": "brand-palette",
                                    "version": 1}]
    sentence = G.render_sentence(rule)
    assert sentence.count(".") == 1 and sentence.endswith(").")
    assert "must satisfy" in sentence
    again, _ = G.validate(_draft())
    assert rule.content_hash() == again.content_hash()


# --- the connective ban: an LLM draft dies at the validator -----------------

def test_llm_draft_with_AND_is_refused_with_the_split_offered():
    r = _refusal(predicate={"and": [
        {"form": "present", "fact": "attr:alt"},
        {"form": "equals", "fact": "role", "literal": "img"}]})
    assert r.refusal_class == "needs_prohibited_operator"
    assert "connective" in r.reason
    assert r.nearest_expressible and "split into 2 rules" in \
        r.nearest_expressible[0]


def test_or_not_nested_all_die_wherever_they_hide():
    for bad in ({"or": []}, {"not": {"form": "present", "fact": "attr:alt"}},
                {"form": "equals", "fact": "role", "literal": "img",
                 "when": {"x": 1}}):
        r = _refusal(predicate=bad)
        assert r.refusal_class == "needs_prohibited_operator"
    # ...including inside the selector or gate
    r = _refusal(applicability={"gate": "surface_lacks",
                                "term": {"term": "role_is", "value": "nav",
                                         "unless": True}})
    assert r.refusal_class == "needs_prohibited_operator"


def test_the_reserved_extension_is_refused_by_name():
    r = _refusal(predicate={"form": "idref_resolves_to_role",
                            "fact": "attr:aria-describedby"})
    assert r.refusal_class == "needs_prohibited_operator"
    assert "RESERVED first extension" in r.reason and "D-471" in r.reason


# --- the selector ceiling ----------------------------------------------------

def test_css_shaped_operands_are_refused():
    for value in ("button.primary", "div > span", ":hover", "a[href]",
                  "#main", "li:nth-child(2n)"):
        r = _refusal(selector=[{"term": "role_is", "value": value}])
        assert r.refusal_class == "needs_prohibited_operator"
        assert "CSS" in r.reason


def test_selector_caps_at_four_flat_terms():
    terms = [{"term": "role_is", "value": "button"},
             {"term": "within", "value": "main"},
             {"term": "has_attribute", "value": "aria-label"},
             {"term": "component_is", "value": "c-card"},
             {"term": "heading_level_is", "value": 2}]
    r = _refusal(selector=terms)
    assert "at most 4" in r.reason


def test_unknown_selector_term_and_bad_heading_level_refuse():
    assert _refusal(selector=[{"term": "closest", "value": "form"}]
                    ).refusal_class == "needs_prohibited_operator"
    assert "1-6" in _refusal(
        selector=[{"term": "heading_level_is", "value": 9}]).reason


# --- capability + interaction routing ----------------------------------------

def test_uncaptured_property_refuses_with_the_capability_class():
    r = _refusal(predicate={"form": "equals", "fact": "style:cursor",
                            "literal": "pointer"})
    assert r.refusal_class == "needs_capability_not_captured"


def test_focus_state_facts_route_to_needs_interaction():
    r = _refusal(predicate={"form": "present",
                            "fact": "attr:data-focus-ring"})
    assert r.refusal_class == "needs_interaction"


def test_uncaptured_attribute_refuses_with_the_capability_class():
    r = _refusal(selector=[{"term": "has_attribute", "value": "data-qa"}])
    assert r.refusal_class == "needs_capability_not_captured"


# --- form-specific rails ------------------------------------------------------

def test_geometry_forms_are_geometry_only_and_vice_versa():
    r = _refusal(predicate={"form": "at_least",
                            "fact": "style:font-size", "px": 14})
    assert "geometry only" in r.reason
    r = _refusal(predicate={"form": "equals", "fact": "geom:width",
                            "literal": 24})
    assert "at_least/at_most" in r.reason


def test_membership_requires_a_versioned_token_set():
    r = _refusal(predicate={"form": "member_of",
                            "fact": "style:background-color",
                            "token_set": {"key": "palette"}})
    assert "versioned token set" in r.reason


def test_regex_shaped_literals_are_refused_as_a_second_engine():
    r = _refusal(predicate={"form": "equals", "fact": "attr:href",
                            "literal": "^https://.*$"})
    assert "NO SECOND ENGINE" in r.reason


def test_population_declaration_is_mandatory():
    assert "population" in _refusal(population="").reason


def test_at_most_one_gate_and_gate_shape():
    r = _refusal(applicability=[
        {"gate": "surface_contains",
         "term": {"term": "role_is", "value": "form"}},
        {"gate": "surface_lacks",
         "term": {"term": "role_is", "value": "nav"}}])
    assert "ONE applicability gate" in r.reason
    rule, refusal = G.validate(_draft(applicability={
        "gate": "surface_lacks",
        "term": {"term": "component_is", "value": "c-legacy-banner"}}))
    assert refusal is None and rule.applicability["gate"] == "surface_lacks"

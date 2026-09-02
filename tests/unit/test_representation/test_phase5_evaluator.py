"""Phase 5 Part 2 — the census evaluator (LLD §h + the §e.6 hard
invariant, D-471). Pure: no DB, no browser.

THE MATRIX: every one of the eleven ratified predicate tokens is
exercised to a witnessed PASS, a witnessed FAIL, and its NOT_DETERMINED
case — plus the vacuous-pass guards this programme was burned by:
no_match_set, the node-cap demonstration (a would-be suppression turns
into census_incomplete), census_unattested, traversal_mode_mismatch."""
from __future__ import annotations

import pytest

from primeqa.interpretation import cust_evaluation as E

pytestmark = pytest.mark.unit

PALETTE = {("brand-palette", 1): ["rgb(0, 82, 204)", "#FFFFFF"]}


def node(**over):
    n = {"role": "button", "name": "Save", "heading": 0, "tag": "",
         "anc": ["main"], "attrs": {"type": "submit"},
         "style": {"background-color": "rgb(0, 82, 204)",
                   "font-size": "13.9993px",
                   "font-family": '"Salesforce Sans", Arial, sans-serif'},
         "box": [10.0, 20.0, 48.0, 32.0]}
    n.update(over)
    return n


def obs(nodes, status="OK", **census_over):
    census = {"schema_version": 1, "traversal_mode": "light_only",
              "node_cap": 1500, "cap_hit": False, "capture_errors": 0,
              "n": len(nodes), "nodes": nodes}
    census.update(census_over)
    return {"status": status, "census": census}


def content(**over):
    c = {"selector": [{"term": "role_is", "value": "button"}],
         "predicate": {"form": "present", "fact": "attr:type"},
         "applicability": None,
         "population": "every button on the surface",
         "criterion": {"profile": "P", "binds_wcag_sc": None},
         "census_schema_version": 1,
         "traversal_mode_assumption": "any",
         "token_set_pins": []}
    c.update(over)
    return c


def run(pred_or_content, nodes, **kw):
    c = (pred_or_content if "selector" in pred_or_content
         else content(predicate=pred_or_content))
    observation = kw.pop("observation", None) or obs(nodes, **kw)
    return E.evaluate_rule(c, observation, token_sets=PALETTE)


# ---------------------------------------------------------------------------
# THE MATRIX — eleven tokens x (PASS, FAIL, NOT_DETERMINED)
# ---------------------------------------------------------------------------

def test_member_of_pass_fail_and_fact_not_captured():
    p = {"form": "member_of", "fact": "style:background-color",
         "token_set": {"key": "brand-palette", "version": 1}}
    assert run(p, [node()])[0] == E.PASS
    v, b = run(p, [node(style={"background-color": "rgb(255, 0, 0)"})])
    assert v == E.FAIL and b["nodes"]
    v, b = run(p, [node(style={})])
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.FACT_NOT_CAPTURED)


def test_not_member_of_pass_fail_and_unresolved_token_set():
    p = {"form": "not_member_of", "fact": "style:background-color",
         "token_set": {"key": "brand-palette", "version": 1}}
    assert run(p, [node(style={"background-color": "rgb(1, 2, 3)"})])[0] \
        == E.PASS
    assert run(p, [node()])[0] == E.FAIL
    p2 = dict(p, token_set={"key": "ghost", "version": 9})
    v, b = run(p2, [node()])
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.FACT_NOT_CAPTURED)


def test_equals_pass_fail_and_fact_not_captured():
    p = {"form": "equals", "fact": "attr:type", "literal": "submit"}
    assert run(p, [node()])[0] == E.PASS
    assert run(p, [node(attrs={"type": "button"})])[0] == E.FAIL
    v, b = run(p, [node(attrs=None)])
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.FACT_NOT_CAPTURED)


def test_not_equals_pass_fail_and_fact_not_captured():
    p = {"form": "not_equals", "fact": "attr:type", "literal": "reset"}
    assert run(p, [node()])[0] == E.PASS
    assert run(p, [node(attrs={"type": "reset"})])[0] == E.FAIL
    v, b = run({"form": "not_equals", "fact": "style:color",
                "literal": "rgb(0, 0, 0)"}, [node(style={})])
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.FACT_NOT_CAPTURED)


def test_present_pass_fail_and_missing_slot():
    p = {"form": "present", "fact": "attr:aria-label"}
    assert run(p, [node(attrs={"aria-label": "Save"})])[0] == E.PASS
    assert run(p, [node()])[0] == E.FAIL          # bag recorded, attr absent
    v, b = run(p, [node(attrs=None)])
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.FACT_NOT_CAPTURED)


def test_absent_is_witnessed_by_the_recorded_bag_never_by_silence():
    p = {"form": "absent", "fact": "attr:aria-hidden"}
    assert run(p, [node()])[0] == E.PASS          # positive record of absence
    assert run(p, [node(attrs={"aria-hidden": "true"})])[0] == E.FAIL
    v, b = run(p, [node(attrs=None)])              # slot never captured
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.FACT_NOT_CAPTURED)


def test_at_least_pass_fail_and_missing_box():
    p = {"form": "at_least", "fact": "geom:width", "px": 44.0}
    assert run(p, [node(box=[0, 0, 44.2, 44.0])])[0] == E.PASS
    assert run(p, [node(box=[0, 0, 20.0, 44.0])])[0] == E.FAIL
    v, b = run(p, [node(box=None)])
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.FACT_NOT_CAPTURED)


def test_at_most_pass_fail_and_epsilon():
    p = {"form": "at_most", "fact": "geom:height", "px": 32.0}
    assert run(p, [node(box=[0, 0, 48, 32.4])])[0] == E.PASS   # within epsilon
    assert run(p, [node(box=[0, 0, 48, 33.1])])[0] == E.FAIL
    v, b = run(p, [node(box=None)])
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.FACT_NOT_CAPTURED)


def test_count_at_least_pass_fail_and_incomplete_walk():
    p = {"form": "count_at_least", "n": 2}
    assert run(p, [node(), node(name="B")])[0] == E.PASS
    assert run(p, [node()])[0] == E.FAIL
    v, b = run(p, [node()], cap_hit=True)          # might have found more
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.CENSUS_INCOMPLETE)


def test_count_at_most_pass_fail_and_incomplete_walk():
    p = {"form": "count_at_most", "n": 1}
    assert run(p, [node()])[0] == E.PASS
    assert run(p, [node(), node(name="B")])[0] == E.FAIL
    v, b = run(p, [node()], cap_hit=True)          # "at most" needs the walk
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.CENSUS_INCOMPLETE)


def test_count_equals_pass_fail_and_incomplete_walk():
    p = {"form": "count_equals", "n": 1}
    assert run(p, [node()])[0] == E.PASS
    assert run(p, [])[0] == E.FAIL
    v, b = run(p, [node()], capture_errors=3)
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.CENSUS_INCOMPLETE)


# ---------------------------------------------------------------------------
# The vacuous-pass guards
# ---------------------------------------------------------------------------

def test_empty_match_set_is_no_match_set_never_pass():
    v, b = run({"form": "present", "fact": "attr:type"},
               [node(role="link")])
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.NO_MATCH_SET)
    assert "matched nothing" in b["detail"]


def test_the_node_cap_demonstration_a_would_be_suppression_becomes_incomplete():
    """The briefed demonstration: surface_lacks would suppress, but the
    census hit its cap — NOT_DETERMINED(census_incomplete), NEVER
    rule_inapplicable (§e.6 hard invariant)."""
    c = content(applicability={"gate": "surface_lacks",
                               "term": {"term": "component_is",
                                        "value": "c-legacy-banner"}})
    banner = node(role="", tag="c-legacy-banner", attrs={})
    # complete census, banner PRESENT: the lacks-gate MAY suppress
    v, b = E.evaluate_rule(c, obs([node(), banner]), token_sets=PALETTE)
    assert (v, b["reason"]) == (E.NOT_DETERMINED, "rule_inapplicable")
    # complete census, banner ABSENT: the rule applies and decides on merits
    assert E.evaluate_rule(c, obs([node()]), token_sets=PALETTE)[0] == E.PASS
    # capped census: the gate decides NOTHING — in BOTH configurations
    for nodes in ([node(), banner], [node()]):
        v, b = E.evaluate_rule(c, obs(nodes, cap_hit=True),
                               token_sets=PALETTE)
        assert (v, b["reason"]) == (E.NOT_DETERMINED, E.CENSUS_INCOMPLETE)
        assert "decides nothing" in b["detail"]
        assert "node_cap_not_hit" in b["failed_conditions"]


def test_surface_contains_gate_applies_and_suppresses_correctly():
    c = content(applicability={"gate": "surface_contains",
                               "term": {"term": "role_is", "value": "form"}})
    v, b = E.evaluate_rule(c, obs([node()]), token_sets=PALETTE)
    assert (v, b["reason"]) == (E.NOT_DETERMINED, "rule_inapplicable")
    v, _ = E.evaluate_rule(c, obs([node(), node(role="form")]),
                           token_sets=PALETTE)
    assert v == E.PASS


def test_all_satisfying_on_an_unfinished_walk_is_never_pass():
    v, b = run({"form": "present", "fact": "attr:type"}, [node()],
               cap_hit=True)
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.CENSUS_INCOMPLETE)
    assert "attest the scope was walked" in b["detail"]


def test_a_witnessed_violation_still_fails_on_a_partial_walk():
    v, b = run({"form": "present", "fact": "attr:aria-label"}, [node()],
               cap_hit=True)
    assert v == E.FAIL          # found = found; incompleteness never acquits


def test_census_unattested_older_schema_and_missing_census():
    c = content(census_schema_version=2)
    v, b = E.evaluate_rule(c, obs([node()]), token_sets=PALETTE)
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.CENSUS_UNATTESTED)
    v, b = E.evaluate_rule(content(), {"status": "OK", "census": None})
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.CENSUS_UNATTESTED)


def test_traversal_mode_mismatch():
    c = content(traversal_mode_assumption="synthetic_aura")
    v, b = E.evaluate_rule(c, obs([node()]), token_sets=PALETTE)
    assert (v, b["reason"]) == (E.NOT_DETERMINED, E.TRAVERSAL_MODE_MISMATCH)
    assert "shadow-DOM traversal" in b["detail"]


# ---------------------------------------------------------------------------
# Normalisation (schema v1)
# ---------------------------------------------------------------------------

def test_colour_normalisation_equates_rgb_and_hex_forms():
    assert E.normalise_color("rgb(0, 82, 204)") == (0, 82, 204, 1.0)
    assert E.normalise_color("#0052CC") == (0, 82, 204, 1.0)
    assert E.normalise_color("#fff") == (255, 255, 255, 1.0)
    assert E.normalise_color("rgba(0, 82, 204, 0.5)") == (0, 82, 204, 0.5)
    assert E.normalise_color("not-a-colour") is None


def test_browser_px_noise_is_absorbed_by_the_pinned_epsilon():
    p = {"form": "equals", "fact": "style:font-size", "literal": "14px"}
    assert run(p, [node()])[0] == E.PASS           # 13.9993px == 14px @ 0.5
    p = {"form": "equals", "fact": "style:font-size", "literal": "15px"}
    assert run(p, [node()])[0] == E.FAIL


def test_font_family_normalises_quotes_and_case():
    p = {"form": "equals", "fact": "style:font-family",
         "literal": "salesforce sans, ARIAL, sans-serif"}
    assert run(p, [node()])[0] == E.PASS


# ---------------------------------------------------------------------------
# Selector semantics over the census
# ---------------------------------------------------------------------------

def test_within_heading_component_and_attribute_terms():
    c = content(selector=[{"term": "within", "value": "main"},
                          {"term": "has_attribute", "value": "type"}])
    assert E.evaluate_rule(c, obs([node()]), token_sets=PALETTE)[0] == E.PASS
    c = content(selector=[{"term": "heading_level_is", "value": 2}],
                predicate={"form": "count_equals", "n": 1})
    nodes = [node(role="heading", heading=2, attrs={})]
    assert E.evaluate_rule(c, obs(nodes), token_sets=PALETTE)[0] == E.PASS


def test_owned_by_bundle_without_a_resolver_is_fact_not_captured():
    c = content(selector=[{"term": "owned_by_bundle", "value": "loanCard"}])
    v, b = E.evaluate_rule(c, obs([node(tag="c-loan-card")]),
                           token_sets=PALETTE)
    assert v == E.NOT_DETERMINED
    assert b["reason"] in (E.NO_MATCH_SET, E.CENSUS_INCOMPLETE) or \
        b["undecidable_nodes"] > 0
    v, _ = E.evaluate_rule(
        c, obs([node(tag="c-loan-card")]), token_sets=PALETTE,
        resolve_bundle_tag=lambda tag: "loanCard")
    assert v == E.PASS

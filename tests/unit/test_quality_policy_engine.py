"""Step 5 — the policy engine, pure (LLD_STEP_5_QUALITY_POLICY §a/§b; rulings
1, 2, 6): the closed vocabulary refuses unknown words; the ladder — a graded
BLOCK → no_go, an ungraded BLOCK or a REVIEW → cannot_determine (never go or
conditional_go), a CONDITIONAL → conditional_go, else go; each axis line names
the triggered rule and its effect; "Nothing ungraded" when true."""
from __future__ import annotations

import pytest

from primeqa.intelligence import quality_policy as qp




SEED = [
    qp.Rule(1, "functional", "failure_present", "BLOCK"),
    qp.Rule(2, "conformance", "level_a_failed", "BLOCK"),
    qp.Rule(3, "conformance", "level_aa_failed", "CONDITIONAL"),
    qp.Rule(4, "human_reviews", "needs_human_pending", "REVIEW"),
    qp.Rule(5, "waivers", "active_waiver_covers_item", "ALLOW", scope="item"),
    qp.Rule(6, "regressions", "tool_drift_only", "ALLOW"),
    qp.Rule(7, "environments", "environment_drift", "REVIEW"),
    qp.Rule(8, "grading", "ungraded_present", "BLOCK"),
    qp.Rule(9, "environments", "production_target_ungraded_on_metadata", "BLOCK"),
    qp.Rule(10, "functional", "pass_rate_below", "BLOCK", parameter=95),
]
POLICY = qp.Policy(id="p1", name="Plimsol default", version=1, status="active", rules=tuple(SEED))


def _ev(**over):
    base = {
        "functional": {"counted": 4, "passed": 4, "failed": 0, "errored": 0, "pass_rate": 100.0,
                       "observed": "4 checks, 4 passed"},
        "conformance": {"failed_by_level": {"A": 0, "AA": 0, "AAA": 0}, "not_determined": 0, "observed": "clean"},
        "regressions": {"new_fail": 0, "functional_new_fail": 0, "not_comparable": 0, "tool_drift_only": False,
                        "observed": "no comparison"},
        "waivers": {"applied": 0, "observed": "0 active"},
        "human_reviews": {"pending": 0, "observed": "0 pending"},
        "environments": {"drift": 0, "non_current": 0, "production_ungraded_on_metadata": 0, "observed": "env 59"},
        "grading": {"count": 0, "items": [], "observed": "Nothing ungraded."},
        "plan": {"id": "9ea0c522-0000-0000-0000-000000000000"},
    }
    for k, v in over.items():
        base[k] = {**base[k], **v} if isinstance(v, dict) and k in base else v
    return base


def _line(d, axis):
    return next(ln for ln in d["evidence_lines"] if ln["axis"] == axis)


def test_vocabulary_is_closed():
    with pytest.raises(qp.PolicyError):
        qp.Rule(1, "functional", "level_a_failed", "BLOCK")          # condition not on that axis
    with pytest.raises(qp.PolicyError):
        qp.Rule(1, "functional", "failure_present", "VETO")          # unknown effect
    with pytest.raises(qp.PolicyError):
        qp.Rule(1, "vibes", "failure_present", "BLOCK")              # unknown axis
    with pytest.raises(qp.PolicyError):
        qp.Rule(1, "functional", "pass_rate_below", "BLOCK")         # parameter declared, none given
    with pytest.raises(qp.PolicyError):
        qp.Rule(1, "functional", "failure_present", "BLOCK", parameter=3)   # no parameter admitted
    with pytest.raises(qp.PolicyError):
        qp.Rule(1, "functional", "failure_present", "BLOCK", scope="tenant")


def test_clean_evidence_is_go_with_seven_lines_and_nothing_ungraded():
    d = qp.evaluate(POLICY, _ev())
    assert d["recommendation"] == "go" and d["effect"] == "ALLOW"
    assert [ln["axis"] for ln in d["evidence_lines"]] == list(qp.AXES)
    assert all(ln["rule"] == "no rule triggered" for ln in d["evidence_lines"])
    assert _line(d, "grading")["observed"] == "Nothing ungraded."
    assert d["policy"] == {"id": "p1", "name": "Plimsol default", "version": 1, "label": "Plimsol default v1"}
    assert d["plan_id"].startswith("9ea0c522")


def test_a_functional_failure_blocks_to_no_go_naming_rule_1():
    d = qp.evaluate(POLICY, _ev(functional={"failed": 1, "passed": 3, "pass_rate": 75.0}))
    assert d["recommendation"] == "no_go"
    ln = _line(d, "functional")
    assert ln["effect"] == "BLOCK" and {r["position"] for r in ln["rules"]} == {1, 10}
    assert ln["rule"] == "rule 1, rule 10"


def test_level_a_blocks_and_level_aa_conditions():
    a = qp.evaluate(POLICY, _ev(conformance={"failed_by_level": {"A": 1, "AA": 0, "AAA": 0}}))
    assert a["recommendation"] == "no_go" and _line(a, "conformance")["rule"].startswith("rule 2:")
    aa = qp.evaluate(POLICY, _ev(conformance={"failed_by_level": {"A": 0, "AA": 2, "AAA": 0}}))
    assert aa["recommendation"] == "conditional_go" and _line(aa, "conformance")["effect"] == "CONDITIONAL"


def test_needs_human_pending_reviews_to_cannot_determine():
    d = qp.evaluate(POLICY, _ev(human_reviews={"pending": 1}))
    assert d["recommendation"] == "cannot_determine" and d["because"] == "a REVIEW"
    assert _line(d, "human_reviews")["rule"].startswith("rule 4:")


def test_ungraded_blocks_to_cannot_determine_never_go_or_conditional():
    d = qp.evaluate(POLICY, _ev(grading={"count": 2, "items": [{"item": "x"}, {"item": "y"}],
                                          "observed": "2 ungraded"}))
    assert d["recommendation"] == "cannot_determine" and d["because"] == "an ungraded BLOCK"
    assert _line(d, "grading")["effect"] == "BLOCK" and len(d["ungraded"]) == 2
    # with a CONDITIONAL beside it — still cannot_determine, never conditional_go
    d2 = qp.evaluate(POLICY, _ev(grading={"count": 1, "items": [{"item": "x"}]},
                                  conformance={"failed_by_level": {"A": 0, "AA": 1, "AAA": 0}}))
    assert d2["recommendation"] == "cannot_determine"
    # D9: a graded BLOCK beside the unknown → NO GO stands
    d3 = qp.evaluate(POLICY, _ev(grading={"count": 1, "items": [{"item": "x"}]},
                                  functional={"failed": 1, "passed": 3, "pass_rate": 75.0}))
    assert d3["recommendation"] == "no_go"


def test_waiver_is_an_item_scoped_allow_that_does_not_move_the_release():
    d = qp.evaluate(POLICY, _ev(waivers={"applied": 1}))
    ln = _line(d, "waivers")
    assert ln["effect"] == "ALLOW" and ln["rules"][0]["scope"] == "item"
    assert d["recommendation"] == "go"


def test_tool_drift_only_allows_and_environment_drift_reviews():
    assert qp.evaluate(POLICY, _ev(regressions={"tool_drift_only": True}))["recommendation"] == "go"
    d = qp.evaluate(POLICY, _ev(environments={"drift": 1}))
    assert d["recommendation"] == "cannot_determine" and _line(d, "environments")["rule"].startswith("rule 7:")


def test_production_target_ungraded_on_metadata_is_a_graded_block():
    d = qp.evaluate(POLICY, _ev(environments={"production_ungraded_on_metadata": 1}))
    assert d["recommendation"] == "no_go" and _line(d, "environments")["rule"].startswith("rule 9:")


def test_pass_rate_parameter_grades_only_with_counted_runs():
    assert qp.evaluate(POLICY, _ev(functional={"pass_rate": 90.0}))["recommendation"] == "no_go"
    assert qp.evaluate(POLICY, _ev(functional={"counted": 0, "pass_rate": None}))["recommendation"] == "go"


def test_a_draft_policy_refuses_to_grade():
    with pytest.raises(qp.PolicyError):
        qp.evaluate(qp.Policy(id="d", name="x", version=2, status="draft", rules=tuple(SEED)), _ev())

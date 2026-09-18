"""Unit: the AUD-014 pre-condition — a decision requires a NON-EMPTY graded
scope. ``scope_emptiness`` is the PURE verdict over the canonical scope read;
every surface (the engine, the composer, the page, the board) shows its
sentence. Four reasons, each naming what is empty; a conformance-only scope
and a one-check scope are NOT empty — the rule is about zero, not about few."""
from __future__ import annotations

import pytest

from primeqa.intelligence.quality_evidence import (
    EMPTY_ALL_EXCLUDED,
    EMPTY_NO_ACTIVE_TARGET,
    EMPTY_NO_REQUIREMENT,
    EMPTY_NO_TEST_CASE,
    scope_emptiness,
)

pytestmark = pytest.mark.unit


def _scope(**over):
    sc = {"keys": ["SQ-205"], "requirement_count": 1, "functional_ids": ["a", "b"], "conformance_ids": [],
          "targets": [59], "targets_source": "declared", "active_targets": [59],
          "target_rows": [{"environment_id": 59, "name": "SFDC sbx", "is_active": True}],
          "excluded_by_env": {}, "checks": 2}
    sc.update(over)
    return sc


def test_a_real_scope_is_not_empty():
    assert scope_emptiness(_scope()) is None


def test_one_check_is_enough_the_rule_is_about_zero_not_few():
    assert scope_emptiness(_scope(functional_ids=["a"], checks=1)) is None


def test_no_requirement_names_the_absence():
    e = scope_emptiness(_scope(keys=[], requirement_count=0, functional_ids=[], targets=[], active_targets=[],
                               target_rows=[], checks=0))
    assert e["reason"] == EMPTY_NO_REQUIREMENT
    assert e["sentence"] == "no requirement is in scope — nothing to grade"


def test_requirements_without_a_live_test_case_name_the_count():
    e = scope_emptiness(_scope(keys=["SQ-1", "SQ-2"], requirement_count=2, functional_ids=[], conformance_ids=[],
                               targets=[], active_targets=[], target_rows=[], checks=0))
    assert e["reason"] == EMPTY_NO_TEST_CASE and e["requirements"] == 2
    assert e["sentence"] == "2 requirements in scope hold no current test case — nothing to grade"
    one = scope_emptiness(_scope(functional_ids=[], targets=[], active_targets=[], target_rows=[], checks=0))
    assert one["sentence"] == "1 requirement in scope holds no current test case — nothing to grade"


def test_declared_targets_all_inactive_name_the_environments():
    e = scope_emptiness(_scope(targets=[78, 80], active_targets=[], checks=0,
                               target_rows=[{"environment_id": 78, "name": "Prod RO", "is_active": False},
                                            {"environment_id": 80, "name": "Old sbx", "is_active": False}]))
    assert e["reason"] == EMPTY_NO_ACTIVE_TARGET and e["targets"] == [78, 80]
    assert e["sentence"] == ("every declared target environment is inactive (Prod RO, Old sbx) — "
                             "no active environment is in scope")


def test_no_declared_target_and_no_active_evidence_names_both_absences():
    e = scope_emptiness(_scope(targets=[], targets_source="fallback:evidence-active", active_targets=[],
                               target_rows=[], checks=0))
    assert e["reason"] == EMPTY_NO_ACTIVE_TARGET and e["targets"] == []
    assert e["sentence"] == ("no target environment — none is declared, and no active "
                             "environment holds a run for this scope")


def test_a_plan_that_excludes_everything_everywhere_leaves_no_check():
    e = scope_emptiness(_scope(excluded_by_env={59: {"a": "data recipe on read-only", "b": "data recipe on read-only"}},
                               checks=0))
    assert e["reason"] == EMPTY_ALL_EXCLUDED and e["targets"] == [59]
    assert e["sentence"] == "the plan excludes every test case on every target — no check remains to grade"


def test_one_unexcluded_pair_on_one_active_target_is_a_check():
    # excluded on 59, admitted on 60 -> one check remains -> not empty
    assert scope_emptiness(_scope(targets=[59, 60], active_targets=[59, 60],
                                  excluded_by_env={59: {"a": "x", "b": "x"}, 60: {"a": "x"}}, checks=1)) is None


def test_a_conformance_only_scope_is_not_empty_targets_do_not_apply():
    # conformance checks grade on the browser plane: no functional test case,
    # no target — and still a graded scope
    assert scope_emptiness(_scope(functional_ids=[], conformance_ids=["u1"], targets=[], active_targets=[],
                                  target_rows=[], checks=1)) is None


def test_conformance_checks_keep_a_scope_whose_functional_side_is_all_excluded():
    assert scope_emptiness(_scope(conformance_ids=["u1"], excluded_by_env={59: {"a": "x", "b": "x"}}, checks=1)) is None


def test_an_inactive_target_with_conformance_checks_is_still_empty_on_the_functional_side():
    # functional test cases exist and have NO active target: that is an empty
    # functional scope regardless of conformance — the rule names it
    e = scope_emptiness(_scope(conformance_ids=["u1"], targets=[78], active_targets=[], checks=1,
                               target_rows=[{"environment_id": 78, "name": "Prod RO", "is_active": False}]))
    assert e["reason"] == EMPTY_NO_ACTIVE_TARGET

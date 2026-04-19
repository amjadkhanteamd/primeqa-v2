"""Tests for the offline prompt-quality eval harness.

Covers:
  - Fixture loading + suite listing
  - Scorer: structural checks for test_plan_generation
  - Dry-mode runner end-to-end against shipped fixtures
  - CLI exit code (0 on all-pass, 1 otherwise)
  - The "regression fixture" that proves the scorer catches bugs

All tests are dry-mode (no Anthropic calls) so this runs fast + free.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


def test(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        import traceback
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


# ---- Fixture loading ------------------------------------------------------

def test_available_suites_lists_test_plan_generation():
    from primeqa.intelligence.llm.eval import available_suites
    suites = available_suites()
    assert "test_plan_generation" in suites, f"got {suites}"


def test_load_suite_returns_parsed_fixtures():
    from primeqa.intelligence.llm.eval import load_suite
    fixtures = load_suite("test_plan_generation")
    assert len(fixtures) >= 2, f"expected >=2 fixtures, got {len(fixtures)}"
    ids = {f.id for f in fixtures}
    assert "basic_validation" in ids
    assert "unresolved_var_regression" in ids


def test_load_suite_unknown_task_returns_empty():
    from primeqa.intelligence.llm.eval import load_suite
    assert load_suite("not_a_real_task") == []


# ---- Scorer --------------------------------------------------------------

def test_scorer_happy_path():
    """The basic_validation fixture's dry-mode output should fully pass
    the scorer's test_plan_generation rubric."""
    from primeqa.intelligence.llm.eval import load_suite
    from primeqa.intelligence.llm.eval.scorer import score
    fx = next(f for f in load_suite("test_plan_generation")
              if f.id == "basic_validation")
    checks = score("test_plan_generation", fx.expected["output"],
                   fx.expected, fx.rubric)
    fails = [c for c in checks if not c.passed]
    assert not fails, f"unexpected fails: {[c.name for c in fails]}"


def test_scorer_catches_unresolved_state_ref():
    """The regression fixture intentionally dangles $no_such — the
    no_unresolved_state_refs check must FAIL for it."""
    from primeqa.intelligence.llm.eval import load_suite
    from primeqa.intelligence.llm.eval.scorer import score
    fx = next(f for f in load_suite("test_plan_generation")
              if f.id == "unresolved_var_regression")
    checks = score("test_plan_generation", fx.expected["output"],
                   fx.expected, fx.rubric)
    named = {c.name: c for c in checks}
    assert "no_unresolved_state_refs" in named
    assert named["no_unresolved_state_refs"].passed is False, \
        "regression fixture should fail the dangling-$var check"


def test_scorer_enforces_allowed_objects():
    from primeqa.intelligence.llm.eval.scorer import score
    output = {"test_plan": {"test_cases": [{
        "title": "uses Opportunity", "coverage_type": "positive",
        "steps": [{"step_order": 1, "action": "create",
                   "target_object": "Opportunity"}],
        "confidence_score": 0.9,
    }]}}
    expected = {"allowed_objects": ["Account"], "required_coverage_types": ["positive"]}
    checks = score("test_plan_generation", output, expected, {})
    named = {c.name: c for c in checks}
    assert named["objects_in_allowed_list"].passed is False


def test_scorer_enforces_forbidden_objects():
    from primeqa.intelligence.llm.eval.scorer import score
    output = {"test_plan": {"test_cases": [{
        "title": "uses Lead", "coverage_type": "positive",
        "steps": [{"step_order": 1, "action": "create", "target_object": "Lead"}],
        "confidence_score": 0.9,
    }]}}
    expected = {"forbidden_objects": ["Lead"], "required_coverage_types": ["positive"]}
    checks = score("test_plan_generation", output, expected, {})
    named = {c.name: c for c in checks}
    assert named["no_forbidden_objects"].passed is False


def test_scorer_unknown_task_uses_fallback():
    from primeqa.intelligence.llm.eval.scorer import score
    checks = score("made_up_task", {"ok": True}, {}, {})
    # Fallback just checks non-empty
    assert len(checks) == 1
    assert checks[0].name == "non_empty_output"
    assert checks[0].passed is True


# ---- Runner --------------------------------------------------------------

def test_runner_dry_mode_happy_fixture_passes():
    from primeqa.intelligence.llm.eval.runner import run_suite
    report = run_suite("test_plan_generation", mode="dry",
                       include_ids=["basic_validation"])
    assert report.total == 1
    assert report.passed == 1, f"got {report.fixtures[0]}"


def test_runner_dry_mode_regression_fixture_fails():
    """The regression fixture exists precisely to prove that when a
    scorer check SHOULD fail, it does. If this test itself passes (i.e.
    runner reports the fixture as failed), the scorer is working."""
    from primeqa.intelligence.llm.eval.runner import run_suite
    report = run_suite("test_plan_generation", mode="dry",
                       include_ids=["unresolved_var_regression"])
    assert report.total == 1
    assert report.failed == 1
    failed = report.fixtures[0]
    failed_checks = [c for c in failed.checks if not c["passed"]]
    assert any(c["name"] == "no_unresolved_state_refs" for c in failed_checks)


def test_runner_records_spec_info_in_dry_mode():
    """Proof that dry-mode DID build the prompt spec (not just skip it)."""
    from primeqa.intelligence.llm.eval.runner import run_suite
    report = run_suite("test_plan_generation", mode="dry",
                       include_ids=["basic_validation"])
    info = report.fixtures[0].spec_info
    assert info["has_tools"] is True  # test_plan_generation uses tool_use
    assert info["has_cache_blocks"] is True
    assert info["force_tool"] == "submit_test_plan"


def test_runner_live_mode_requires_credentials():
    from primeqa.intelligence.llm.eval.runner import run_suite
    report = run_suite("test_plan_generation", mode="live",
                       include_ids=["basic_validation"])
    # Without tenant_id + api_key, every fixture errors.
    assert report.failed == 1
    assert "requires" in (report.fixtures[0].error or "").lower()


# ---- CLI ----------------------------------------------------------------

def test_cli_exits_zero_when_all_pass():
    from primeqa.intelligence.llm.eval.__main__ import main
    rc = main(["test_plan_generation",
               "--filter", "basic_validation",
               "--json"])
    assert rc == 0


def test_cli_exits_nonzero_when_any_fails():
    from primeqa.intelligence.llm.eval.__main__ import main
    rc = main(["test_plan_generation",
               "--filter", "unresolved_var_regression",
               "--json"])
    assert rc == 1


def test_cli_rejects_unknown_task():
    from primeqa.intelligence.llm.eval.__main__ import main
    rc = main(["not_a_real_task", "--json"])
    assert rc == 2


def main_run():
    tests = [
        ("available_suites_lists_test_plan_generation", test_available_suites_lists_test_plan_generation),
        ("load_suite_returns_parsed_fixtures", test_load_suite_returns_parsed_fixtures),
        ("load_suite_unknown_task_returns_empty", test_load_suite_unknown_task_returns_empty),
        ("scorer_happy_path", test_scorer_happy_path),
        ("scorer_catches_unresolved_state_ref", test_scorer_catches_unresolved_state_ref),
        ("scorer_enforces_allowed_objects", test_scorer_enforces_allowed_objects),
        ("scorer_enforces_forbidden_objects", test_scorer_enforces_forbidden_objects),
        ("scorer_unknown_task_uses_fallback", test_scorer_unknown_task_uses_fallback),
        ("runner_dry_mode_happy_fixture_passes", test_runner_dry_mode_happy_fixture_passes),
        ("runner_dry_mode_regression_fixture_fails", test_runner_dry_mode_regression_fixture_fails),
        ("runner_records_spec_info_in_dry_mode", test_runner_records_spec_info_in_dry_mode),
        ("runner_live_mode_requires_credentials", test_runner_live_mode_requires_credentials),
        ("cli_exits_zero_when_all_pass", test_cli_exits_zero_when_all_pass),
        ("cli_exits_nonzero_when_any_fails", test_cli_exits_nonzero_when_any_fails),
        ("cli_rejects_unknown_task", test_cli_rejects_unknown_task),
    ]
    print("=" * 60)
    print("Eval harness tests")
    print("=" * 60)
    passed = sum(1 for n, fn in tests if test(n, fn))
    print(f"\n{passed}/{len(tests)} passed\n")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main_run())

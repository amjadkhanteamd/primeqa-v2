"""Scoring rubric for eval-harness outputs.

Every task has a scoring function that takes (actual, expected, rubric)
and returns a list of CheckResult — each a named assertion with pass/
fail + optional note. We avoid a single numeric "score" because it hides
what broke; a list of pass/fail checks is easier to diff between runs.

Each task's scorer is picked from the `SCORERS` dict keyed by task name.
Unknown tasks get the structural-only scorer (just asserts non-empty).

Design:
  - NO LLM-as-judge yet. Pure structural checks. That's a future phase
    when we have golden outputs to compare against.
  - The rubric on the fixture can tweak thresholds (min_tests,
    min_coverage, required_objects) without code changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class CheckResult:
    name: str
    passed: bool
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "note": self.note}


# ---- Task-specific scorers ------------------------------------------------

def _score_test_plan_generation(
    actual: Dict[str, Any],
    expected: Dict[str, Any],
    rubric: Dict[str, Any],
) -> List[CheckResult]:
    """Assert the structure + coverage of a test plan without comparing
    text verbatim. Text comparison is the wrong bar — LLMs should vary
    wording freely.

    Rubric knobs (all optional):
      min_tests                 default 3
      max_tests                 default 8
      required_coverage_types   default ["positive"]
      allowed_objects           (if set) every target_object MUST be in this list
      forbidden_objects         (if set) NO target_object may be in this list
      max_confidence_floor      default 0.5 (mean confidence must be >= this)
    """
    checks: List[CheckResult] = []

    plan = (actual or {}).get("test_plan") or actual or {}
    tcs = plan.get("test_cases") or []

    min_tests = int(rubric.get("min_tests", expected.get("min_tests", 3)))
    max_tests = int(rubric.get("max_tests", expected.get("max_tests", 8)))
    required_cov = set(
        rubric.get("required_coverage_types")
        or expected.get("required_coverage_types", ["positive"])
    )
    allowed_objects = rubric.get("allowed_objects") or expected.get("allowed_objects")
    forbidden_objects = set(
        rubric.get("forbidden_objects") or expected.get("forbidden_objects", [])
    )
    min_conf = float(rubric.get("min_confidence", expected.get("min_confidence", 0.5)))

    # 1. Count
    checks.append(CheckResult(
        "count_in_range",
        min_tests <= len(tcs) <= max_tests,
        f"got {len(tcs)} (want {min_tests}..{max_tests})",
    ))

    # 2. Coverage
    actual_cov = {tc.get("coverage_type") for tc in tcs if tc.get("coverage_type")}
    missing = required_cov - actual_cov
    checks.append(CheckResult(
        "required_coverage_present",
        not missing,
        f"missing: {sorted(missing)}" if missing else "all present",
    ))

    # 3. Object usage
    refs = set()
    for tc in tcs:
        for step in tc.get("steps") or []:
            if step.get("target_object"):
                refs.add(step["target_object"])

    if allowed_objects is not None:
        bad = refs - set(allowed_objects)
        checks.append(CheckResult(
            "objects_in_allowed_list",
            not bad,
            f"unexpected: {sorted(bad)}" if bad else "all in list",
        ))
    if forbidden_objects:
        bad = refs & forbidden_objects
        checks.append(CheckResult(
            "no_forbidden_objects",
            not bad,
            f"used: {sorted(bad)}" if bad else "none used",
        ))

    # 4. Confidence floor
    confs = [float(tc.get("confidence_score", 0.0)) for tc in tcs]
    mean = sum(confs) / len(confs) if confs else 0.0
    checks.append(CheckResult(
        "mean_confidence_above_floor",
        mean >= min_conf,
        f"mean={mean:.2f}, floor={min_conf}",
    ))

    # 5. state_ref integrity — every $var referenced must have been set
    #    by a prior step via state_ref. This is the runtime fail-fast
    #    converted into a pre-flight check.
    #    Collect defined + referenced $vars per TC (independent), then
    #    assert no dangling references.
    all_dangling: List[str] = []
    var_pat = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
    for tc in tcs:
        defined = set()
        for step in tc.get("steps") or []:
            if step.get("state_ref"):
                defined.add(step["state_ref"])
            # Look at every string-valued field for $var references
            for k, v in step.items():
                if k == "state_ref":
                    continue
                for match in var_pat.findall(str(v)):
                    if match not in defined:
                        all_dangling.append(f"{tc.get('title','?')}/step {step.get('step_order','?')}: {match}")
    checks.append(CheckResult(
        "no_unresolved_state_refs",
        not all_dangling,
        ("; ".join(all_dangling[:3]) + (" ..." if len(all_dangling) > 3 else ""))
        if all_dangling else "all $vars resolved",
    ))

    return checks


def _score_structural_only(
    actual: Dict[str, Any],
    expected: Dict[str, Any],
    rubric: Dict[str, Any],
) -> List[CheckResult]:
    """Default fallback: just check the output is a non-empty dict.

    Useful for tasks we haven't yet written task-specific scoring for;
    at least we detect "the gateway returned null" kinds of failures.
    """
    return [CheckResult(
        "non_empty_output",
        bool(actual),
        "got empty" if not actual else "ok",
    )]


SCORERS: Dict[str, Callable[..., List[CheckResult]]] = {
    "test_plan_generation": _score_test_plan_generation,
}


def score(
    task: str,
    actual: Dict[str, Any],
    expected: Dict[str, Any],
    rubric: Optional[Dict[str, Any]] = None,
) -> List[CheckResult]:
    fn = SCORERS.get(task, _score_structural_only)
    return fn(actual, expected, rubric or {})

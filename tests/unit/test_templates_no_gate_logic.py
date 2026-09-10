"""Step 5 (LLD_STEP_5_QUALITY_POLICY §b): gate logic lives ONLY in the policy.
Grep-asserted over every template: no template compares a metric to a
number, reads a threshold key, or assigns an effect / recommendation word —
templates render the engine's words (styling on those words is presentation).
"""
from __future__ import annotations

import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "templates"
_METRICS = (r"pass_rate|min_pass_rate|max_flaky|max_run_age|block_on|failed|errored|blockers|warnings|"
            r"counted_runs|risk\.score|confidence|drift|non_current|ungraded|pending|applied")
# a metric compared to a number inside a Jinja statement or expression
_COMPARE = re.compile(r"\{[%{][^%}]*\b(" + _METRICS + r")\b[^%}]*?(<=|>=|<|>)\s*\d")
_COMPARE_REV = re.compile(r"\{[%{][^%}]*\d\s*(<=|>=|<|>)[^%}]*\b(" + _METRICS + r")\b")
# a threshold key read from the criteria object
_THRESHOLD = re.compile(r"decision_criteria[^%}]*(min_pass_rate|max_flaky|max_run_age|block_on|critical_tests)")
# an effect or recommendation word ASSIGNED in a template (derivation)
_ASSIGN = re.compile(r"\{%\s*set\s+\w+\s*=\s*['\"](BLOCK|REVIEW|CONDITIONAL|ALLOW|go|no_go|conditional_go|cannot_determine)['\"]")


# The decision surfaces: where a release's recommendation and effects render.
# The metric-vs-number check runs THERE; four pre-existing colour bands on
# pass-rate figures (dashboard.html:50/176/179, runs/s4_list.html:45 — a text
# colour, not an effect) are presentation on non-decision pages and stay.
DECISION_SURFACES = ("releases", "plans", "settings/quality_policy.html", "components/_readiness.html")


def _hits(pattern, only_decision_surfaces=False):
    out = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        rel = str(path.relative_to(TEMPLATES))
        if only_decision_surfaces and not rel.startswith(DECISION_SURFACES):
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                out.append(f"{path.relative_to(TEMPLATES)}:{i}: {line.strip()[:110]}")
    return out


def test_no_decision_surface_compares_a_metric_to_a_number():
    assert _hits(_COMPARE, True) == [] and _hits(_COMPARE_REV, True) == []


def test_the_only_metric_comparisons_anywhere_are_the_known_colour_bands():
    hits = _hits(_COMPARE) + _hits(_COMPARE_REV)
    assert sorted(h.split(":")[0] + ":" + h.split(":")[1] for h in hits) == sorted(
        ["dashboard.html:50", "dashboard.html:176", "dashboard.html:179", "runs/s4_list.html:45"])


def test_no_template_reads_a_criteria_threshold():
    assert _hits(_THRESHOLD) == []


def test_no_template_assigns_an_effect_or_recommendation_word():
    assert _hits(_ASSIGN) == []

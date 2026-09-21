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
# pass-rate figures (three on the dashboard's cards, one text colour in the
# runs list) are presentation on non-decision pages and stay.
DECISION_SURFACES = ("releases", "plans", "settings/quality_policy.html", "components/_readiness.html")

#: The known colour bands, keyed by FILE and counted — never by line number,
#: which any edit above them shifts (round 3: the AUD-007 empty state moved all
#: three dashboard bands and turned this gate red for a change it does not judge).
KNOWN_COLOUR_BANDS = {"dashboard.html": 3, "runs/s4_list.html": 1}


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
    import collections
    hits = _hits(_COMPARE) + _hits(_COMPARE_REV)
    by_file = collections.Counter(h.split(":")[0] for h in hits)
    assert dict(by_file) == KNOWN_COLOUR_BANDS, (
        "a metric-vs-number comparison appeared in a template, or a known colour "
        f"band was removed: {hits}")


def test_no_template_reads_a_criteria_threshold():
    assert _hits(_THRESHOLD) == []


def test_no_template_assigns_an_effect_or_recommendation_word():
    assert _hits(_ASSIGN) == []

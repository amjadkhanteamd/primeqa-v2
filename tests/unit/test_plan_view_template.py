"""Step 4 fix (the production 500 on the plan view): the plan template renders a
resolution that carries an ENVIRONMENT exclusion — the row shape that has no
``keys`` entry — without a database and without Flask (base.html stubbed).

Jinja's subscript on a dict falls back to attribute lookup when the key is
absent, so ``x['keys']`` on such a row yields the dict's own ``keys`` METHOD and
the join filter dies. The template reads ``.get`` now; this test is the guard.
"""
from __future__ import annotations

import pathlib

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "templates"

_STUB_BASE = "{% block title %}{% endblock %}{% block content %}{% endblock %}"


def _env():
    return Environment(loader=ChoiceLoader([DictLoader({"base.html": _STUB_BASE}),
                                            FileSystemLoader(str(TEMPLATES))]),
                       autoescape=True)


def _plan(*, executed=False):
    return {
        "id": "f52771fd-971f-4c3f-a507-53d110aa51dc", "scope_kind": "release", "scope_ref": "16",
        "planned_by": 1, "planned_by_name": "AK", "authorised_by": None, "authorised_by_name": None,
        "planned_at": "2026-09-09T12:18:04", "executed_by": (1 if executed else None),
        "executed_by_name": ("AK" if executed else None), "executed_at": ("2026-09-09T12:30:00" if executed else None),
        "execution": ({"s4_jobs": [{"job_id": 1, "attached": False}], "ui_jobs": [], "refusals": []} if executed else None),
        "inputs": {"targets_source": "fallback:evidence-active", "environment_id": None, "resolved_scope_kind": "release",
                   "schedule": None},
        "resolution": {
            "requirement_keys": ["SQ-205"], "claim_count": 1,
            "claims_by_kind": {"data_behavior / value-claim": {"archetype": "data_behavior", "claim_kind": "value-claim",
                               "lane": "functional", "claims": [{"test_id": "abcdef12-0000-0000-0000-000000000000", "version_seq": 1,
                               "keys": ["SQ-205"], "link_kinds": ["generated_from"], "origins": ["jira"], "unclassified": False,
                               "recipes": [{"recipe_id": "r1", "version_seq": 1, "recipe_kind": "data-recipe", "mode": "WRITE"}]}]}},
            "environments": [{"environment_id": 59, "name": "Prime QA NEW", "is_active": True, "is_production": False,
                              "execution_policy": "full", "admitted": True,
                              "pin": {"connected_org_id": "o", "org_version_seq": 253, "state": "CURRENT"}}],
            "personas": [], "declared_surfaces": [],
            "manifests": {"functional": {"pairs": [{"test_id": "abcdef12", "environment_id": 59, "recipes": ["r1"]}], "job_count": 1},
                          "conformance": []},
            "selection": {"impact": "not yet — every scoped item", "risk": "not yet — every scoped item",
                          "persona": "not yet — every declared persona", "regression": "not yet — every eligible recipe"},
        },
        "exclusions": {"items": [
            # the production shape: an ENVIRONMENT exclusion carries no `keys` entry
            {"kind": "environment", "ref": 78, "reason": "env_not_target",
             "sentence": "The environment holds evidence for this scope but is not a target.",
             "name": "Prod1", "is_active": False, "is_production": True, "execution_policy": "read_only"},
            {"kind": "claim", "ref": "71583230-3a10-44ae-9744-fe1eb38cbd85", "reason": "claim_deprecated",
             "sentence": "The claim is deprecated — it no longer applies.", "keys": ["SQ-205"]},
        ], "counts": {"env_not_target": 1, "claim_deprecated": 1}},
        "exclusion_lines": [{"reason": "env_not_target", "count": 1, "sentence": "…"},
                            {"reason": "claim_deprecated", "count": 1, "sentence": "…"}],
        "excluded_total": 2,
    }


def _render(plan):
    return _env().get_template("plans/detail.html").render(
        plan=plan, available=True, env_names={59: "Prime QA NEW", 78: "Prod1"},
        user={"role": "tester", "full_name": "AK", "email": "x@y"}, csrf_input="", active_page="releases")


def test_plan_view_renders_an_environment_exclusion_that_carries_no_keys():
    html = _render(_plan())
    assert 'data-testid="plan-exclusion" data-reason="env_not_target" data-ref="78"' in html
    assert "Prod1" in html and "inactive" in html and "production" in html and "read_only" in html
    assert 'data-reason="claim_deprecated"' in html and "SQ-205" in html       # the claim row still names its keys
    assert 'data-excluded-total="2"' in html
    assert 'data-testid="run-this-plan"' in html and "not yet run" in html


def test_plan_view_renders_the_executed_state():
    html = _render(_plan(executed=True))
    assert "already run" in html and 'data-executed="1"' in html


def test_plan_view_renders_with_no_exclusions_at_all():
    p = _plan(); p["exclusions"] = {"items": [], "counts": {}}; p["exclusion_lines"] = []; p["excluded_total"] = 0
    assert "Nothing excluded." in _render(p)

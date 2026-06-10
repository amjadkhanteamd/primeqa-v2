"""Unit: S8 repair suggestions (theme #6, D-201) — pure map over the closed S6
verdict + cause vocabulary. Every path returns human-gated, owner-routed
suggestions; passing verdicts return nothing."""
from __future__ import annotations

import pytest

from primeqa.evolution.repair import suggest_repairs

pytestmark = pytest.mark.unit

_PASSING = ("prohibition_enforced", "value_persisted",
            "asserted_metadata_present", "asserted_value_matches")
_FAILING_CAUSED = [
    ("prohibition_not_enforced", "vr_inactive", "org"),
    ("prohibition_not_enforced", "vr_formula_drift", "claim"),
    ("prohibition_not_enforced", "vr_formula_indeterminate", "recipe"),
    ("prohibition_not_enforced", "no_active_vr", "org"),
    ("prohibition_not_enforced", "enforcement_gap", "org"),
    ("rejected_unasserted_reason", "other_vr_fired", "claim"),
    ("rejected_unasserted_reason", "platform_constraint", "recipe"),
]


def test_passing_verdicts_have_nothing_to_repair():
    for v in _PASSING:
        assert suggest_repairs(v) == []
    assert suggest_repairs(None) == []


@pytest.mark.parametrize("verdict,cause,owner", _FAILING_CAUSED)
def test_every_caused_failure_maps_to_an_owner_routed_suggestion(verdict, cause, owner):
    out = suggest_repairs(verdict, cause_kind=cause, vr_name="My_VR")
    assert out, f"{verdict}/{cause} produced no suggestion"
    assert out[0]["owner"] == owner
    assert out[0]["human_gated"] is True
    assert out[0]["title"] and out[0]["detail"]


def test_vr_name_is_woven_into_the_suggestion():
    out = suggest_repairs("prohibition_not_enforced",
                          cause_kind="vr_inactive", vr_name="Block_Closed_Won")
    assert "Block_Closed_Won" in out[0]["title"] + out[0]["detail"]


def test_uncaused_failures_still_get_a_generic_suggestion():
    assert suggest_repairs("prohibition_not_enforced")          # no cause
    assert suggest_repairs("rejected_unasserted_reason")


def test_value_not_persisted_offers_both_org_and_claim_paths():
    out = suggest_repairs("value_not_persisted")
    assert {s["owner"] for s in out} == {"org", "claim"}


def test_inspection_drift_and_errored_paths():
    assert suggest_repairs("asserted_metadata_absent")[0]["owner"] == "org"
    assert suggest_repairs("asserted_value_differs")[0]["owner"] == "org"
    assert suggest_repairs("not_evaluated")[0]["owner"] == "ops"

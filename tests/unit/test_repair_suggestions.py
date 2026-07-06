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
    # D-229: positive-vertical failure causes
    ("value_not_persisted", "field_not_createable", "recipe"),
    ("value_not_persisted", "before_save_automation_overwrote", "org"),  # D-241
    ("automation_not_triggered", "automation_inactive", "org"),
    ("automation_not_triggered", "automation_effect_absent", "recipe"),
    ("state_not_transitioned", "automation_inactive", "org"),
    ("state_not_transitioned", "automation_effect_absent", "recipe"),
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


def test_value_not_persisted_field_not_createable_is_recipe_owned():
    # D-229 (review #6): the field-not-createable cause is NOT an automation
    # overwrite — it's a read-only field, a recipe/claim concern, not an org one.
    out = suggest_repairs("value_not_persisted", cause_kind="field_not_createable")
    assert out[0]["owner"] == "recipe"
    assert "read-only" in out[0]["detail"] or "not createable" in out[0]["detail"]


def test_value_not_persisted_before_save_is_org_owned():
    # D-241: a before-save Flow overwrote the posted value — an org behavior the
    # test correctly surfaced, NOT a recipe defect. Owner = org, names the Flow.
    out = suggest_repairs("value_not_persisted",
                          cause_kind="before_save_automation_overwrote")
    assert out[0]["owner"] == "org"
    assert "before-save Flow" in (out[0]["title"] + out[0]["detail"])


def test_automation_and_state_uncaused_still_get_a_suggestion():
    # D-229 (review #7): the positive automation/state failures must not return
    # an empty repair list even with no attributed cause.
    assert suggest_repairs("automation_not_triggered")[0]["owner"] == "org"
    assert suggest_repairs("state_not_transitioned")[0]["owner"] == "org"


def test_automation_inactive_is_org_owned_reactivate_flow():
    out = suggest_repairs("automation_not_triggered", cause_kind="automation_inactive")
    assert out[0]["owner"] == "org"
    assert "Flow" in out[0]["title"] + out[0]["detail"]


def test_inspection_drift_and_errored_paths():
    assert suggest_repairs("asserted_metadata_absent")[0]["owner"] == "org"
    assert suggest_repairs("asserted_value_differs")[0]["owner"] == "org"
    assert suggest_repairs("not_evaluated")[0]["owner"] == "ops"


# --- not_evaluated forks by failure_category (never suggest a futile re-run) --

def test_not_evaluated_setup_rejection_is_recipe_owned_no_rerun():
    # A deterministic org rejection of the setup data (AmbiguousRejection /
    # PaddingRejection / SetupRejected): the fix is the test's staged values or
    # the org rule naming its fields — a re-run fails identically.
    out = suggest_repairs("not_evaluated", failure_category="setup_rejection")
    assert [s["owner"] for s in out] == ["recipe", "org"]
    text = " ".join(s["title"] + " " + s["detail"] for s in out).lower()
    assert "fail the same way" in text
    assert "re-queue" not in text


def test_not_evaluated_normalization_is_recipe_owned_no_rerun():
    out = suggest_repairs("not_evaluated", failure_category="normalization")
    assert out[0]["owner"] == "recipe"
    assert "re-running as-is repeats it" in out[0]["detail"].lower()


def test_not_evaluated_indeterminate_categories_keep_ops_rerun_copy():
    # auth / transient / rate_limit / unknown / None (every pre-fork caller)
    # keep today's ops re-run suggestion verbatim.
    for cat in (None, "auth", "transient", "rate_limit", "unknown", "permission"):
        out = suggest_repairs("not_evaluated", failure_category=cat)
        assert out[0]["owner"] == "ops", cat
        assert "re-run" in out[0]["title"].lower(), cat

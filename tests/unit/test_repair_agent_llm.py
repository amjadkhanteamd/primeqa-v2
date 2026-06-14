"""Unit: D-236 — the auto-fix agent's LLM layer in repair_agent. The pure
core (recipe-owner cause precedence + the field-change applier) and the safety
guarantee that auto-apply is DORMANT unless the per-tenant flag is on."""
from __future__ import annotations

from unittest import mock

import pytest

from primeqa.intelligence import repair_agent as RA
from primeqa.intelligence.llm.prompts.repair_proposal import REMOVE_SENTINEL

pytestmark = pytest.mark.unit


# ---- proposal_for: recipe-owner cause takes precedence (D-236) -------------

def test_recipe_owner_cause_maps_to_recipe_edit():
    # a recipe-owner cause wins over the verdict's finding/regenerate default
    assert RA.proposal_for("value_not_persisted", "field_not_createable",
                           "failed") == "recipe_edit"
    assert RA.proposal_for("rejected_unasserted_reason", "platform_constraint",
                           "failed") == "recipe_edit"
    assert RA.proposal_for("automation_not_triggered", "automation_effect_absent",
                           "failed") == "recipe_edit"


def test_spine_mapping_unchanged_without_a_recipe_owner_cause():
    assert RA.proposal_for("value_not_persisted", None, "failed") is None      # finding
    assert RA.proposal_for(None, "vr_formula_drift", "failed") == \
        "regenerate_from_current_org"
    assert RA.proposal_for("rejected_unasserted_reason", None, "failed") == \
        "regenerate_from_current_org"
    assert RA.proposal_for(None, None, "errored") == "rerun"
    assert RA.proposal_for("prohibition_not_enforced", None, "failed") is None  # finding


# ---- apply_field_changes: pure merge onto a qualified payload --------------

def test_apply_set_uses_qualified_key_when_field_is_new():
    out = RA.apply_field_changes({"Opportunity.Amount": "5"}, "Opportunity",
                                 {"StageName": "Prospecting"})
    assert out["Opportunity.StageName"] == "Prospecting"
    assert out["Opportunity.Amount"] == "5"           # untouched


def test_apply_set_keeps_existing_bare_key_form():
    out = RA.apply_field_changes({"Amount": "5"}, "Opportunity", {"Amount": "9"})
    assert out == {"Amount": "9"}                     # kept the existing bare key


def test_apply_remove_drops_either_key_form():
    out = RA.apply_field_changes(
        {"Opportunity.CreatedById": "x", "Name": "n"}, "Opportunity",
        {"CreatedById": REMOVE_SENTINEL, "Name": REMOVE_SENTINEL})
    assert out == {}                                  # both forms dropped


def test_apply_does_not_mutate_input():
    src = {"Opportunity.Amount": "5"}
    RA.apply_field_changes(src, "Opportunity", {"Amount": REMOVE_SENTINEL})
    assert src == {"Opportunity.Amount": "5"}         # input preserved


# ---- auto_apply is DORMANT unless the flag is on (the safety guarantee) ----

def test_auto_apply_is_dormant_when_flag_off():
    with mock.patch.object(RA, "_repair_settings", return_value={
            "auto_apply": False, "agent_enabled": True,
            "threshold_high": 0.85, "max_attempts": 3}), \
         mock.patch("primeqa.semantic.connection.get_tenant_connection") as gc:
        out = RA.auto_apply_proposals(7)
    assert out == {"applied": 0, "skipped": 0}
    gc.assert_not_called()                            # never even opened a connection


def test_auto_apply_runs_when_flag_on():
    # flag on but no proposals → opens a connection, applies nothing
    fake_conn = mock.MagicMock()
    fake_conn.execute.return_value.mappings.return_value.all.return_value = []
    cm = mock.MagicMock()
    cm.__enter__.return_value = fake_conn
    with mock.patch.object(RA, "_repair_settings", return_value={
            "auto_apply": True, "agent_enabled": True,
            "threshold_high": 0.85, "max_attempts": 3}), \
         mock.patch("primeqa.semantic.connection.get_tenant_connection",
                    return_value=cm):
        out = RA.auto_apply_proposals(7)
    assert out == {"applied": 0, "skipped": 0}

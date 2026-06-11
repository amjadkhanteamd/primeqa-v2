"""Unit: the D-215.1 repair-agent triage map (pure, no DB)."""
from __future__ import annotations

from primeqa.intelligence.repair_agent import proposal_for


def test_drift_shapes_regenerate():
    assert proposal_for("prohibition_enforced", "vr_formula_drift", "passed") \
        == "regenerate_from_current_org"
    assert proposal_for("rejected_unasserted_reason", "other_vr_fired", "failed") \
        == "regenerate_from_current_org"
    assert proposal_for("prohibition_enforced", "no_active_vr", "failed") \
        == "regenerate_from_current_org"
    assert proposal_for(None, "vr_formula_indeterminate", "failed") \
        == "regenerate_from_current_org"


def test_infrastructure_shapes_rerun():
    assert proposal_for("not_evaluated", None, "errored") == "rerun"
    assert proposal_for(None, None, "errored") == "rerun"


def test_findings_never_get_proposals():
    for verdict in ("prohibition_not_enforced", "value_not_persisted",
                    "state_not_transitioned", "automation_not_triggered",
                    "asserted_metadata_absent", "asserted_value_differs"):
        assert proposal_for(verdict, None, "failed") is None, verdict
    # a finding with a drift-ish cause STILL never repairs (finding wins)
    assert proposal_for("prohibition_not_enforced", "vr_formula_drift",
                        "failed") is None


def test_unmapped_shapes_propose_nothing():
    assert proposal_for("value_persisted", None, "passed") is None
    assert proposal_for("something_new", None, "failed") is None

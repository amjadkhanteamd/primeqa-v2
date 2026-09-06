"""Step A.1 — apply → re-verify at root, the pure parts (LLD_STEP_A1_REVERIFY).

* the apply refusal gains two recorded reasons: ``claim_deprecated``
  (every kind) and ``recipe_moved`` (recipe edits only);
* the settle table: a completed job with a run → ran; completed without
  a run → no_run / no_eligible_recipe (the silence made loud); failed or
  cancelled → no_run with the job's error code; queued/claimed/running
  → wait;
* the autonomous pass PRE-APPROVES and never writes a version;
* the human decide threads ``decided_by`` into the apply (the approval act).
"""
from __future__ import annotations

from unittest import mock

from primeqa.intelligence import repair_agent as RA

_ON = {"auto_apply": False, "agent_enabled": True,
       "gate_apply_enabled": True, "max_attempts": 3}


def _row(kind="recipe_edit", verdict="DERIVED"):
    return {"id": 9, "run_id": "r", "claim_test_id": "c", "environment_id": 59,
            "proposal_kind": kind, "status": "proposed",
            "gate_verdict": verdict, "grounding_source": {"rule": "R1"}}


# ---- the refusal table --------------------------------------------------

def test_deprecated_claim_refuses_every_kind():
    for kind in ("recipe_edit", "rerun", "regenerate_from_current_org"):
        why = RA._apply_refusal(_ON, _row(kind), claim_status="deprecated")
        assert why and why.startswith("claim_deprecated")


def test_live_claim_and_unmoved_recipe_is_applicable():
    assert RA._apply_refusal(_ON, _row(), claim_status="approved",
                             recipe_moved=False) is None


def test_unknown_claim_status_does_not_refuse_on_that_ground():
    assert RA._apply_refusal(_ON, _row(), claim_status=None) is None


def test_recipe_moved_refuses_recipe_edits_only():
    why = RA._apply_refusal(_ON, _row("recipe_edit"), claim_status="approved",
                            recipe_moved=True)
    assert why and why.startswith("recipe_moved")
    assert RA._apply_refusal(_ON, _row("rerun"), claim_status="approved",
                             recipe_moved=True) is None


def test_step_a_refusals_still_come_first():
    off = {**_ON, "gate_apply_enabled": False}
    assert "dormant" in RA._apply_refusal(off, _row(), claim_status="deprecated")
    assert "SPECULATIVE" in RA._apply_refusal(_ON, _row(verdict="SPECULATIVE"),
                                              claim_status="deprecated")


# ---- the settle table ---------------------------------------------------

def test_settle_waits_while_the_job_is_not_terminal():
    for st in (None, "queued", "claimed", "running"):
        assert RA.settle_transition(st, None, None) is None


def test_settle_completed_with_a_run_records_the_run():
    tr = RA.settle_transition("completed", None, {
        "run_id": "run-1", "outcome": "failed", "verdict": "creation_rejected"})
    assert tr == {"reverify_state": "ran", "reverify_run_id": "run-1",
                  "reverify_outcome": "failed",
                  "reverify_verdict": "creation_rejected",
                  "reverify_refusal": None}


def test_settle_completed_without_a_run_is_the_loud_silence():
    tr = RA.settle_transition("completed", None, None)
    assert tr["reverify_state"] == "no_run"
    assert tr["reverify_refusal"] == "no_eligible_recipe"
    assert tr["reverify_run_id"] is None


def test_settle_failed_or_cancelled_records_the_job_error():
    assert RA.settle_transition("failed", "sf_error", None)["reverify_refusal"] == "sf_error"
    assert RA.settle_transition("cancelled", None, None)["reverify_refusal"] == "cancelled"
    assert RA.settle_transition("failed", "stale_timeout", None)["reverify_state"] == "no_run"


# ---- the autonomous pass pre-approves, never writes -----------------------

def _conn_with(rows):
    conn = mock.MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = rows
    conn.execute.return_value.scalar.return_value = "repair_proposals"  # provisioned
    cm = mock.MagicMock(); cm.__enter__.return_value = conn
    return cm, conn


def test_auto_pass_marks_a_derived_row_approved_and_writes_no_version():
    cm, conn = _conn_with([_row()])
    with mock.patch.object(RA, "_repair_settings", return_value={**_ON, "auto_apply": True}), \
         mock.patch("primeqa.semantic.connection.get_tenant_connection", return_value=cm), \
         mock.patch.object(RA, "_applicability", return_value={"claim_status": "approved",
                                                                "recipe_moved": False}), \
         mock.patch.object(RA, "_env_is_production", return_value=False), \
         mock.patch.object(RA, "_recipe_edit_attempts", return_value=0), \
         mock.patch.object(RA, "_audit") as audit, \
         mock.patch.object(RA, "_apply") as apply:
        out = RA.auto_apply_proposals(7)
    assert out == {"applied": 1, "skipped": 0}
    apply.assert_not_called()                                   # never a write
    sqls = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert any("status = 'approved'" in q for q in sqls)
    assert not any("status = 'applied'" in q for q in sqls)
    assert audit.call_args.args[1] == "ui.repair_auto_approved"


def test_auto_pass_skips_a_deprecated_claim():
    cm, conn = _conn_with([_row()])
    with mock.patch.object(RA, "_repair_settings", return_value={**_ON, "auto_apply": True}), \
         mock.patch("primeqa.semantic.connection.get_tenant_connection", return_value=cm), \
         mock.patch.object(RA, "_applicability", return_value={"claim_status": "deprecated"}), \
         mock.patch.object(RA, "_apply") as apply:
        out = RA.auto_apply_proposals(7)
    assert out == {"applied": 0, "skipped": 1}
    apply.assert_not_called()


# ---- the human decide threads the approval act ---------------------------

def test_decide_threads_decided_by_into_the_apply_and_refuses_deprecated():
    row = {**_row(), "status": "proposed"}
    conn = mock.MagicMock()
    conn.execute.return_value.mappings.return_value.first.return_value = row
    cm = mock.MagicMock(); cm.__enter__.return_value = conn
    with mock.patch("primeqa.semantic.connection.get_tenant_connection", return_value=cm), \
         mock.patch.object(RA, "_repair_settings", return_value=_ON), \
         mock.patch.object(RA, "_applicability", return_value={"claim_status": "approved",
                                                                "recipe_moved": False}), \
         mock.patch.object(RA, "_apply", return_value={"action": "recipe_edit",
                                                       "s4_job_id": 5, "reverify_job_id": 5,
                                                       "applied_recipe_version_seq": 2}) as apply, \
         mock.patch.object(RA, "_stamp") as stamp:
        res = RA.decide_proposal(7, 9, approve=True, decided_by=42)
    assert res["ok"] is True and res["status"] == "applied"
    assert apply.call_args.kwargs["decided_by"] == 42
    assert stamp.call_args.args[2] == "applied"
    with mock.patch("primeqa.semantic.connection.get_tenant_connection", return_value=cm), \
         mock.patch.object(RA, "_repair_settings", return_value=_ON), \
         mock.patch.object(RA, "_applicability", return_value={"claim_status": "deprecated"}), \
         mock.patch.object(RA, "_apply") as apply2:
        res = RA.decide_proposal(7, 9, approve=True, decided_by=42)
    assert res["ok"] is False and res["refused"] is True
    assert res["error"].startswith("claim_deprecated") and res["claim_status"] == "deprecated"
    apply2.assert_not_called()
